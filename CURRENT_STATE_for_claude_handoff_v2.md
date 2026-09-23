# CURRENT_STATE — for Claude handoff

Last updated 2026-09-19. Supersedes the 2026-09-17 version.

**Opening line for a fresh conversation:**

> Continue the Kalshi contagion paper bot work. State is in
> `CURRENT_STATE_for_claude_handoff.md` in github.com/ebbandari/kal-claude-share.
> The velocity alignment result is the live finding; next is the foundation
> work in section 6.

---

## THE ONE THING THAT MATTERS MOST

**The premise has never been tested.** Nobody has checked whether laggards
actually follow when two or more coins collapse on the same side. Every week of
work has been about execution — fills, fees, reachability, sizing, direction.

If laggards do not follow, the trigger is noise and all of it is polish on a
rule with no premise.

Test: find every instant where ≥2 coins moved ≥0.12 same-side within 15s;
measure each laggard's mid at 15/30/60/120s and at settlement; compare against
matched random instants. Data is in the KCP snapshots, ~8s cadence.

---

## 1. What the bot does, settled from source

`contagion_paper_bot_v2_1_5.py` line 692:

```python
opp = "NO" if tr["side"] == "YES" else "YES"
```

`tr["side"]` is the **collapsing** side. YES collapses on 2+ coins → buy **NO**
on the laggards → betting the laggards **follow**. It is contagion.

Line 695: buying NO is priced `1 - yes_bid`, the correct complement. That is
also why a `cost = 1.0` row exists — `yes_bid` was 0.

**The trigger is symmetric.** It fires on a YES *or* NO mid dropping, and a NO
collapse is a YES rise. Counts are balanced: 11,398 YES vs 12,238 NO purchases.

```
OBS_S        = 15    the only trigger window
DROP_C       = 0.12  TWELVE CENTS of mid movement, not 12 percent
T2_WINDOW_S  = 60    two coins must fire within the same epoch
BREAKER_N    = 4     four or more -> stand down
REFRACTORY_S = 120   cooldown
```

**None of these was derived.** They define the entire 47,268-trade population,
so every result is conditional on them. Esfandiar's position: one threshold
cannot suit BTC and SOL alike; it should be in units that make sense; it should
differ per coin; and **most importantly it should be dynamic over time.**
Candidate fix: normalise by recent realised volatility (`rv60_bps` is already
in the row table) so the threshold is "a 2-sigma move".

Two instances running since July, both `layer=shadow`, agreeing 99.3%
trade-for-trade: brain PID 5670 and probe2 PID 1355.

---

## 2. Solved, and not worth revisiting

**Strike K.** Published per market in the lifecycle `metadata_updated` event's
`raw_json` — the flat column is blank on 100% of rows. `strike_type:
greater_or_equal` on 3,172 of 3,172 sampled 15M markets, and the field is not
degenerate. Kalshi's REST API serves settled markets unauthenticated. Coverage
79.4% → **99.83%**.

**The settlement label.** Kalshi settles on 60 one-per-second CF readings over
`[T-60, T)` on the **.000** offset. Verified on 40 markets, independently
confirmed by HYPE at 1 Hz. Use fixed `.000`; never select per market against
`expiration_value` — that is look-ahead.

**Reversal.** Closed, negatively. `PnL_opp = -PnL_current - spread - fee_own -
fee_opp` — two fee terms. Both reversal policies worse than skip-only at every
size. Baseline reconciliation exact to 0.000000000.

**Amendment leakage.** Closed. Zero duplicate timestamps in 7,195,149 rows.
GPT withdrew the concern. **Caveat:** that audit covered 400 of 14,809 files
and checked within-file only, so cross-file boundary duplicates were never
tested. A complete audit was written, not run. Low priority.

**Polymarket's price-to-beat needs no API.** Polymarket's docs state that both
the price to beat and the settlement price come from the Chainlink TWAP feed,
60-second lookback for 15-minute markets. So it is
`Chainlink 60s TWAP over [open-60, open)`, computable from
`rtds_chainlink_prices` at ~41 updates/minute.

---

## 3. The live finding

**Skipping trades where CF was moving AWAY from the purchased side.**

Baseline: half-touch CF-skip-only, **+$934.81** on 25,847 trades.

```
lookback   TOWARD $    AWAY $    n kept   familywise p (YES / NO)
5s         +3299.63  -2256.68    17,551    0.0958 / 0.0120
15s        +2935.42  -2077.35    20,924    0.0080 / 0.0080   <-- both clear
30s        +2626.07  -1843.13    19,939    0.3752 / 0.0279
60s+                                       all fail
```

**Use 15s, not 5s.** Nothing in the code selects a horizon — all six are
independent labels on the same 25,847 trades, and one trade can be TOWARD at 5s
and AWAY at 300s. Claude picked 5s by reading all six and reporting the largest
dollar figure, which is selection on the reported statistic. GPT's family-wise
p prices that: **15s is the only horizon where both sides clear.**

**The short lookbacks are information; the long ones are price selection.** At
15s the mean entry price is 0.4750 toward vs 0.4745 away. At 300s it is 0.635
vs 0.388 — a 24-cent gap, so "aligned at 300s" just means "already moved".

**It holds in all three regimes and is largest PRE-drawdown**, so it is not a
recovery artifact. 5 of 6 segment-side cells positive, 10 of 12 coin-side
cells. It also works on coins that lose overall, so it is not a disguised coin
effect.

**But it reduces the bleed rather than rescuing it.** Segment 2, aligned only,
at 15s: `4,588 × -0.2397 + 4,198 × +0.0334 = -$960`.

---

## 4. The three regimes

```
Jul 15-27      +525    62% of days up
Jul 28-Aug 21  -1,979  25 days, 36% of days up
Aug 22-Sep 06  +2,389  75% of days up, win rate jumped ~25% -> ~42%
```

Max drawdown **-2,260.04** trade-level, 24.1 days, against +934.81 total — a
**2.4x ratio**. Strip the last 16 days and the rule loses 1,461 over the first
38. **The win-rate jump hit all sessions and all coins at once**, which points
at the market, not the rule.

**XRP and DOGE are the drawdown.** Segment 2, TOWARD-only: XRP -378.16 and
DOGE -403.82 against the other four at +209.05.

---

## 5. KNOWN WRONG — fix before quoting

**The AfterHours number is computed with a broken session boundary.** The
diagnostic used `h < 13` for EuropeanAM when the archive boundary is **13:30**,
so 13:00-13:29 was misfiled into USsession.

```
AfterHours +871 of +935 total   AfterHours and Asia are UNAFFECTED (20:00-24:00,
                                00:00-07:00 in both versions)
EuropeanAM -182.26              WRONG
USsession   +52.32              WRONG
```

The velocity program uses the correct boundary but was never run on that cut.

---

## 6. NEXT — foundation before experiments

Sorted by **what gets answered once and never asked again**, not by what is
runnable today.

### Foundation — worth a broken day

```
0  BACKUP + this document        the velocity result exists on one VM
1  script review                 has caught a real defect in every tool
2  AfterHours correction         a number we KNOW is wrong is in the record
3  volatility signature          realised variance per unit time, 0.2s..60s.
                                 Settles Savitzky-Golay vs TV vs two-point
                                 PERMANENTLY. Every future derivative choice
                                 depends on it.
4  index tracking                CF/Chainlink/Coinbase pairwise bps, by coin,
                                 session and segment. Settles whether the three
                                 are interchangeable. Every cross-venue idea
                                 needs it.
5  lead-lag                      does Coinbase lead CF, at -5s..+5s. Has a
                                 MECHANISM: CF is an aggregate with Coinbase as
                                 an input. A lead that appears only when one
                                 feed is stale is not a lead -- report clock-age
                                 distributions.
```

### Experiments — repeatable endlessly, do after

```
Chainlink entry/exit overlay     classify executed trades by Chainlink
                                 direction; reversal-exit priced from the
                                 executable arrival BID with its own fee, never
                                 from midpoints
acceleration building/exhausting uses accel_est_*_oriented_bps_s2, already
                                 computed and never examined
run_audition.py                  micro-structural vs macro-regime fork. Tested
                                 on synthetic only; may need repair first
contagion premise test           see top of this document
depth within coin                the cross-coin dismissal was confounded
sizing past SIZE_CAP=25          arbitrary, never exceeded, and every
                                 measurement says bigger is better
WRAcc / PRIM / EBM               only after the holdout
spend Sept 7-13 ONCE             after freezing at most three policies
```

---

## 7. Conventions that are not optional

**Sept 7-13 2026 is sealed.** Every tool enforces it. Never `--allow-sealed`.

**Chaining jobs:** capture the child PID and check its exit code. Never
`pgrep -f` (matches its own command line), never grep a log for `error` or
`abort` (a healthy log contains those words).

**GCS:** one reused in-process `google.cloud.storage.Client`. Never
`subprocess` + `gsutil` in a loop — 208x slower, measured.

**probe2 has read-only GCS scope.** Writing requires
`gcloud auth login --no-launch-browser` with `CLOUDSDK_CONFIG` in a temp dir
and a revoke trap. See `backup_cf_reachability_v1_1.sh`.

**Progress every ~1%** with elapsed and remaining. **Section-number long
programs** (S1, S1.1 …) — it speeds up adversarial review materially. **One-line
description at the top of every script.**

---

## 8. Where things live

```
probe2   all analysis inputs and outputs, 11 GB CF cache, ~47 GB free
brain    live paper bot + both CF collectors. Do not disturb.
GCS      gs://kalshi-data-vault-kalshi-collector-personal/
           analysis/CF_REACHABILITY_V1_1/snapshots/20260916T005907Z/
           48 objects, 258.86 MiB, SHA-256 readback, SNAPSHOT_COMPLETE.json
           NOTE: predates the reversal and velocity outputs -- back those up
github   ebbandari/kal-claude-share — transfer channel between Esfandiar,
           Claude and GPT. Not an archive; GCS is the archive.
```

Current tools, all GPT adversarial rewrites of Claude drafts:

```
build_strike_table_v1_1_gpt.py          cf_reachability_v1_1_gpt.py
recover_strikes_from_api_v1_1_gpt.py    cf_reversal_counterfactual_v2_1_gpt.py
build_opportunity_base_v1_1_gpt.py      cf_velocity_alignment_v1_1_gpt.py
verify_settlement_offset_v1_1_gpt.py
```

---

## 9. The recurring failure mode

Claude's drafts pass their own tests and then fail in production because a
column name or a population definition was **assumed rather than read**:

- `near_impossible` — the real column is `near_impossible_exact`. Would have
  made three of four policies silently identical to the fourth.
- `accel_5_30` — the real column is `accel_5_30_bps_per_s`. Would have
  analysed nothing.
- session boundary at 13:00 — the real boundary is 13:30. Corrupted the
  Europe/US split, and that error is still in the record.

**Read the header before writing the reader.** A self-test built on your own
fixtures cannot catch a schema mismatch with a real file.

---

## 10. Pending from Esfandiar

He wants a **longer-term plan** than a day's steps, and has a **twist** on it he
will share. He asked to be reminded.
