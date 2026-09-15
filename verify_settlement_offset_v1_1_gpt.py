#!/usr/bin/env python3
"""Verify which CF sub-second print reproduces Kalshi's published expiration value.

Key rules:
- The ticker clock is America/New_York, not UTC. API close_time is authoritative.
- Settlement window candidates use [close-60s, close), not (close-60s, close].
- Main indices are evaluated across .000/.200/.400/.600/.800 separately.
- HYPE is a 1 Hz exception and is reported as a control, not counted in the 5 Hz vote.
- Each candidate offset must have exactly one usable observation for all 60 seconds.

This script is read-only: it calls the public Kalshi market endpoint and reads archived
CF values from GCS with gsutil. Hour files are cached in memory to avoid repeated
subprocess startup.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
from decimal import Decimal, InvalidOperation
import gzip
import io
import json
import math
import os
import random
import re
import statistics as st
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

BUCKET = "gs://kalshi-data-vault-kalshi-collector-personal"
API = "https://api.elections.kalshi.com/trade-api/v2/markets/"
OFFSETS_MS = (0, 200, 400, 600, 800)
MAIN_5HZ_COINS = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
BANDS = (
    ("OvernightAsia", 0.0, 7.0),
    ("EuropeanAM", 7.0, 13.5),
    ("USsession", 13.5, 20.0),
    ("AfterHours", 20.0, 24.0),
)
INDEX = {
    "BTC": ("KXBTC15M", "BRTI"),
    "ETH": ("KXETH15M", "ETHUSD_RTI"),
    "SOL": ("KXSOL15M", "SOLUSD_RTI"),
    "XRP": ("KXXRP15M", "XRPUSD_RTI"),
    "DOGE": ("KXDOGE15M", "DOGEUSD_RTI"),
    "HYPE": ("KXHYPE15M", "HYPEUSD_RTI"),
}
MON = dict(JAN=1, FEB=2, MAR=3, APR=4, MAY=5, JUN=6,
           JUL=7, AUG=8, SEP=9, OCT=10, NOV=11, DEC=12)
TICKER_TZ = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

_HOUR_CACHE: dict[tuple[str, str, str, int], list[dict]] = {}


def parse_iso(v: str) -> dt.datetime | None:
    if not v:
        return None
    try:
        x = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return (x if x.tzinfo else x.replace(tzinfo=UTC)).astimezone(UTC)
    except Exception:
        return None


def parse_ticker_close_guess(ticker: str) -> dt.datetime | None:
    """Interpret the ticker's embedded wall clock as America/New_York, then convert UTC.

    Example observed in production:
      KXBTC15M-26SEP061000-00 -> 2026-09-06 14:00:00Z (10:00 EDT)
    """
    m = re.search(r"-(\d\d)([A-Z]{3})(\d\d)(\d{2})(\d{2})-", ticker)
    if not m or m.group(2) not in MON:
        return None
    try:
        local = dt.datetime(
            2000 + int(m.group(1)), MON[m.group(2)], int(m.group(3)),
            int(m.group(4)), int(m.group(5)), tzinfo=TICKER_TZ,
        )
        return local.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def band_for(close_utc: dt.datetime) -> str | None:
    x = close_utc.astimezone(UTC)
    hour = x.hour + x.minute / 60.0 + x.second / 3600.0
    return next((name for name, lo, hi in BANDS if lo <= hour < hi), None)


def decimal_places(raw: str) -> int:
    s = str(raw).strip().lower()
    if "e" in s:
        try:
            d = Decimal(s)
            return max(0, -d.as_tuple().exponent)
        except InvalidOperation:
            return 8
    return len(s.partition(".")[2]) if "." in s else 0


def market_truth(ticker: str, tries: int = 3) -> dict | None:
    """Return published expiration value and authoritative UTC close time."""
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(API + ticker, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                market = json.loads(resp.read().decode("utf-8")).get("market", {})
            raw = market.get("expiration_value")
            close = parse_iso(market.get("close_time"))
            if raw in (None, "") or close is None:
                return None
            value = float(raw)
            if not math.isfinite(value) or value <= 0:
                return None
            return {
                "expiration_value": value,
                "expiration_value_raw": str(raw),
                "close_utc": close,
                "result": str(market.get("result") or ""),
                "floor_strike": market.get("floor_strike"),
                "strike_type": market.get("strike_type"),
            }
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code in (404, 401, 403):
                return None
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        if attempt + 1 < tries:
            time.sleep(0.5 * (2 ** attempt))
    if last:
        print(f"  warn API {ticker}: {last}", file=sys.stderr)
    return None


def _parse_cf_row(row: dict, order: int) -> dict | None:
    ts = parse_iso(row.get("timestamp_utc"))
    if ts is None:
        raw_ms = row.get("timestamp_ms")
        try:
            ts = dt.datetime.fromtimestamp(float(raw_ms) / 1000.0, UTC)
        except Exception:
            return None
    raw_val = row.get("value_float64", row.get("value_raw", row.get("value")))
    try:
        value = float(raw_val)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    try:
        amend = float(row.get("amend_time_ms") or -1)
    except (TypeError, ValueError):
        amend = -1.0
    return {"ts": ts, "value": value, "amend": amend, "order": order}


def read_cf_hour(coin: str, index_id: str, hour_utc: dt.datetime) -> list[dict]:
    h = hour_utc.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    key = (coin, index_id, h.date().isoformat(), h.hour)
    if key in _HOUR_CACHE:
        return _HOUR_CACHE[key]
    uri = (f"{BUCKET}/CF_RTI_V3_1/{coin}/{index_id}/"
           f"{h.date().isoformat()}/{h.hour:02d}/values.csv.gz")
    p = subprocess.run(["gsutil", "cat", uri], capture_output=True, timeout=90)
    if p.returncode != 0:
        raise RuntimeError(f"gsutil cat failed for {uri}: "
                           f"{p.stderr.decode('utf-8', 'replace')[:180]}")
    rows: list[dict] = []
    try:
        with gzip.open(io.BytesIO(p.stdout), "rt", encoding="utf-8", errors="replace", newline="") as fh:
            for i, row in enumerate(csv.DictReader(fh)):
                x = _parse_cf_row(row, i)
                if x is not None:
                    rows.append(x)
    except Exception as exc:
        raise RuntimeError(f"could not parse {uri}: {type(exc).__name__}: {exc}") from exc
    rows.sort(key=lambda r: (r["ts"], r["amend"], r["order"]))
    _HOUR_CACHE[key] = rows
    return rows


def _hours_covering(start: dt.datetime, end_exclusive: dt.datetime) -> list[dt.datetime]:
    h = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    last = (end_exclusive - dt.timedelta(microseconds=1)).astimezone(UTC).replace(
        minute=0, second=0, microsecond=0)
    out = []
    while h <= last:
        out.append(h)
        h += dt.timedelta(hours=1)
    return out


def offset_series_from_rows(rows: list[dict], close_utc: dt.datetime) -> dict:
    """Construct exact 60-observation candidate series for [T-60,T).

    Duplicate exact timestamps are resolved by maximum amend_time_ms, then last row.
    """
    close = close_utc.astimezone(UTC)
    start = close - dt.timedelta(seconds=60)
    chosen: dict[tuple[int, int], dict] = {}
    duplicates = 0
    for row in rows:
        ts = row["ts"].astimezone(UTC)
        if not (start <= ts < close):
            continue
        off = int(round(ts.microsecond / 1000.0))
        if off not in OFFSETS_MS:
            continue
        sec_epoch = int(ts.replace(microsecond=0).timestamp())
        key = (off, sec_epoch)
        old = chosen.get(key)
        if old is not None:
            duplicates += 1
        if old is None or (row["amend"], row["order"]) >= (old["amend"], old["order"]):
            chosen[key] = row

    start_sec = int(start.replace(microsecond=0).timestamp())
    result: dict[str, dict] = {}
    for off in OFFSETS_MS:
        vals = []
        missing = []
        for i in range(60):
            rec = chosen.get((off, start_sec + i))
            if rec is None:
                missing.append(i)
            else:
                vals.append(rec["value"])
        key = f"{off:03d}"
        result[key] = {
            "n": len(vals),
            "complete": len(vals) == 60,
            "mean": st.mean(vals) if len(vals) == 60 else None,
            "missing_seconds": missing,
        }
    return {"start": start, "close": close, "offsets": result,
            "duplicate_exact_timestamps": duplicates}


def offset_means(coin: str, index_id: str, close_utc: dt.datetime) -> dict:
    rows: list[dict] = []
    start = close_utc - dt.timedelta(seconds=60)
    for h in _hours_covering(start, close_utc):
        rows.extend(read_cf_hour(coin, index_id, h))
    return offset_series_from_rows(rows, close_utc)


def load_candidate_pool(table_path: str) -> dict:
    pool = collections.defaultdict(lambda: collections.defaultdict(list))
    with open(os.path.expanduser(table_path), newline="") as fh:
        for row in csv.DictReader(fh):
            if not row.get("floor_strike"):
                continue
            series = row.get("series") or str(row.get("market_ticker", "")).split("-")[0]
            guess = parse_ticker_close_guess(row.get("market_ticker", ""))
            if guess is None:
                continue
            band = band_for(guess)
            if band:
                pool[series][band].append((guess.date().isoformat(), row["market_ticker"], guess))
    return pool


def evaluate_market(coin: str, index_id: str, ticker: str, guessed_close: dt.datetime) -> dict | None:
    truth = market_truth(ticker)
    if truth is None:
        return None
    close = truth["close_utc"]
    data = offset_means(coin, index_id, close)
    decimals = decimal_places(truth["expiration_value_raw"])
    tolerance = 0.5 * (10.0 ** (-decimals)) + 1e-12
    candidates = {}
    for off, rec in data["offsets"].items():
        if rec["complete"]:
            mean = float(rec["mean"])
            abs_err = abs(mean - truth["expiration_value"])
            candidates[off] = {
                "mean": mean,
                "error_bps": 1e4 * (mean - truth["expiration_value"]) / truth["expiration_value"],
                "abs_error": abs_err,
                "within_published_precision": abs_err <= tolerance,
            }
    if not candidates:
        return None
    ranked = sorted(candidates, key=lambda o: candidates[o]["abs_error"])
    winner = ranked[0]
    runner_up_margin = (candidates[ranked[1]]["abs_error"] - candidates[winner]["abs_error"]
                        if len(ranked) > 1 else None)
    return {
        "coin": coin,
        "ticker": ticker,
        "band": band_for(close) or "UNKNOWN",
        "guessed_close_utc": guessed_close.isoformat().replace("+00:00", "Z"),
        "api_close_utc": close.isoformat().replace("+00:00", "Z"),
        "ticker_clock_error_s": (close - guessed_close).total_seconds(),
        "expiration_value": truth["expiration_value"],
        "expiration_value_raw": truth["expiration_value_raw"],
        "result": truth["result"],
        "winner_offset": winner,
        "winner_error_bps": candidates[winner]["error_bps"],
        "winner_abs_error": candidates[winner]["abs_error"],
        "runner_up_margin_abs": runner_up_margin,
        "n_precision_matches": sum(int(v["within_published_precision"]) for v in candidates.values()),
        "duplicates": data["duplicate_exact_timestamps"],
        "candidates": candidates,
        "counts": {off: rec["n"] for off, rec in data["offsets"].items()},
    }


def write_report(path: str, records: list[dict]) -> None:
    if not path:
        return
    p = Path(os.path.expanduser(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "coin", "band", "ticker", "guessed_close_utc", "api_close_utc",
        "ticker_clock_error_s", "expiration_value", "expiration_value_raw",
        "result", "winner_offset", "winner_error_bps", "winner_abs_error",
        "runner_up_margin_abs", "n_precision_matches", "duplicates",
    ]
    for off in (f"{x:03d}" for x in OFFSETS_MS):
        cols += [f"n_{off}", f"mean_{off}", f"error_bps_{off}", f"precision_match_{off}"]
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in records:
            x = {k: r.get(k, "") for k in cols}
            for off in (f"{v:03d}" for v in OFFSETS_MS):
                x[f"n_{off}"] = r["counts"].get(off, 0)
                c = r["candidates"].get(off)
                if c:
                    x[f"mean_{off}"] = repr(c["mean"])
                    x[f"error_bps_{off}"] = repr(c["error_bps"])
                    x[f"precision_match_{off}"] = int(c["within_published_precision"])
            w.writerow(x)


def self_test() -> int:
    fails = []
    def ck(name: str, cond: bool) -> None:
        print(f"  {name:64s} {'PASS' if cond else 'FAIL'}")
        if not cond:
            fails.append(name)

    guess = parse_ticker_close_guess("KXBTC15M-26SEP061000-00")
    ck("ticker 10:00 on 2026-09-06 converts from EDT to 14:00Z",
       guess == dt.datetime(2026, 9, 6, 14, 0, tzinfo=UTC))

    close = dt.datetime(2026, 9, 6, 14, 0, tzinfo=UTC)
    rows = []
    order = 0
    for i in range(60):
        for off in OFFSETS_MS:
            rows.append({"ts": close - dt.timedelta(seconds=60-i) + dt.timedelta(milliseconds=off),
                         "value": 100.0 + off / 1000.0, "amend": -1.0, "order": order})
            order += 1
    # Add values exactly at close; [T-60,T) must exclude them.
    for off in OFFSETS_MS:
        rows.append({"ts": close + dt.timedelta(milliseconds=off), "value": 999.0,
                     "amend": -1.0, "order": order})
        order += 1
    got = offset_series_from_rows(rows, close)
    ck("window is [T-60,T): 60 rows per offset, close rows excluded",
       all(got["offsets"][f"{o:03d}"]["n"] == 60 for o in OFFSETS_MS))
    ck(".200 mean uses the intended 60 pre-close prints",
       abs(got["offsets"]["200"]["mean"] - 100.2) < 1e-12)

    midnight = dt.datetime(2026, 9, 7, 0, 0, tzinfo=UTC)
    rows2 = [{"ts": midnight - dt.timedelta(seconds=60-i) + dt.timedelta(milliseconds=200),
              "value": float(i), "amend": -1.0, "order": i} for i in range(60)]
    got2 = offset_series_from_rows(rows2, midnight)
    ck("midnight-crossing final minute is handled across UTC dates",
       got2["offsets"]["200"]["n"] == 60)

    # Duplicate timestamp: higher amend time wins, no extra sample.
    dup_ts = close - dt.timedelta(seconds=30) + dt.timedelta(milliseconds=200)
    rows3 = list(rows[:-5])
    rows3.append({"ts": dup_ts, "value": 123.0, "amend": 1.0, "order": 9999})
    rows3.append({"ts": dup_ts, "value": 124.0, "amend": 2.0, "order": 10000})
    got3 = offset_series_from_rows(rows3, close)
    ck("duplicate exact timestamp collapses to one sample",
       got3["offsets"]["200"]["n"] == 60 and got3["duplicate_exact_timestamps"] >= 2)
    expected = (59 * 100.2 + 124.0) / 60
    ck("latest/highest-amend duplicate is selected",
       abs(got3["offsets"]["200"]["mean"] - expected) < 1e-9)

    partial = rows[:-5]
    # Remove one .400 second inside the window.
    partial = [r for r in partial if not (r["ts"] == close - dt.timedelta(seconds=10) + dt.timedelta(milliseconds=400))]
    got4 = offset_series_from_rows(partial, close)
    ck("partial offset is refused rather than averaged", not got4["offsets"]["400"]["complete"])

    ck("HYPE is explicitly excluded from the 5 Hz vote", "HYPE" not in MAIN_5HZ_COINS)
    print("\n" + ("ALL PASS" if not fails else "FAILURES: " + ", ".join(fails)))
    return 0 if not fails else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", default="~/strike_table/strike_table.csv")
    ap.add_argument("--coins", default=",".join(INDEX))
    ap.add_argument("--per-band", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--out", default="~/settlement_offset_verification.csv")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()

    random.seed(args.seed)
    pool = load_candidate_pool(args.table)
    coins = [c.strip().upper() for c in args.coins.split(",") if c.strip()]
    bad = [c for c in coins if c not in INDEX]
    if bad:
        raise SystemExit(f"unknown coins: {bad}")

    records: list[dict] = []
    skipped = 0
    intended = 0
    for coin in coins:
        series, index_id = INDEX[coin]
        for band, _, _ in BANDS:
            candidates = pool[series].get(band, [])
            by_day = collections.defaultdict(list)
            for day, ticker, guess in candidates:
                by_day[day].append((ticker, guess))
            days = sorted(by_day)
            random.shuffle(days)
            successes = 0
            for day in days:
                if successes >= args.per_band:
                    break
                intended += 1
                ticker, guess = random.choice(by_day[day])
                try:
                    rec = evaluate_market(coin, index_id, ticker, guess)
                except Exception as exc:  # noqa: BLE001
                    print(f"  skip {ticker}: {type(exc).__name__}: {exc}", file=sys.stderr)
                    rec = None
                if rec is None:
                    skipped += 1
                    continue
                records.append(rec)
                successes += 1
                if args.verbose:
                    errs = " ".join(
                        f".{o}={rec['candidates'][o]['error_bps']:+.4f}bps"
                        for o in sorted(rec["candidates"])
                    )
                    print(f"{coin:5s} {rec['band']:14s} {ticker:26s} "
                          f"winner .{rec['winner_offset']}  {errs}")

    write_report(args.out, records)

    main_records = [r for r in records if r["coin"] in MAIN_5HZ_COINS and len(r["candidates"]) == 5]
    hype_records = [r for r in records if r["coin"] == "HYPE"]
    tally = collections.Counter(r["winner_offset"] for r in main_records)
    by_band = collections.defaultdict(collections.Counter)
    by_coin = collections.defaultdict(collections.Counter)
    errors = collections.defaultdict(list)
    ambiguous = 0
    clock_mismatch = 0
    for r in main_records:
        by_band[r["band"]][r["winner_offset"]] += 1
        by_coin[r["coin"]][r["winner_offset"]] += 1
        ambiguous += int(r["n_precision_matches"] != 1)
        clock_mismatch += int(abs(r["ticker_clock_error_s"]) > 1.0)
        for off, c in r["candidates"].items():
            errors[off].append(abs(c["error_bps"]))

    print("\n" + "=" * 68)
    print(f"  markets evaluated                  {len(records)}")
    print(f"  complete 5 Hz markets in vote      {len(main_records)}")
    print(f"  HYPE 1 Hz controls                 {len(hype_records)}")
    print(f"  skipped/incomplete                 {skipped}")
    print(f"  ticker/API close mismatches >1s    {clock_mismatch}")
    print(f"  non-unique published-precision hit {ambiguous}")

    print("\n  winning offset, main 5 Hz indices only:")
    for off, n in tally.most_common():
        print(f"    .{off}   {n:4d}   {100*n/max(len(main_records),1):5.1f}%")

    print("\n  |error| in bps, main 5 Hz indices:")
    for off in (f"{x:03d}" for x in OFFSETS_MS):
        if errors[off]:
            print(f"    .{off}   mean {st.mean(errors[off]):8.5f}   "
                  f"median {st.median(errors[off]):8.5f}   n={len(errors[off])}")

    print("\n  by UTC session band:")
    for name, _, _ in BANDS:
        if by_band[name]:
            print(f"    {name:16s} " + "  ".join(f".{k}={v}" for k, v in by_band[name].most_common()))

    print("\n  by coin:")
    for coin in coins:
        if by_coin[coin]:
            print(f"    {coin:6s} " + "  ".join(f".{k}={v}" for k, v in by_coin[coin].most_common()))
    if hype_records:
        h = collections.Counter(r["winner_offset"] for r in hype_records)
        print("\n  HYPE 1 Hz observed offset/control (not part of 5 Hz vote):")
        print("    " + "  ".join(f".{k}={v}" for k, v in h.most_common()))

    print(f"\n  report: {os.path.expanduser(args.out)}")
    if main_records and len(tally) == 1 and ambiguous == 0:
        off = next(iter(tally))
        print(f"\n  VERDICT: .{off} uniquely wins every complete 5 Hz market tested.")
        print("           Safe to freeze only after the report confirms coverage across")
        print("           coins, UTC bands, dates, and published-value precision.")
    elif main_records:
        top, n = tally.most_common(1)[0]
        print(f"\n  VERDICT: .{top} wins {n}/{len(main_records)} complete 5 Hz markets.")
        print("           NOT unanimous or not uniquely identified; do not hard-code yet.")
    else:
        print("\n  VERDICT: no complete 5 Hz markets tested; inspect errors/report.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
