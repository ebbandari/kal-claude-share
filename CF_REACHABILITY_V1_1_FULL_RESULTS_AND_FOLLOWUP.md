# CF Reachability v1.1 — full results and follow-up

Complete record of the run, the outputs on disk, the questions raised afterwards,
and the commands and raw output behind each answer.

Snapshot: `gs://kalshi-data-vault-kalshi-collector-personal/analysis/CF_REACHABILITY_V1_1/snapshots/20260916T005907Z/`

---

## 1. The run

Cache stage completed cleanly: **14,809 FETCHED, 6 ABSENT, of 14,815 objects, 11 GB on disk.**

The chained analysis did not fire overnight. The wrapper's guard was defective in
two ways — a self-matching `pgrep -f`, and a log grep for `abort` that matches the
script's own banner text. **Which defect determined the failure is not established**;
no `CACHE STAGE FAILED` message was printed, which the grep guard would have
produced had it fired. Both are now prohibited in `OPERATIONAL_NOTES.md`. Step 3
was relaunched by hand.

```bash
nohup python3 -u ~/cf_reachability_v1_1_gpt.py \
  --stage all \
  --no-fetch \
  --base ~/paper_bot_opportunity_base.csv \
  --coverage ~/strike_recovery_v1_1/opportunity_strike_coverage_recovered.csv \
  --api-cache ~/strike_recovery_v1_1/api_market_cache.jsonl \
  --out ~/cf_reach_v1_1 \
  --cf-availability-lag-ms 250 \
  > ~/cf_reach_v1_1.log 2>&1 &
```

Started 17:40:57, ran **just over two hours**. Reference build ~1%/min over 59,232
markets, then the opportunity sweep at a similar rate.

**Two claims made during the run that were wrong**, recorded because the pattern
matters more than the instances:

Consecutive tails showed 87% of the reference build and 75.9% of the sweep, and I
concluded two processes were writing to one log. **There was one process**; the
tails straddled the stage change. I then asserted the sweep would be near-instant
because it was "arithmetic on in-memory counts." It was not — each opportunity
performs seven CF lookups, each capable of pulling and decompressing an hour file.

---

## 2. Final output

```
[4/4] outputs

  rows in 47,268   rows out 47,268   RECONCILES
    OK                              47,151   99.8%
    MISSING_STRIKE                      78    0.2%
    STALE_CF                            39    0.1%

  gateable reference rows       47,151
  economic-eligible rows        43,288
  near-impossible exact-fee     17,335
    ask_scalable       removed   -274.603  kept    +90.034  all   -184.568
    ask_exact          removed   -351.227  kept    -19.850  all   -371.077
    logged_scalable    removed   +162.101  kept   +325.230  all   +487.332
    logged_exact       removed    +86.701  kept   +215.202  all   +301.903
  coin        n   near-imp   PnL ask exact
  BTC       6770       2316        -41.580
  ETH       6827       2497        -10.414
  SOL       7195       2637        -23.638
  XRP       7424       3442       -142.873
  DOGE      7651       3523       -125.244
  HYPE      7421       2920        -27.328
```

`MISSING_STRIKE=78` matches the recovery count exactly. `STALE_CF` held at 24 from
59% through 91%, then rose 27 → 35 → 39 in the final ticks — a cluster near the end
of the window, plausibly the 6 ABSENT cache hours surfacing, **not confirmed**.

### The gate removes 94.7% of the losses

```
executable prices, exact one-contract fee
  all trades      -371.08
  gate REMOVES    -351.23     94.7% of the loss
  gate KEEPS       -19.85

executable prices, scalable fee (the large-size limit)
  all trades      -184.57
  gate REMOVES    -274.60
  gate KEEPS       +90.03     positive

kept rows: 25,953
  exact     -19.85  =  -0.00076 per trade
  scalable  +90.03  =  +0.00347 per trade
```

The entire difference between −19.85 and +90.03 is the fee ceiling at size 1. Real
size walks the book, so the truth sits between them and nearer the pessimistic end
than naive scaling implies.

### No threshold was tuned

"Near-impossible" means the upper 95% confidence bound sits below break-even, where
break-even is `executable ask + fee/contracts`. Both sides computed from data.

### The coin pattern is coherent with the hypothesis

```
coin       n   near-imp   rate   PnL exact
BTC     6770       2316    34%     -41.58
ETH     6827       2497    37%     -10.41
SOL     7195       2637    37%     -23.64
HYPE    7421       2920    39%     -27.33
DOGE    7651       3523    46%    -125.24
XRP     7424       3442    46%    -142.87
```

**DOGE and XRP carry 72% of all losses and have the highest near-impossible rates.**
The bot loses most where the CF target was least reachable. This also explains the
inversion found earlier — those two looked best at logged prices and worst at
executable ones.

---

## 3. What the outputs contain

```bash
ls -la ~/cf_reach_v1_1/
```

```
-rw-r--r--     14956  CF_REACHABILITY_REPORT.md
drwxr-xr-x            cf_cache                        11 GB, excluded from backup
-rw-r--r--      2952  cf_reachability_summary.json
-rw-r--r--  12354397  cf_reference_counts.csv.gz
-rw-r--r--  37832583  paper_bot_cf_reachability_rows.csv.gz
```

Integer histogram counts on the fixed signed-bps grid live in
`cf_reference_counts.csv.gz`; probabilities are derived from them at read time in
the row table (~190 columns).

```
reference_gateable        reference_n
reference_horizon_s       reference_underflow_n
reference_cell            reference_overflow_n

p_win_hat                 p_win_wilson_lo / hi
p_win_bin_lower / upper   p_win_day_cluster_lo / hi
p_win_ci_lo / hi_conservative
reference_days            reference_dkw_epsilon

reference_move_p05 .. p95_bps
pred_final_avg_p05 .. p95, each with DKW lo/hi bands, bps and native units

side_clearance_vs_K_q50_bps / q90 / q95
side_projected_clearance_native_q50 / q90 / q95
```

**Four interval methods** — grid discretisation, Wilson, day-clustered, and the
conservative widest. The day-clustered one is the honest default: markets on the
same day share a regime, so treating 1,329 as independent overstates certainty.

---

## 4. The reference evidence

```bash
zcat ~/cf_reach_v1_1/paper_bot_cf_reachability_rows.csv.gz | python3 -c "
import sys,csv,collections
r=csv.DictReader(sys.stdin)
c=collections.Counter(); n=[]
for x in r:
    if x['status']!='OK': continue
    c[x['reference_cell']]+=1
    try: n.append(int(x['reference_n']))
    except: pass
for k,v in c.most_common(): print('  %-40s%8d'%(k,v))
n.sort()
print('reference_n:  min %d  p10 %d  median %d  p90 %d  max %d'%(
      n[0],n[len(n)//10],n[len(n)//2],n[9*len(n)//10],n[-1]))
"
```

```
reference cell level used:
  coin_horizon_session                       29940
  coin_horizon_session_weekday               17211

reference_n:  min 300  p10 324  median 1329  p90 1930  max 2197
```

**No row fell back to `coin_horizon`. None was descriptive or insufficient.**
Minimum is exactly 300 — the gate threshold — so everything below it was refused.
Median 1,329.

That rules out a thin-tail artifact: at n=1,329 a 5% tail rests on ~66 observations,
not 5.

**One thing worth stating plainly:** 63.5% of rows *could not* use the weekday cell
and backed off to session. So this is effectively a **coin × horizon × session**
model. The session distinction is vindicated; weekday conditioning is aspirational
at this sample size and should not be described as though it were used throughout.

---

## 5. The reversal question, and a correction

Esfandiar asked whether reversing the near-impossible trades — the contrarian of the
contrarian rule — would be profitable.

**My first answer was materially wrong.** I wrote `2.03c − spread` and omitted both
fee terms. The correct relation is:

```
PnL_opp = -PnL_current - spread - fee_yes - fee_no
```

because `-PnL_current` adds back the fee paid on the losing side, so it must be
subtracted again, and the new side's fee subtracted too. **Two fees, not zero.**

Worked example at a 0.15/0.85 pair, both fees 0.01:

```
YES loses    -0.1600
NO wins      +0.1400        my estimate said +0.1600
```

The measured spread, from 44,304 real decision instants:

```bash
python3 -c "
import csv, statistics as st
s=[]
for p in ('/home/esfandiar/paper_bot_depth_reconstruction.csv',
          '/home/esfandiar/paper_bot_depth_DOGE_XRP.csv'):
    for r in csv.DictReader(open(p)):
        try:
            ya=float(r['decision_yes_ask']); na=float(r['decision_no_ask'])
        except: continue
        if 0<ya<1 and 0<na<1: s.append(ya+na-1)
print('n=%d  median %.4f  mean %.4f  p90 %.4f'%(
      len(s), st.median(s), st.mean(s), sorted(s)[9*len(s)//10]))
"
```

```
n=44304  median spread 0.0100  mean 0.0146  p90 0.0300
```

```
2.03 - 1.00(spread) - 2.00(fees) = -0.97 cents per trade
my +$98 to +$178  ->  roughly -$168.  The sign flips.
```

**It is still not settled either way.** The fee is price-dependent (0.02 at the
money, 0.01 at 0.15/0.85), near-impossible trades were bought cheap, and −2.03c is
an average across 17,335 trades at varying prices. Only the per-trade computation
with the **actual opposite ask** — `decision_no_ask`, never `1 − yes_ask` — decides
it.

**This reframes the reversal question.** It is no longer "reverse BTC and ETH,"
chosen after seeing coin-level results. It is "reverse the trades where the CF
target was unreachable" — 17,335 of them, identified by a rule with no tuned
parameters.

---

## 6. On tuning

**Tuning** = trying several values and keeping whichever makes the result look best.

I did it earlier in this project: after seeing all twelve coin-side cells I
highlighted SOL:NO and ETH:YES as "the two that survive." Nothing told you in
advance to pick those. The objection was correct.

**The near-impossible gate avoids it** — the threshold is derived, not chosen.

**But several parameters were set**, on principle rather than against PnL:

```
MAX_CF_AGE_S = 5           freshness limit
N_GATE = 300               minimum cell count
250 ms                     CF availability lag
the bin grid
horizons 120-870, step 15
```

**A sensitivity check on these is legitimate and would strengthen the result** — if
the conclusion holds at N_GATE 200 and 500, it is not resting on one arbitrary
choice. That differs from searching for the value that maximises PnL.

---

## 7. What is established, and what is not

**Established.** The gate removes 94.7% of losses at executable prices. It is built
on well-populated cells with no tuned threshold. The near-impossible rate tracks the
loss concentration across coins. Rows in equals rows out; 99.8% evaluable.

**Not established.** This is still the development period — the gate was not tuned,
but it was built and measured on the same 47,268 rows. **September 7–13 is the test,
run once, nothing changed afterward.** The profitable version depends on size, and
real size walks the book. The reversal is arithmetic on summary statistics, not yet
a per-trade computation.

---

## 8. Open questions

**Order of the next two.** Reversal first, or holdout first? Reversal is analysis on
data already held; the holdout is a one-shot resource. Reversal first produces a
better rule to test — but also means more decisions made before the holdout is
spent, which cuts the other way.

**Separate rule or modification of the gate?** "Skip near-impossible" and "buy the
other side of near-impossible" are different strategies with different capacity and
risk profiles. Pooling their results would let one mask the other.

**The 6 ABSENT cache hours and the 39 STALE_CF rows** should be named by coin and
date. Separate diagnostic pass, or folded into whatever runs next?

**Sensitivity on N_GATE, MAX_CF_AGE_S and the availability lag** — before the
holdout, or after?
