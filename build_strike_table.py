#!/usr/bin/env python3
"""
build_strike_table.py

Step one of the CF reachability work: build one authoritative table of
per-market strikes and comparators, so the analysis that follows can run in
seconds instead of re-reading the archive every time.

WHY A TABLE FIRST
-----------------
The strike K is not a column.  The flattened `floor_strike` field in
kalshi_snapshots is blank on 100% of rows.  The authoritative value is nested
inside `raw_json` on lifecycle events:

    {"type":"market_lifecycle_v2",
     "msg":{"event_type":"metadata_updated",
            "market_ticker":"KXBTC15M-26SEP061000-00",
            "strike_type":"greater_or_equal",
            "floor_strike":79800.71,
            "custom_strike":{"round_digits":"2"}}}

Reading that out of ~1,300 lifecycle files takes half an hour.  Doing it once
and caching the result means the reachability analysis can be rewritten and
rerun freely, which it will need to be.

THE CAUSAL QUESTION THIS TABLE EXISTS TO ANSWER
-----------------------------------------------
Kalshi knows K before the market opens.  We know it when the event ARRIVES.
Those are different times, and the difference decides whether a rule is
implementable or only backtestable.  So every event keeps its receive
timestamp and the table supports two joins:

    strike_asof   latest event with recv_ts <= decision time.  Causal.
                  This is what a live bot could actually have used.

    strike_any    any event for that ticker, regardless of arrival.
                  Strictly better information than we had. Backtest only.

If the two give the same coverage, the causal constraint costs nothing.  If
strike_asof covers materially fewer trades, that is a finding about real-time
knowability, not a data problem to paper over.

WHAT IT REFUSES TO DO
---------------------
It will not pick between conflicting strikes for the same ticker and instant.
It will not fill a missing strike.  It will not read the sealed holdout.
Every market in the opportunity universe appears in the coverage report,
including the ones with no strike at all -- a coverage gap that becomes a
silent exclusion is the failure mode that has cost this project the most.

USAGE
    python3 build_strike_table.py --self-test
    python3 build_strike_table.py --probe --from 2026-07-15 --to 2026-09-06
    python3 build_strike_table.py --from 2026-07-15 --to 2026-09-06 \
        --out ~/strike_table --settled ~/logs/paper_bot_logs_v2/settled_2026-07-15.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

BUCKET = "gs://kalshi-data-vault-kalshi-collector-personal"
COINS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE")

# The chronological holdout. Nothing in this range may be read during
# development. Enforced, not merely documented.
SEALED_FROM = dt.date(2026, 9, 7)
SEALED_TO = dt.date(2026, 9, 13)

KNOWN_COMPARATORS = {"greater_or_equal", "greater", "less_or_equal", "less"}


# --------------------------------------------------------------- storage glue
class Store:
    """Read-only GCS access. Overridden in --self-test by a local fake."""

    def ls(self, uri: str) -> list[str]:
        p = subprocess.run(["gsutil", "ls", uri], capture_output=True, timeout=300)
        if p.returncode:
            return []
        return [x.strip() for x in p.stdout.decode("utf-8", "replace").splitlines()
                if x.strip().startswith("gs://")]

    def lines(self, uri: str):
        p = subprocess.Popen(["gsutil", "cat", uri], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
        gz = gzip.GzipFile(fileobj=p.stdout, mode="rb")
        try:
            yield from io.TextIOWrapper(gz, encoding="utf-8", errors="replace",
                                        newline="")
        finally:
            for close in (gz.close, p.stdout.close):
                try:
                    close()
                except Exception:                                # noqa: BLE001
                    pass
            if p.poll() is None:
                try:
                    p.terminate()
                except Exception:                                # noqa: BLE001
                    pass


class FakeStore:
    """Local directory tree standing in for the bucket, for --self-test."""

    def __init__(self, root: Path):
        self.root = root

    def _p(self, uri: str) -> Path:
        return self.root / uri.replace(BUCKET + "/", "").rstrip("/")

    def ls(self, uri: str) -> list[str]:
        p = self._p(uri)
        if not p.exists():
            return []
        out = []
        for c in sorted(p.iterdir()):
            out.append(uri.rstrip("/") + "/" + c.name + ("/" if c.is_dir() else ""))
        return out

    def lines(self, uri: str):
        p = self._p(uri)
        if not p.exists():
            return
        with gzip.open(p, "rt", encoding="utf-8", errors="replace", newline="") as fh:
            yield from fh


# ------------------------------------------------------------------- parsing
def parse_ts(v):
    """Return epoch seconds, or None. Accepts ISO-8601 and numeric epochs."""
    if v in (None, ""):
        return None
    try:
        x = float(v)
        if x > 1e18:
            x /= 1e9
        elif x > 1e15:
            x /= 1e6
        elif x > 1e11:
            x /= 1e3
        return x
    except (TypeError, ValueError):
        pass
    try:
        s = str(v).strip().replace("Z", "+00:00")
        d = dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.timestamp()
    except Exception:                                            # noqa: BLE001
        return None


def num(v):
    if v in (None, ""):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def session_date(uri: str):
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", uri)
    if not m:
        return None
    try:
        return dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None


def is_sealed(d: dt.date) -> bool:
    return SEALED_FROM <= d <= SEALED_TO


# ----------------------------------------------------------------- extraction
def extract_events(store, session_uri, coin, stats):
    """Yield one record per lifecycle event that carries strike information."""
    uri = session_uri.rstrip("/") + f"/{coin}/kalshi_lifecycle.csv.gz"
    try:
        for row in csv.DictReader(store.lines(uri)):
            stats["lifecycle_rows"] += 1
            raw = row.get("raw_json") or ""
            if "strike" not in raw:
                continue
            try:
                msg = json.loads(raw).get("msg", {})
            except Exception:                                    # noqa: BLE001
                stats["unparseable_raw_json"] += 1
                continue
            if not isinstance(msg, dict):
                continue
            ticker = msg.get("market_ticker") or row.get("market_ticker") or ""
            series = ticker.split("-")[0] if ticker else ""
            if "15M" not in series:
                stats["non_15m_skipped"] += 1
                continue
            stype = msg.get("strike_type")
            fstrike = num(msg.get("floor_strike"))
            if stype is None and fstrike is None:
                continue
            recv = parse_ts(row.get("recv_ts_utc"))
            if recv is None:
                stats["event_missing_recv_ts"] += 1
                continue
            stats["strike_events"] += 1
            yield {
                "market_ticker": ticker,
                "series": series,
                "recv_ts": recv,
                "recv_ts_utc": row.get("recv_ts_utc", ""),
                "strike_type": stype or "",
                "floor_strike": "" if fstrike is None else repr(fstrike),
                "cap_strike": ("" if num(msg.get("cap_strike")) is None
                               else repr(num(msg.get("cap_strike")))),
                "round_digits": str((msg.get("custom_strike") or {}).get("round_digits", "")),
                "event_type": msg.get("event_type", ""),
                "found_in_coin_dir": coin,
                "source_session": Path(session_uri.rstrip("/")).name,
                # provenance: identifies the exact message, so duplicates
                # collapse and near-duplicates do not.
                "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            }
    except Exception as exc:                                     # noqa: BLE001
        stats["file_read_errors"] += 1
        print(f"    warn {coin}: {type(exc).__name__}: {exc}", file=sys.stderr)


def dedupe_and_flag(events, stats):
    """Collapse identical events; flag incompatible ones. Never choose."""
    by_hash = {}
    for e in events:
        k = (e["market_ticker"], e["raw_sha256"])
        if k in by_hash:
            stats["duplicate_identical_collapsed"] += 1
            # keep the earliest sighting; record that it was seen more than once
            if e["recv_ts"] < by_hash[k]["recv_ts"]:
                e["seen_copies"] = by_hash[k].get("seen_copies", 1) + 1
                by_hash[k] = e
            else:
                by_hash[k]["seen_copies"] = by_hash[k].get("seen_copies", 1) + 1
            continue
        e["seen_copies"] = 1
        by_hash[k] = e

    uniq = sorted(by_hash.values(), key=lambda x: (x["market_ticker"], x["recv_ts"]))

    # conflict = same ticker, same instant, different strike or comparator
    at_instant = collections.defaultdict(set)
    for e in uniq:
        at_instant[(e["market_ticker"], round(e["recv_ts"], 3))].add(
            (e["floor_strike"], e["strike_type"]))
    conflicted = {t for (t, _), v in at_instant.items() if len(v) > 1}
    for e in uniq:
        e["strike_conflict"] = int(e["market_ticker"] in conflicted)
        e["comparator_known"] = int(e["strike_type"] in KNOWN_COMPARATORS)
    stats["tickers_with_strike_conflict"] = len(conflicted)
    stats["events_unknown_comparator"] = sum(1 for e in uniq if not e["comparator_known"])
    return uniq


# ------------------------------------------------------------------- coverage
def load_opportunities(path, sealed_report):
    """The paper-bot universe. Every row survives into the coverage report."""
    rows = []
    for x in csv.DictReader(open(path, newline="")):
        t = parse_ts(x.get("t_entry"))
        if t is None:
            continue
        d = dt.datetime.fromtimestamp(t, dt.timezone.utc).date()
        if is_sealed(d):
            sealed_report["trades_in_sealed_window_excluded"] += 1
            continue
        rows.append({
            "t": t,
            "date": d.isoformat(),
            "ticker": x.get("ticker", ""),
            "coin": x.get("sib", ""),
            "side": x.get("opp", ""),
            "cost": x.get("cost", ""),
            "win": x.get("win", ""),
        })
    return rows


def coverage(opps, events, stats):
    """Left join. Every opportunity produces exactly one row."""
    by_ticker = collections.defaultdict(list)
    for e in events:
        if e["floor_strike"] == "":
            continue
        by_ticker[e["market_ticker"]].append(e)
    for t in by_ticker:
        by_ticker[t].sort(key=lambda x: x["recv_ts"])

    out = []
    for o in opps:
        evs = by_ticker.get(o["ticker"], [])
        rec = dict(o)
        rec["n_strike_events"] = len(evs)

        # strike_any: best available, regardless of arrival time
        if evs:
            last = evs[-1]
            rec["strike_any"] = last["floor_strike"]
            rec["comparator_any"] = last["strike_type"]
            rec["strike_conflict"] = last["strike_conflict"]
        else:
            rec["strike_any"] = ""
            rec["comparator_any"] = ""
            rec["strike_conflict"] = 0

        # strike_asof: the latest event we had RECEIVED by decision time.
        # Strictly causal. An event one millisecond later is not available.
        prior = [e for e in evs if e["recv_ts"] <= o["t"]]
        if prior:
            p = prior[-1]
            rec["strike_asof"] = p["floor_strike"]
            rec["comparator_asof"] = p["strike_type"]
            rec["strike_asof_age_s"] = round(o["t"] - p["recv_ts"], 3)
        else:
            rec["strike_asof"] = ""
            rec["comparator_asof"] = ""
            rec["strike_asof_age_s"] = ""

        if not evs:
            rec["status"] = "MISSING_STRIKE"
        elif rec["strike_conflict"]:
            rec["status"] = "STRIKE_CONFLICT"
        elif not prior:
            rec["status"] = "STRIKE_ARRIVED_AFTER_DECISION"
        elif p["strike_type"] not in KNOWN_COMPARATORS:
            rec["status"] = "UNKNOWN_COMPARATOR"
        else:
            rec["status"] = "OK"
        stats[rec["status"]] += 1
        out.append(rec)
    return out


def write_csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ----------------------------------------------------------------------- main
def sessions_in_range(store, d0, d1, allow_sealed):
    out, skipped = [], 0
    for u in store.ls(f"{BUCKET}/KCP_data/"):
        d = session_date(u)
        if d is None or not (d0 <= d <= d1):
            continue
        if is_sealed(d) and not allow_sealed:
            skipped += 1
            continue
        out.append((d, u))
    return sorted(out), skipped


def run(a, store):
    d0 = dt.date.fromisoformat(a.date_from)
    d1 = dt.date.fromisoformat(a.date_to)
    if is_sealed(d1) and not a.allow_sealed:
        print(f"--to {a.date_to} falls in the sealed holdout "
              f"({SEALED_FROM}..{SEALED_TO}). Refusing.", file=sys.stderr)
        return 2

    sess, skipped = sessions_in_range(store, d0, d1, a.allow_sealed)
    print(f"sessions in range {d0}..{d1}: {len(sess)}"
          + (f"   ({skipped} sealed sessions refused)" if skipped else ""))
    if a.probe:
        for d, u in sess[:5]:
            print("   ", Path(u.rstrip('/')).name)
        if len(sess) > 5:
            print(f"    ... and {len(sess)-5} more")
        print(f"\nwould read {len(sess)} x {len(COINS)} = {len(sess)*len(COINS)} "
              f"lifecycle files")
        return 0

    stats = collections.Counter()
    events = []
    for i, (d, u) in enumerate(sess, 1):
        if i % 10 == 1 or i == len(sess):
            print(f"[{i}/{len(sess)}] {Path(u.rstrip('/')).name}")
        for coin in COINS:
            events.extend(extract_events(store, u, coin, stats))

    events = dedupe_and_flag(events, stats)

    out = Path(os.path.expanduser(a.out))
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "strike_events.csv", events)

    # one row per ticker, latest event, for convenience
    latest = {}
    for e in events:
        t = e["market_ticker"]
        if t not in latest or e["recv_ts"] > latest[t]["recv_ts"]:
            latest[t] = e
    write_csv(out / "strike_table.csv", sorted(latest.values(),
                                               key=lambda x: x["market_ticker"]))

    lines = []
    def P(s=""):
        print(s)
        lines.append(s)

    P()
    P("=" * 68)
    P("STRIKE TABLE")
    P("=" * 68)
    P()
    P(f"  sessions read              {len(sess):,}")
    P(f"  lifecycle rows             {stats['lifecycle_rows']:,}")
    P(f"  strike-bearing events      {stats['strike_events']:,}")
    P(f"  identical duplicates       {stats['duplicate_identical_collapsed']:,}")
    P(f"  unique events kept         {len(events):,}")
    P(f"  distinct 15M markets       {len(latest):,}")
    P(f"  tickers with CONFLICT      {stats['tickers_with_strike_conflict']:,}")
    P(f"  unknown comparator         {stats['events_unknown_comparator']:,}")
    P(f"  unparseable raw_json       {stats['unparseable_raw_json']:,}")
    P(f"  files that failed to read  {stats['file_read_errors']:,}")
    P()
    by_series = collections.Counter(e["series"] for e in latest.values())
    P("  distinct markets by series:")
    for s, n in by_series.most_common():
        P(f"    {s:20s} {n:6,}")

    if a.settled:
        sealed = collections.Counter()
        opps = load_opportunities(os.path.expanduser(a.settled), sealed)
        cov = coverage(opps, events, stats)
        write_csv(out / "opportunity_strike_coverage.csv", cov)
        P()
        P("=" * 68)
        P("COVERAGE AGAINST THE PAPER-BOT OPPORTUNITY UNIVERSE")
        P("=" * 68)
        P()
        P(f"  opportunities in range     {len(opps):,}")
        P(f"  rows written               {len(cov):,}   "
          f"({'MATCH' if len(cov)==len(opps) else 'MISMATCH'})")
        P(f"  excluded, sealed window    {sealed['trades_in_sealed_window_excluded']:,}")
        P()
        for k in ("OK", "STRIKE_ARRIVED_AFTER_DECISION", "MISSING_STRIKE",
                  "STRIKE_CONFLICT", "UNKNOWN_COMPARATOR"):
            n = stats[k]
            P(f"    {k:32s} {n:7,}  {100*n/max(len(cov),1):5.1f}%")
        P()
        causal = sum(1 for r in cov if r["strike_asof"] != "")
        anyk = sum(1 for r in cov if r["strike_any"] != "")
        P(f"  strike_any  available      {anyk:7,}  {100*anyk/max(len(cov),1):5.1f}%")
        P(f"  strike_asof available      {causal:7,}  {100*causal/max(len(cov),1):5.1f}%")
        P(f"  cost of the causal rule    {anyk-causal:7,}  "
          f"{100*(anyk-causal)/max(anyk,1):5.1f}% of available strikes")
        P()
        P("  If those two are close, requiring the strike to have ARRIVED")
        P("  before the decision costs nothing. If they diverge, the gap is")
        P("  what a live bot could not have known, and no backtest that uses")
        P("  strike_any is implementable.")
        P()
        # does coverage correlate with anything? a second selection bias
        P("  OK-rate by coin, side and entry-price band:")
        for dim, key in (("coin", "coin"), ("side", "side")):
            g = collections.defaultdict(lambda: [0, 0])
            for r in cov:
                g[r[key]][0] += 1
                g[r[key]][1] += int(r["status"] == "OK")
            P(f"    by {dim}:")
            for k in sorted(g):
                n, ok = g[k]
                P(f"      {k:8s} {ok:6,}/{n:6,}  {100*ok/max(n,1):5.1f}%")
        g = collections.defaultdict(lambda: [0, 0])
        for r in cov:
            c = num(r["cost"])
            b = "n/a" if c is None else f"{int(c*10)/10:.1f}-{int(c*10)/10+0.1:.1f}"
            g[b][0] += 1
            g[b][1] += int(r["status"] == "OK")
        P("    by entry-price band:")
        for k in sorted(g):
            n, ok = g[k]
            if n >= 100:
                P(f"      {k:10s} {ok:6,}/{n:6,}  {100*ok/max(n,1):5.1f}%")
        P()
        P("  Large differences in OK-rate across these dimensions would mean")
        P("  strike availability is a SECOND selection mechanism, layered on")
        P("  the execution-evidence selection already under investigation.")

    (out / "SUMMARY.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    P()
    P(f"  wrote {out}/strike_events.csv")
    P(f"        {out}/strike_table.csv")
    if a.settled:
        P(f"        {out}/opportunity_strike_coverage.csv")
    P(f"        {out}/SUMMARY.txt")
    return 0


# ------------------------------------------------------------------ self-test
def self_test():
    fails = []

    def ck(name, cond):
        print(f"  {name:62s} {'PASS' if cond else 'FAIL'}")
        if not cond:
            fails.append(name)

    def ev(ticker, ts, strike, stype="greater_or_equal"):
        msg = {"event_type": "metadata_updated", "market_ticker": ticker,
               "strike_type": stype, "floor_strike": strike,
               "custom_strike": {"round_digits": "2"}}
        return {"recv_ts_utc": ts, "market_ticker": ticker, "coin": "BTC",
                "event_subtype": "market_lifecycle_v2",
                "raw_json": json.dumps({"type": "market_lifecycle_v2", "msg": msg})}

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        base = root / "KCP_data"
        # one ordinary session, and one inside the sealed window
        for name, rows in (
            ("2026-08-01_Sat_USsession_1330-2000UTC", [
                ev("KXBTC15M-A", "2026-08-01T14:00:00Z", 100.0),
                ev("KXBTC15M-A", "2026-08-01T14:00:00Z", 100.0),        # identical dup
                ev("KXBTC15M-B", "2026-08-01T14:05:00Z", 200.0),
                ev("KXBTC15M-B", "2026-08-01T14:05:00Z", 999.0),        # CONFLICT
                ev("KXBTC15M-C", "2026-08-01T14:10:00Z", 300.0, "exotic"),  # unknown cmp
                ev("KXSOCCER-X", "2026-08-01T14:00:00Z", 1.0),          # not 15M
            ]),
            ("2026-09-09_Wed_USsession_1330-2000UTC", [
                ev("KXBTC15M-SEALED", "2026-09-09T14:00:00Z", 500.0),
            ]),
        ):
            d = base / name / "BTC"
            d.mkdir(parents=True)
            with gzip.open(d / "kalshi_lifecycle.csv.gz", "wt", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            for c in COINS[1:]:
                (base / name / c).mkdir(parents=True)

        # opportunities: one per status we expect to see
        settled = root / "settled.csv"
        T = lambda s: str(dt.datetime.fromisoformat(s).timestamp())
        with settled.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["t_entry", "ticker", "sib", "opp",
                                               "cost", "win", "pnl", "size"])
            w.writeheader()
            for t, tk in (
                ("2026-08-01T14:01:00+00:00", "KXBTC15M-A"),        # OK
                ("2026-08-01T13:59:00+00:00", "KXBTC15M-A"),        # arrived after
                ("2026-08-01T14:06:00+00:00", "KXBTC15M-B"),        # conflict
                ("2026-08-01T14:11:00+00:00", "KXBTC15M-C"),        # unknown cmp
                ("2026-08-01T14:20:00+00:00", "KXBTC15M-ZZZ"),      # missing
                ("2026-09-09T14:30:00+00:00", "KXBTC15M-SEALED"),   # sealed
            ):
                w.writerow({"t_entry": T(t), "ticker": tk, "sib": "BTC",
                            "opp": "YES", "cost": "0.5", "win": "1",
                            "pnl": "0.1", "size": "1"})

        out = root / "out"
        a = argparse.Namespace(date_from="2026-07-15", date_to="2026-09-06",
                               out=str(out), settled=str(settled), probe=False,
                               allow_sealed=False)
        rc = run(a, FakeStore(root))
        ck("run completes", rc == 0)

        cov = list(csv.DictReader((out / "opportunity_strike_coverage.csv").open()))
        # One ticker can carry several opportunities with different statuses --
        # KXBTC15M-A has one decision before its strike arrived and one after --
        # so collect the set per ticker rather than collapsing to the last row.
        st = collections.defaultdict(set)
        for r in cov:
            st[r["ticker"]].add(r["status"])
        ck("every non-sealed opportunity produces one row", len(cov) == 5)
        ck("no sealed-window trade in the output",
           not any("SEALED" in r["ticker"] for r in cov))
        ck("normal case -> OK", "OK" in st["KXBTC15M-A"])
        ck("same ticker, earlier decision -> arrived-after",
           st["KXBTC15M-A"] == {"OK", "STRIKE_ARRIVED_AFTER_DECISION"})
        ck("conflicting strikes fail closed", st["KXBTC15M-B"] == {"STRIKE_CONFLICT"})
        ck("unknown comparator is refused", st["KXBTC15M-C"] == {"UNKNOWN_COMPARATOR"})
        ck("absent strike is counted, not dropped",
           st["KXBTC15M-ZZZ"] == {"MISSING_STRIKE"})

        evs = list(csv.DictReader((out / "strike_events.csv").open()))
        ck("identical duplicate collapsed",
           sum(1 for e in evs if e["market_ticker"] == "KXBTC15M-A") == 1)
        ck("non-15M series excluded",
           not any("SOCCER" in e["market_ticker"] for e in evs))
        ck("sealed session never read",
           not any("SEALED" in e["market_ticker"] for e in evs))

        row_a = next(r for r in cov if r["ticker"] == "KXBTC15M-A"
                     and r["status"] == "OK")
        ck("strike_asof carries an age", row_a["strike_asof_age_s"] != "")
        row_late = next(r for r in cov
                        if r["status"] == "STRIKE_ARRIVED_AFTER_DECISION")
        ck("late strike: asof blank but any populated",
           row_late["strike_asof"] == "" and row_late["strike_any"] != "")

        # a run that TARGETS the sealed window must refuse
        a2 = argparse.Namespace(date_from="2026-09-07", date_to="2026-09-13",
                                out=str(root / "o2"), settled=None, probe=False,
                                allow_sealed=False)
        ck("a run aimed at the holdout is refused", run(a2, FakeStore(root)) == 2)

    print("\n" + ("ALL PASS" if not fails else "FAILURES: " + ", ".join(fails)))
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="date_from", default="2026-07-15")
    ap.add_argument("--to", dest="date_to", default="2026-09-06")
    ap.add_argument("--out", default="~/strike_table")
    ap.add_argument("--settled", default=None,
                    help="paper-bot settled CSV, for the coverage report")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--allow-sealed", action="store_true",
                    help="deliberately read the holdout. Do not use in development.")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    return run(a, Store())


if __name__ == "__main__":
    raise SystemExit(main())
