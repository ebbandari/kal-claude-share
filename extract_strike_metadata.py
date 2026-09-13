#!/usr/bin/env python3
"""
extract_strike_metadata.py

Answer one question: does `strike_type` vary?

The schema pack showed `strike_type: greater_or_equal` on four 15-minute
markets in a single 18-minute window.  That is a reasonable observation and a
bad basis for a rule, because it decides how ties settle -- whether an exact
equality between the final-minute CF average and the strike pays YES or pays
nothing.  A pipeline that assumes the wrong comparator is wrong only on the
rare exact tie, which is precisely the kind of error that survives testing and
surfaces with money on the table.

So this samples BREADTH rather than depth: a few sessions spread across the
whole archive, every coin, every 15-minute crypto series it finds -- including
series we do not subscribe to, since they appear in the lifecycle stream
anyway and a differing comparator on those would be informative.

WHERE THE DATA IS
-----------------
`floor_strike` as a flat CSV column is blank on 100% of rows.  The
authoritative value is nested inside `raw_json` on lifecycle
`metadata_updated` events:

    {"type":"market_lifecycle_v2",
     "msg":{"event_type":"metadata_updated",
            "market_ticker":"KXBTC15M-26SEP061000-00",
            "strike_type":"greater_or_equal",
            "floor_strike":79800.71,
            "custom_strike":{"round_digits":"2"},
            "yes_sub_title":"Target Price: $79,800.71"}}

This reads only.  It never writes to GCS and never modifies the archive.

USAGE
    python3 extract_strike_metadata.py --probe
    python3 extract_strike_metadata.py --out ~/strike_metadata
    python3 extract_strike_metadata.py --out ~/strike_metadata --every-n 3
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

BUCKET = "gs://kalshi-data-vault-kalshi-collector-personal"
COINS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE")


def sh(cmd, timeout=300):
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def gs_ls(uri):
    rc, out, _ = sh(["gsutil", "ls", uri])
    if rc != 0:
        return []
    return [x.strip() for x in out.decode("utf-8", "replace").splitlines()
            if x.strip().startswith("gs://")]


def gs_lines(uri):
    """Stream a gzipped object without landing it on disk."""
    p = subprocess.Popen(["gsutil", "cat", uri], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
    gz = gzip.GzipFile(fileobj=p.stdout, mode="rb")
    try:
        for line in io.TextIOWrapper(gz, encoding="utf-8", errors="replace", newline=""):
            yield line
    finally:
        try:
            gz.close()
        except Exception:                                       # noqa: BLE001
            pass
        try:
            p.stdout.close()
        except Exception:                                       # noqa: BLE001
            pass
        if p.poll() is None:
            p.terminate()


def session_date(uri):
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", uri)
    return m.group(1) if m else None


def pick_sessions(every_n):
    """One session per every_n distinct dates, spread across the archive."""
    all_sessions = [u for u in gs_ls(f"{BUCKET}/KCP_data/") if session_date(u)]
    by_date = collections.defaultdict(list)
    for u in all_sessions:
        by_date[session_date(u)].append(u)
    dates = sorted(by_date)
    chosen = []
    for i, d in enumerate(dates):
        if i % every_n:
            continue
        # prefer the US session; it has the most markets
        s = next((x for x in by_date[d] if "USsession" in x), by_date[d][0])
        chosen.append((d, s))
    return dates, chosen


def extract(session_uri, coin, date, rows, stats):
    uri = session_uri.rstrip("/") + f"/{coin}/kalshi_lifecycle.csv.gz"
    try:
        rd = csv.DictReader(gs_lines(uri))
        for r in rd:
            stats["lifecycle_rows"] += 1
            raw = r.get("raw_json") or ""
            if "strike_type" not in raw:
                continue
            try:
                m = json.loads(raw).get("msg", {})
            except Exception:                                   # noqa: BLE001
                stats["unparseable_raw_json"] += 1
                continue
            if "strike_type" not in m:
                continue
            ticker = m.get("market_ticker", "")
            series = ticker.split("-")[0] if ticker else ""
            stats["metadata_updated_events"] += 1
            rows.append({
                "date": date,
                "coin_directory": coin,        # which coin dir it was FOUND in
                "series": series,              # NOT the same thing -- lifecycle
                "market_ticker": ticker,       # is not coin-scoped
                "strike_type": m.get("strike_type", ""),
                "floor_strike": m.get("floor_strike", ""),
                "cap_strike": m.get("cap_strike", ""),
                "round_digits": (m.get("custom_strike") or {}).get("round_digits", ""),
                "yes_sub_title": m.get("yes_sub_title", ""),
                "event_type": m.get("event_type", ""),
                "recv_ts_utc": r.get("recv_ts_utc", ""),
            })
    except Exception as exc:                                    # noqa: BLE001
        stats["read_errors"] += 1
        print(f"    warn {coin}: {type(exc).__name__}: {exc}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="~/strike_metadata")
    ap.add_argument("--every-n", type=int, default=7,
                    help="sample one session per N distinct dates (default 7, weekly)")
    ap.add_argument("--coins", default=",".join(COINS))
    ap.add_argument("--probe", action="store_true",
                    help="report which sessions would be read, then exit")
    a = ap.parse_args()

    coins = [c.strip().upper() for c in a.coins.split(",") if c.strip()]
    dates, chosen = pick_sessions(a.every_n)
    if not dates:
        print("no KCP sessions found", file=sys.stderr)
        return 2

    print(f"archive spans {dates[0]} .. {dates[-1]}  ({len(dates)} distinct dates)")
    print(f"sampling every {a.every_n} -> {len(chosen)} sessions x {len(coins)} coins "
          f"= {len(chosen)*len(coins)} lifecycle files\n")
    for d, s in chosen:
        print("   ", d, Path(s.rstrip("/")).name)
    if a.probe:
        return 0

    rows, stats = [], collections.Counter()
    print()
    for i, (d, s) in enumerate(chosen, 1):
        print(f"[{i}/{len(chosen)}] {d}")
        for coin in coins:
            before = len(rows)
            extract(s, coin, d, rows, stats)
            got = len(rows) - before
            if got:
                print(f"    {coin:5s} {got:4d} metadata_updated events")

    out = Path(os.path.expanduser(a.out))
    out.mkdir(parents=True, exist_ok=True)

    if rows:
        keys = list(rows[0].keys())
        with (out / "strike_metadata.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)

    # ---- the actual question: does strike_type vary? ----
    fifteen = [r for r in rows if "15M" in r["series"]]
    by_type = collections.Counter(r["strike_type"] for r in rows)
    by_type_15 = collections.Counter(r["strike_type"] for r in fifteen)
    by_series = collections.defaultdict(collections.Counter)
    for r in fifteen:
        by_series[r["series"]][r["strike_type"]] += 1
    by_date_15 = collections.defaultdict(collections.Counter)
    for r in fifteen:
        by_date_15[r["date"]][r["strike_type"]] += 1

    lines = []
    def P(s=""):
        print(s)
        lines.append(s)

    P()
    P("=" * 66)
    P("DOES strike_type VARY?")
    P("=" * 66)
    P()
    P(f"  lifecycle rows read        {stats['lifecycle_rows']:,}")
    P(f"  metadata_updated events    {stats['metadata_updated_events']:,}")
    P(f"  of which 15-minute markets {len(fifteen):,}")
    P(f"  unparseable raw_json       {stats['unparseable_raw_json']:,}")
    P(f"  files that failed to read  {stats['read_errors']:,}")
    P()
    P("  ALL markets, by strike_type:")
    for k, n in by_type.most_common():
        P(f"    {k or '(blank)':24s} {n:6,}")
    P()
    P("  15-MINUTE markets only, by strike_type:")
    for k, n in by_type_15.most_common():
        P(f"    {k or '(blank)':24s} {n:6,}")
    P()
    P("  by 15-minute series:")
    for s in sorted(by_series):
        types = ", ".join(f"{k}={v}" for k, v in by_series[s].most_common())
        P(f"    {s:20s} {types}")
    P()
    P("  by date, 15-minute markets:")
    for d in sorted(by_date_15):
        types = ", ".join(f"{k}={v}" for k, v in by_date_15[d].most_common())
        P(f"    {d}   {types}")
    P()
    if len(by_type_15) <= 1:
        P("  VERDICT: strike_type is CONSTANT across every 15-minute market")
        P("           sampled. Equality pays YES, on this evidence.")
    else:
        P("  VERDICT: strike_type VARIES. The comparator cannot be hard-coded;")
        P("           it must be read per market from lifecycle raw_json.")
    P()
    P("  Note: coin_directory is where the event was FOUND, not the coin it")
    P("  describes. The lifecycle stream is not coin-scoped, so series from")
    P("  other coins appear under any directory. Use `series`, not")
    P("  `coin_directory`, to identify the market.")

    (out / "SUMMARY.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {out}/strike_metadata.csv  ({len(rows):,} rows)")
    print(f"wrote {out}/SUMMARY.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
