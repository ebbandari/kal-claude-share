# CURRENT_STATE

Handoff for a new session. Last updated 2026-09-17.

**Opening line for a fresh conversation:**

> Continue the contagion paper bot CF reachability work. Reversal is closed.
> Next is the velocity alignment lane and the untested contagion premise.
> State is in `CURRENT_STATE.md` in github.com/ebbandari/kal-claude-share.

---

## THE ONE THING THAT MATTERS MOST

**The premise has never been tested.** Nobody has checked whether laggards
actually follow when two or more coins collapse on the same side. Every week of
work so far has been about execution — fills, fees, reachability, sizing.

If laggards do not follow, the trigger is noise and all of it is polish on a
rule with no premise.

Test: find every instant where ≥2 coins moved ≥0.12 same-side within 15s;
measure each laggard's mid at 15/30/60/120s and at settlement; compare against
matched random instants. Data is in the KCP snapshots, ~8s cadence.

---

## What the bot does, settled from source

`contagion_paper_bot_v2_1_5.py` line 692:

```python
opp = "NO" if tr["side"] == "YES" else "YES"
```

`tr["side"]` is the **collapsing** side. YES collapses on 2+ coins → buy **NO**
on the laggards → betting the laggards **follow**. It is contagion.

Line 695: buying NO is priced `1 - yes_bid`, the correct complement. That is
also why a `cost = 1.0` row exists in the logs — `yes_bid` was 0.

Two instances still running, both since July, both `layer=shadow`:

```
brain   PID 5670   settled_2026-07-15.csv
probe2  PID 1355   settled_2026-07-24.csv    independent replication
```

They agree 99.3% trade-for-trade within 2 seconds.

---

## Solved this campaign

**Strike K.** Published per market in the lifecycle `metadata_updated` event's
`raw_json` — the flat `floor_strike` column is blank on 100% of rows.
`strike_type: greater_or_equal` on 3,172 of 3,172 sampled 15M markets, and the
field is not degenerate (other Kalshi series use `structured`, `greater`,
`custom`). Kalshi's REST API serves settled markets unauthenticated. Coverage
went 79.4% → **99.83%**.

**The settlement label.** Kalshi settles on 60 one-per-second CF readings over
`[T-60, T)` on the **.000** sub-second offset. Verified on 40 markets and
independently confirmed by HYPE, which publishes at 1 Hz and has no other
choice. Use fixed `.000`; never select per market against `expiration_value` —
that is look-ahead.

**Reversal.** Closed, negatively.

```
PnL_opp = -PnL_current - spread - fee_own - fee_opp     TWO fee terms
```

```
size    B_SKIP_ONLY    C_REVERSE_ALL    D_REVERSE_CONSERVATIVE
one          -19.85          -561.20                   -191.69
half        +934.81         -6722.94                  -1475.94
full       +1014.39         -8027.54                  -1891.38
```

Both reversal policies worse than skip-only at every size. Baseline
reconciliation exact to 0.000000000.

---

## The live result, with its caveats

**CF reachability gate removes 94.7% of exact-fee losses.** The gate is
`near_impossible = upper CI of p_win < break_even`, where break-even is
`ask + fee/q`. **No tuned threshold anywhere.**

Skip-only is the first positive result at executable prices:

```
q = 1                      -19.85
half touch, cap 25        +934.81
full touch, cap 25       +1014.39
```

Win rate 48.2% at an average entry near 46c. Gross edge ~1.6c, of which the fee
takes ~1.4c. The whole gain from sizing is the cent ceiling amortising.

**But the equity curve is three regimes, not an edge:**

```
Jul 15-27      +525    62% of days up
Jul 28-Aug 21  -1,979  25 days, 36% of days up
Aug 22-Sep 06  +2,389  75% of days up, win rate jumped ~25% -> ~42%
```

Max drawdown **-2,260.04** (trade-level; peak 2026-07-28 00:46, trough
2026-08-21 03:33, 24.1 days) against +934.81 total — a **2.4x ratio**. Strip
the last 16 days and the rule loses 1,461 over the first 38. The win-rate jump
hit all sessions and all coins at once, which points at the market, not the
rule.

**The sealed holdout sits immediately after the best stretch in the record.**

---

## Open, in priority order

1. **Contagion premise** — do laggards follow at all. See top of this file.
2. **Velocity alignment** — `cf_velocity_alignment_v1_1_gpt.py`, both lanes,
   `half_skip` first. Tests whether CF moving toward or away from the bought
   side carries information the gate lacks.
3. **Sizing curve** — `SIZE_CAP = 25` is arbitrary and has never been exceeded.
   Every measurement says bigger is better: q=1 loses, sub-25 orders lost -122,
   q=25 orders made +1,057, median share of touch consumed is 9.8%, and zero
   rows ever contended for the same market+side. Proposed rule:
   `clamp(floor(touch/2), 25, 1000)`, skip when touch < 50.
4. **Depth within coin** — the cross-coin dismissal was confounded; ETH makes
   +1,099 on median depth 150 while BTC makes +599 on depth 2,092.
5. **WRAcc / PRIM / EBM** on the unified policy table — descriptive only until
   the holdout is spent.
6. **Spend Sept 7-13 once**, after freezing at most three policies.

---

## Two measurement gaps found and not yet fixed

**Velocity is a backward average, not instantaneous.** `vel_Xs_bps_per_s` is
the mean over `[t-h, t]`. Under acceleration `a`:

```
v_h = v_t - a*h/2
```

So the stored value lags by `a*h/2` — negligible at 5s, ~480% off at 60s,
meaningless at 300s, **and it can flip the sign**. `v_t` is recoverable from two
horizons and nothing computes it.

**`accel_{a}_{b}_bps_per_s` is not acceleration.** It is `v_short - v_long`, a
velocity contrast in bps/s. The constant-acceleration estimate is:

```
a = 2*(v_short - v_long) / (h_long - h_short)      [bps/s^2]
```

Only that has the units to answer "will it get there in time."

---

## Conventions that are not optional

**Sept 7-13 2026 is sealed.** Every tool enforces it. Never pass
`--allow-sealed` during development.

**Chaining jobs:** capture the child PID and check its exit code. Never
`pgrep -f` (matches its own command line) and never grep a log for `error` or
`abort` (a healthy log contains those words). See `OPERATIONAL_NOTES.md`.

**GCS:** one reused in-process `google.cloud.storage.Client`. Never
`subprocess` + `gsutil` in a loop — measured 208x slower on this project.

**probe2 has read-only GCS scope.** Writing requires
`gcloud auth login --no-launch-browser` with `CLOUDSDK_CONFIG` pointed at a temp
dir and a revoke trap. See `backup_cf_reachability_v1_1.sh`.

**Progress every ~1% of the work**, with elapsed and remaining.

**Section-number long programs** (S1, S1.1, S2 …). It materially speeds up the
adversarial review cycle.

---

## Where things live

```
probe2   all analysis inputs and outputs, 11 GB CF cache, 47 GB free
brain    live paper bot + both CF collectors. Do not disturb.
GCS      gs://kalshi-data-vault-kalshi-collector-personal/
           analysis/CF_REACHABILITY_V1_1/snapshots/20260916T005907Z/
           48 objects, 258.86 MiB, SHA-256 verified by readback,
           SNAPSHOT_COMPLETE.json written last
github   ebbandari/kal-claude-share — code transfer channel between
           Esfandiar, Claude and GPT. Not an archive; GCS is the archive.
```

**Current tool versions**, all on probe2 and in the repo. Every one is GPT's
adversarial rewrite of a Claude draft:

```
build_strike_table_v1_1_gpt.py
recover_strikes_from_api_v1_1_gpt.py
build_opportunity_base_v1_1_gpt.py
verify_settlement_offset_v1_1_gpt.py
cf_reachability_v1_1_gpt.py
cf_reversal_counterfactual_v2_1_gpt.py
cf_velocity_alignment_v1_1_gpt.py
```

For hashes: `sha256sum ~/cf_*_gpt.py ~/build_*_gpt.py ~/recover_*_gpt.py`

---

## The recurring failure mode, stated so the next session avoids it

Claude's drafts pass their own tests and then fail on production because a
column name or a population definition was **assumed rather than read**. It has
happened at least three times:

- `near_impossible` — the real column is `near_impossible_exact`. Would have
  made three of four policies silently identical to the fourth.
- `accel_5_30` — the real column is `accel_5_30_bps_per_s`. Would have
  analysed nothing.
- session boundary at 13:00 — the real boundary is 13:30. Corrupted the
  Europe/US split.

**Read the header before writing the reader.** A self-test built on your own
fixtures cannot catch a schema mismatch with a real file.
