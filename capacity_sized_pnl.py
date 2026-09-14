#!/usr/bin/env python3
"""
capacity_sized_pnl.py

Two jobs, in order:

  PART 1  Diagnose the capacity column per coin, so we know which rows have
          genuine displayed-size data and which do not.  An earlier check
          only looked at paper_bot_depth_reconstruction.csv (BTC/ETH/SOL/HYPE)
          and DOGE/XRP live in the other file, so their capacity was never
          actually examined.  Assume nothing; count it.

  PART 2  Recompute PnL buying THE SIZE THAT WAS ACTUALLY AVAILABLE on each
          individual trade -- not an average, not a flat assumption -- with
          the exact Kalshi order fee and the arithmetic bound:

              price * c + fee(price, c)  <  c * $1

          i.e. never buy when the total cost including fees already equals or
          exceeds the maximum possible payout.  Such a trade cannot win.

Every row is treated on its own terms.  The reported "avg size" is only a
summary of the per-trade sizes used; it is never an input to any calculation.

Usage
    python3 capacity_sized_pnl.py --rows ~/paper_bot_reprice_v1_2_rows.csv
    python3 capacity_sized_pnl.py --rows ~/paper_bot_reprice_v1_2_rows.csv --max-size 10
"""
from __future__ import annotations

import argparse
import collections
import csv
import math
import os
import statistics as st


def finite(v):
    """Return a finite float, or None. Empty string and junk both give None."""
    if v in (None, ""):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def exact_fee(price: float, contracts: float) -> float:
    """Kalshi fee: 0.07 * C * P * (1-P), rounded UP to the next whole cent.

    The ceiling applies to the WHOLE ORDER, which is why one contract pays the
    worst rate per contract and larger orders amortise the rounding.
    """
    return math.ceil(0.07 * contracts * price * (1.0 - price) * 100 - 1e-9) / 100.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True,
                    help="paper_bot_reprice_v1_2_rows.csv")
    ap.add_argument("--max-size", type=float, default=25.0,
                    help="cap on contracts per trade (default 25)")
    ap.add_argument("--capacity-field", default="arrival_capacity_at_touch",
                    help="which capacity column to use")
    a = ap.parse_args()

    path = os.path.expanduser(a.rows)

    # ---------------------------------------------------------------- PART 1
    # Count, per coin, what the capacity column actually contains.  MISSING and
    # ZERO are different failures and must not be conflated: missing means the
    # reconstruction never produced a figure, zero means it produced one and it
    # was empty depth.
    diag = collections.defaultdict(lambda: {"rows": 0, "missing": 0, "zero": 0,
                                            "usable": 0, "sizes": []})
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("primary_reprice_eligible") != "1":
                continue
            coin = r.get("coin") or "UNKNOWN"
            d = diag[coin]
            d["rows"] += 1
            cap = finite(r.get(a.capacity_field))
            if cap is None:
                d["missing"] += 1
            elif cap <= 0:
                d["zero"] += 1
            else:
                d["usable"] += 1
                d["sizes"].append(cap)

    print("=== PART 1: does the capacity column actually hold data? ===")
    print("field: %s\n" % a.capacity_field)
    print("%-8s%9s%10s%8s%9s%10s%10s%10s"
          % ("coin", "eligible", "missing", "zero", "usable", "p10", "median", "p90"))
    for coin in sorted(diag):
        d = diag[coin]
        s = sorted(d["sizes"])
        if s:
            p10, med, p90 = s[len(s)//10], st.median(s), s[9*len(s)//10]
            print("%-8s%9d%10d%8d%9d%10.0f%10.0f%10.0f"
                  % (coin, d["rows"], d["missing"], d["zero"], d["usable"], p10, med, p90))
        else:
            print("%-8s%9d%10d%8d%9d%10s%10s%10s"
                  % (coin, d["rows"], d["missing"], d["zero"], d["usable"], "-", "-", "-"))
    print()
    print("  A coin with high 'missing' has NO displayed-size evidence and its")
    print("  sized PnL below is not comparable to the others.  Read that column")
    print("  before reading any result.")

    # ---------------------------------------------------------------- PART 2
    # Per-trade sizing.  c is set from THIS trade's own displayed capacity,
    # capped by --max-size.  No averaging anywhere.
    g = collections.defaultdict(lambda: {
        "n": 0, "contracts": 0.0, "pnl": 0.0, "wins": 0,
        "skip_no_capacity": 0, "skip_bound": 0, "sizes": [],
    })

    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("primary_reprice_eligible") != "1":
                continue
            coin = r.get("coin") or "UNKNOWN"
            ask = finite(r.get("primary_repriced_ask"))
            win = finite(r.get("win"))
            cap = finite(r.get(a.capacity_field))
            if ask is None or win not in (0.0, 1.0):
                continue

            # No usable displayed size -> we cannot say what we could have
            # bought, so we do not guess.  Counted, never silently dropped.
            if cap is None or cap < 1:
                for k in (coin, "ALL"):
                    g[k]["skip_no_capacity"] += 1
                continue

            c = min(cap, a.max_size)          # this trade's own available size
            f = exact_fee(ask, c)

            # Esfandiar's bound: total outlay must be below the maximum payout.
            # If not, the position cannot profit even if it wins outright.
            if ask * c + f >= c * 1.0:
                for k in (coin, "ALL"):
                    g[k]["skip_bound"] += 1
                continue

            pnl = (win - ask) * c - f
            for k in (coin, "ALL"):
                x = g[k]
                x["n"] += 1
                x["contracts"] += c
                x["pnl"] += pnl
                x["wins"] += int(win)
                x["sizes"].append(c)

    print("\n=== PART 2: buy the size actually available on each trade ===")
    print("cap %.0f contracts, exact order fee, bound applied\n" % a.max_size)
    print("%-8s%8s%8s%10s%10s%13s%14s%10s%9s"
          % ("coin", "n", "win%", "med size", "contracts", "total PnL",
             "per contract", "no-cap", "bound"))
    for k in ["ALL"] + sorted(x for x in g if x != "ALL"):
        x = g[k]
        if not x["n"]:
            print("%-8s%8d%8s%10s%10s%13s%14s%10d%9d"
                  % (k, 0, "-", "-", "-", "-", "-",
                     x["skip_no_capacity"], x["skip_bound"]))
            continue
        print("%-8s%8d%7.2f%%%10.0f%10.0f%+13.2f%+14.5f%10d%9d"
              % (k, x["n"], 100*x["wins"]/x["n"], st.median(x["sizes"]),
                 x["contracts"], x["pnl"], x["pnl"]/x["contracts"],
                 x["skip_no_capacity"], x["skip_bound"]))
    print()
    print("  'med size' is the median of the per-trade sizes used; it is a")
    print("  report, not an input.  Each trade was priced at its own size.")
    print("  'no-cap' rows had no displayed size and were EXCLUDED, not assumed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
