#!/usr/bin/env python3
"""
build_opportunity_base.py

Join everything already computed about each paper-bot opportunity into one
compact table, so the CF reachability analysis can iterate in seconds instead
of re-joining four files every run.

No GCS. No archive scan. Everything here is already on disk.

THE SPINE IS THE REPRICE OUTPUT
-------------------------------
Three files describe the same opportunities and none of them agree on row
count:

    settled_*.csv                  the bot's own log
    paper_bot_reprice_v1_2_rows    settled + reconstructed ask + execution status
    trades_*.csv                   carries STC, which settled does NOT

The reprice rows are the spine because they already carry the reconstructed
executable ask, the marketable flag, the displayed capacity and the execution
status -- everything the settled log lacks.  STC is left-joined from trades on
(ticker, opp, t_entry), a key proven to match 100% of 47,268 rows.

EVERY SPINE ROW SURVIVES
------------------------
A left join that silently drops rows is how a coverage gap becomes a selection
bias nobody notices.  So each output row carries an explicit `stc_status`, and
the totals reconcile: rows in equals rows out.

THE SEALED HOLDOUT
------------------
2026-09-07 through 2026-09-13 is the chronological holdout.  Opportunities in
that range are excluded and counted, not silently dropped, and the exclusion is
reported.  Pass --allow-sealed only when the holdout is deliberately being
spent, which is once, at the end.

USAGE
    python3 build_opportunity_base.py --self-test
    python3 build_opportunity_base.py \
        --reprice ~/paper_bot_reprice_v1_2_rows.csv \
        --trades  ~/logs/paper_bot_logs_v2/trades_2026-07-15.csv \
        --out     ~/paper_bot_opportunity_base.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import math
import os
import sys
import tempfile
from pathlib import Path

SEALED_FROM = dt.date(2026, 9, 7)
SEALED_TO = dt.date(2026, 9, 13)

# STC join tolerance. The two files are written by the same process at the same
# instant, so an exact match is expected; the tolerance exists to absorb float
# formatting differences, not genuine timing drift.
STC_JOIN_TOL_S = 0.001


def num(v):
    if v in (None, ""):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def as_date(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).date()


def is_sealed(d):
    return SEALED_FROM <= d <= SEALED_TO


def exact_order_fee(price, contracts):
    """Kalshi fee, ceilinged to the next whole cent on the WHOLE ORDER."""
    return math.ceil(0.07 * contracts * price * (1.0 - price) * 100 - 1e-9) / 100.0


def scalable_fee(price, contracts):
    return 0.07 * contracts * price * (1.0 - price)


def load_stc(path, stats):
    """(ticker, side) -> sorted [(t, stc, session, dow, hour)] from the trades log.

    settled does not carry stc. trades does. The join key was verified at 100%
    on 47,268 rows.
    """
    idx = collections.defaultdict(list)
    for r in csv.DictReader(open(path, newline="")):
        t = num(r.get("t"))
        if t is None:
            stats["trades_rows_without_t"] += 1
            continue
        stats["trades_rows"] += 1
        idx[(r.get("ticker", ""), r.get("opp", ""))].append(
            (t, r.get("stc", ""), r.get("session", ""), r.get("dow", ""),
             r.get("hour", "")))
    for k in idx:
        idx[k].sort()
    return idx


def lookup_stc(idx, ticker, side, t):
    """Nearest trades row at the same instant. Returns (rec, status)."""
    cands = idx.get((ticker, side))
    if not cands:
        return None, "NO_TRADES_ROW"
    best = min(cands, key=lambda x: abs(x[0] - t))
    if abs(best[0] - t) > STC_JOIN_TOL_S:
        return best, "TIME_MISMATCH"
    return best, "OK"


def build(a):
    stats = collections.Counter()
    stc_idx = load_stc(os.path.expanduser(a.trades), stats)
    print(f"trades rows indexed   {stats['trades_rows']:,}")

    out_rows = []
    for line_no, r in enumerate(csv.DictReader(
            open(os.path.expanduser(a.reprice), newline="")), start=2):
        stats["reprice_rows"] += 1
        t = num(r.get("t_entry")) or num(r.get("t"))
        if t is None:
            stats["EXCLUDED_NO_DECISION_TIME"] += 1
            continue
        d = as_date(t)
        if is_sealed(d) and not a.allow_sealed:
            stats["EXCLUDED_SEALED_HOLDOUT"] += 1
            continue

        cost = num(r.get("cost"))
        size = num(r.get("size")) or 1.0
        win = num(r.get("win"))
        # cost must lie in the OPEN interval. A cost of exactly 0 or 1 is not a
        # quote -- 1.0 on a NO means the YES side had no bid at all -- and the
        # fee and PnL arithmetic is degenerate there. Same check GPT added to
        # the reprice tool after the same defect appeared in it.
        if (cost is None or not (0.0 < cost < 1.0)
                or size is None or size <= 0
                or win not in (0.0, 1.0)):
            stats["EXCLUDED_INVALID_ECONOMICS"] += 1
            continue

        rec, status = lookup_stc(stc_idx, r.get("ticker", ""), r.get("opp", ""), t)
        stats["stc_" + status] += 1

        ask = num(r.get("primary_repriced_ask"))
        cap = num(r.get("arrival_capacity_at_touch"))
        stc = num(rec[1]) if rec else None

        row = {
            # identity
            "t_entry": repr(t),
            "date_utc": d.isoformat(),
            "ticker": r.get("ticker", ""),
            "coin": r.get("sib") or r.get("coin", ""),
            "side": r.get("opp") or r.get("purchased_side", ""),
            "burst_id": r.get("burst_id", ""),
            "source_reprice_line": line_no,

            # what the bot logged
            "logged_cost": repr(cost),
            "size": repr(size),
            "win": int(win),

            # what the book showed
            "reconstructed_ask": "" if ask is None else repr(ask),
            "marketable_at_limit": r.get("primary_marketable_at_logged_limit", ""),
            "reprice_eligible": r.get("primary_reprice_eligible", ""),
            "reprice_direction": r.get("primary_reprice_direction", ""),
            "capacity_at_touch": "" if cap is None else repr(cap),
            "execution_support": r.get("conservative_execution_evidence", ""),

            # economics at the LOGGED price, both fee models
            "pnl_logged_scalable": repr((win - cost) * size - scalable_fee(cost, size)),
            "pnl_logged_exact": repr((win - cost) * size - exact_order_fee(cost, size)),

            # economics at the DISPLAYED ask, both fee models
            "pnl_ask_scalable": ("" if ask is None else
                                 repr((win - ask) * size - scalable_fee(ask, size))),
            "pnl_ask_exact": ("" if ask is None else
                              repr((win - ask) * size - exact_order_fee(ask, size))),

            # clock -- STC is the field the reachability analysis needs most
            "stc": "" if stc is None else repr(stc),
            "stc_status": status,
            "close_ts": "" if stc is None else repr(t + stc),
            "session": rec[2] if rec else "",
            "dow": rec[3] if rec else (r.get("decision_dow_utc", "")),
            "hour": rec[4] if rec else (r.get("decision_hour_utc", "")),

            # confirmation signals, carried RAW and in their native units.
            #
            # cb_confirm is a DOLLAR difference in the Coinbase price over the
            # prior 15s, so 0.02 means 0.003 bps on BTC and ~1000 bps on DOGE.
            # It is NOT comparable across coins and must be normalised before
            # use. That normalisation needs a denominator -- the strike -- and
            # so belongs in the reachability step, not here.
            #
            # pm_confirm has no such defect: it is a Polymarket probability
            # change on a 0-1 scale, so it means the same thing on every coin.
            "cb_confirm_raw_usd": "" if num(r.get("cb_confirm")) is None
                                  else repr(num(r.get("cb_confirm"))),
            "pm_confirm_probability": "" if num(r.get("pm_confirm")) is None
                                      else repr(num(r.get("pm_confirm"))),
            # A blank confirmation is MISSING, not a failure to confirm.
            # Counting blanks as "did not confirm" would load every stale or
            # absent observation onto one side of any split.
            "cb_confirm_status": ("MISSING" if num(r.get("cb_confirm")) is None
                                  else "PRESENT"),
            "pm_confirm_status": ("MISSING" if num(r.get("pm_confirm")) is None
                                  else "PRESENT"),
            "feed_stale": r.get("feed_stale", ""),
        }
        out_rows.append(row)

    out = Path(os.path.expanduser(a.out))
    out.parent.mkdir(parents=True, exist_ok=True)
    if out_rows:
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out_rows[0]))
            w.writeheader()
            w.writerows(out_rows)

    lines = []
    def P(s=""):
        print(s)
        lines.append(s)

    kept = len(out_rows)
    excluded = (stats["EXCLUDED_SEALED_HOLDOUT"] + stats["EXCLUDED_NO_DECISION_TIME"]
                + stats["EXCLUDED_INVALID_ECONOMICS"])
    P()
    P("=" * 66)
    P("OPPORTUNITY BASE TABLE")
    P("=" * 66)
    P()
    P(f"  reprice rows in            {stats['reprice_rows']:,}")
    P(f"  rows written               {kept:,}")
    P(f"  excluded                   {excluded:,}")
    P(f"    sealed holdout           {stats['EXCLUDED_SEALED_HOLDOUT']:,}")
    P(f"    no decision time         {stats['EXCLUDED_NO_DECISION_TIME']:,}")
    P(f"    invalid economics        {stats['EXCLUDED_INVALID_ECONOMICS']:,}")
    P(f"  reconciles                 "
      f"{'YES' if kept + excluded == stats['reprice_rows'] else 'NO -- INVESTIGATE'}")
    P()
    P("  STC join:")
    for k in sorted(k for k in stats if k.startswith("stc_")):
        P(f"    {k[4:]:22s} {stats[k]:7,}  {100*stats[k]/max(kept,1):5.1f}%")
    P()
    if out_rows:
        ds = sorted(r["date_utc"] for r in out_rows)
        P(f"  date range                 {ds[0]} .. {ds[-1]}")
        g = collections.Counter(r["coin"] for r in out_rows)
        P("  by coin: " + "  ".join(f"{k}={v:,}" for k, v in sorted(g.items())))
        nostc = sum(1 for r in out_rows if r["stc"] == "")
        P(f"  rows with no STC           {nostc:,}  "
          f"({100*nostc/max(kept,1):.1f}%)  -- these cannot enter a")
        P("                             time-to-close analysis and must be")
        P("                             reported, never silently dropped")
    P()
    P(f"  wrote {out}")
    Path(str(out) + ".summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if kept + excluded == stats["reprice_rows"] else 1


# ------------------------------------------------------------------ self-test
def self_test():
    fails = []

    def ck(n, c):
        print(f"  {n:60s} {'PASS' if c else 'FAIL'}")
        if not c:
            fails.append(n)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        T = lambda s: dt.datetime.fromisoformat(s).timestamp()

        rp = root / "reprice.csv"
        rows = [
            # ordinary row, ask above cost
            dict(t_entry=repr(T("2026-08-01T14:00:00+00:00")), ticker="KXBTC15M-A",
                 sib="BTC", opp="YES", cost="0.40", size="1", win="1",
                 burst_id="b1", primary_repriced_ask="0.45",
                 primary_marketable_at_logged_limit="0", primary_reprice_eligible="1",
                 primary_reprice_direction="PAY_UP", arrival_capacity_at_touch="50",
                 conservative_execution_evidence="1",
                 cb_confirm="0.0300", pm_confirm="0.0400", feed_stale="0"),
            # no reconstructed ask at all
            dict(t_entry=repr(T("2026-08-01T14:05:00+00:00")), ticker="KXBTC15M-B",
                 sib="BTC", opp="NO", cost="0.60", size="1", win="0",
                 burst_id="b2", primary_repriced_ask="",
                 primary_marketable_at_logged_limit="", primary_reprice_eligible="0",
                 primary_reprice_direction="INELIGIBLE", arrival_capacity_at_touch="",
                 conservative_execution_evidence="0",
                 cb_confirm="", pm_confirm="", feed_stale="1"),
            # no matching trades row -> no STC
            dict(t_entry=repr(T("2026-08-01T14:10:00+00:00")), ticker="KXSOL15M-C",
                 sib="SOL", opp="YES", cost="0.50", size="1", win="1",
                 burst_id="b3", primary_repriced_ask="0.50",
                 primary_marketable_at_logged_limit="1", primary_reprice_eligible="1",
                 primary_reprice_direction="UNCHANGED", arrival_capacity_at_touch="7",
                 conservative_execution_evidence="1"),
            # INSIDE the sealed holdout
            dict(t_entry=repr(T("2026-09-09T14:00:00+00:00")), ticker="KXBTC15M-S",
                 sib="BTC", opp="YES", cost="0.50", size="1", win="1",
                 burst_id="b4", primary_repriced_ask="0.50",
                 primary_marketable_at_logged_limit="1", primary_reprice_eligible="1",
                 primary_reprice_direction="UNCHANGED", arrival_capacity_at_touch="9",
                 conservative_execution_evidence="1"),
            # cost outside (0,1) -> invalid economics
            dict(t_entry=repr(T("2026-08-02T14:00:00+00:00")), ticker="KXBTC15M-D",
                 sib="BTC", opp="NO", cost="1.0", size="1", win="1",
                 burst_id="b5", primary_repriced_ask="0.50",
                 primary_marketable_at_logged_limit="1", primary_reprice_eligible="1",
                 primary_reprice_direction="UNCHANGED", arrival_capacity_at_touch="9",
                 conservative_execution_evidence="1"),
        ]
        with rp.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)

        tr = root / "trades.csv"
        trows = [
            dict(t=repr(T("2026-08-01T14:00:00+00:00")), ticker="KXBTC15M-A",
                 opp="YES", stc="300.5", session="US", dow="Fri", hour="14"),
            dict(t=repr(T("2026-08-01T14:05:00+00:00")), ticker="KXBTC15M-B",
                 opp="NO", stc="120.0", session="US", dow="Fri", hour="14"),
            dict(t=repr(T("2026-09-09T14:00:00+00:00")), ticker="KXBTC15M-S",
                 opp="YES", stc="200.0", session="US", dow="Wed", hour="14"),
            dict(t=repr(T("2026-08-02T14:00:00+00:00")), ticker="KXBTC15M-D",
                 opp="NO", stc="200.0", session="US", dow="Sat", hour="14"),
        ]
        with tr.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(trows[0]))
            w.writeheader()
            w.writerows(trows)

        out = root / "base.csv"
        a = argparse.Namespace(reprice=str(rp), trades=str(tr), out=str(out),
                               allow_sealed=False)
        rc = build(a)
        ck("run reconciles rows in == rows out + excluded", rc == 0)

        got = list(csv.DictReader(out.open()))
        by = {r["ticker"]: r for r in got}
        ck("sealed-holdout row excluded", "KXBTC15M-S" not in by)
        ck("invalid economics row excluded", "KXBTC15M-D" not in by)
        ck("three rows written", len(got) == 3)
        ck("STC joined where trades row exists", by["KXBTC15M-A"]["stc"] == "300.5")
        ck("close_ts derived from t + stc",
           abs(float(by["KXBTC15M-A"]["close_ts"])
               - (float(by["KXBTC15M-A"]["t_entry"]) + 300.5)) < 1e-6)
        ck("missing trades row -> NO_TRADES_ROW, row survives",
           by["KXSOL15M-C"]["stc_status"] == "NO_TRADES_ROW"
           and by["KXSOL15M-C"]["stc"] == "")
        ck("row with no reconstructed ask survives, ask PnL blank",
           by["KXBTC15M-B"]["reconstructed_ask"] == ""
           and by["KXBTC15M-B"]["pnl_ask_scalable"] == "")
        # win at 0.40, size 1: scalable fee 0.07*.4*.6 = 0.0168 -> 0.5832
        ck("logged scalable PnL correct",
           abs(float(by["KXBTC15M-A"]["pnl_logged_scalable"]) - (0.6 - 0.0168)) < 1e-9)
        # exact fee ceilings 0.0168 -> 0.02
        ck("logged exact PnL uses the cent ceiling",
           abs(float(by["KXBTC15M-A"]["pnl_logged_exact"]) - (0.6 - 0.02)) < 1e-9)
        ck("ask PnL is worse than logged when ask > cost",
           float(by["KXBTC15M-A"]["pnl_ask_scalable"])
           < float(by["KXBTC15M-A"]["pnl_logged_scalable"]))
        ck("capacity carried through", by["KXBTC15M-A"]["capacity_at_touch"] == "50.0")
        ck("cb_confirm carried RAW in usd",
           by["KXBTC15M-A"]["cb_confirm_raw_usd"] == "0.03")
        ck("pm_confirm carried as a probability",
           by["KXBTC15M-A"]["pm_confirm_probability"] == "0.04")
        ck("present confirmations flagged PRESENT",
           by["KXBTC15M-A"]["cb_confirm_status"] == "PRESENT"
           and by["KXBTC15M-A"]["pm_confirm_status"] == "PRESENT")
        ck("BLANK confirmation is MISSING, not a failure to confirm",
           by["KXBTC15M-B"]["pm_confirm_status"] == "MISSING"
           and by["KXBTC15M-B"]["pm_confirm_probability"] == "")
        ck("feed_stale carried through", by["KXBTC15M-B"]["feed_stale"] == "1")

        # deliberately spending the holdout must include it
        out2 = root / "base2.csv"
        a2 = argparse.Namespace(reprice=str(rp), trades=str(tr), out=str(out2),
                                allow_sealed=True)
        build(a2)
        got2 = [r["ticker"] for r in csv.DictReader(out2.open())]
        ck("--allow-sealed includes the holdout", "KXBTC15M-S" in got2)

    print("\n" + ("ALL PASS" if not fails else "FAILURES: " + ", ".join(fails)))
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reprice", default="~/paper_bot_reprice_v1_2_rows.csv")
    ap.add_argument("--trades", default="~/logs/paper_bot_logs_v2/trades_2026-07-15.csv")
    ap.add_argument("--out", default="~/paper_bot_opportunity_base.csv")
    ap.add_argument("--allow-sealed", action="store_true",
                    help="include 2026-09-07..09-13. Only when spending the holdout.")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    return build(a)


if __name__ == "__main__":
    raise SystemExit(main())
