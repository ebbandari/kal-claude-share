You are right. **My previous list mixed two scopes**:

* Sections **0–10** were the near-term execution plan, centered on tonight and the next work session.
* Sections **11–14** were later stages of the overall paper-bot program.

And yes, the **Grok code audit absolutely belongs on the overall list**. I left it in “parked work,” but that made it sound optional or remote. It is neither.

## Tonight, or the next immediate work block

Do only as much of this as energy allows:

1. **Create the second verified backup snapshot**

   * Include `CF_REACHABILITY_V1_1_FULL_RESULTS_AND_FOLLOWUP.md`.
   * Do not modify the first immutable snapshot.

2. **Have Claude write the exact size-aware reversal program**

   * Join the reachability rows to the actual opposite-side arrival ask and capacity.
   * Test:

     * original contrarian;
     * CF skip-only;
     * reverse-all-near-impossible diagnostic;
     * conservative CF-conditioned reversal.
   * Evaluate:

     * one contract;
     * half displayed touch, capped at 25;
     * full displayed touch, capped at 25.

3. **Give me the code before trusting its result**

   * I run the adversarial review.
   * Then it runs on the July 15–September 6 development data.

That is enough for tonight. Do not begin PRIM, WRAcc, EBM, or the holdout tonight unless the reversal analysis somehow finishes cleanly and effortlessly.

---

# Overall active roadmap

## Phase A — Finish the current paper-bot rule analysis

### A1. Current CF result and backup

Status:

* CF reachability run complete.
* Row reconciliation complete.
* First immutable GCS snapshot verified.
* Second snapshot needed only to add the full follow-up report.

The CF gate removed most historical losses, but the kept one-contract strategy was still slightly negative. 

### A2. Exact size-aware CF-conditioned reversal

This is the immediate substantive task.

Compare:

```text
original contrarian
CF skip-only
CF reverse-all diagnostic
CF conservative conditional reversal
```

At:

```text
1 contract
half-touch, capped at 25
full-touch, capped at 25
```

Use actual opposite-side arrival asks, exact order fees, and displayed capacity.

### A3. Basic distributions and graphics

Before mining rules, produce:

```text
P&L distributions
equity curves
maximum drawdown
gains and losses separately
coin × side
hour × weekday
STC bands
CF probability edge
q50/q90 clearance
spread
capacity
feed_stale
```

This is where we determine whether reversal or skip-only success is broad or concentrated in a small corner.

### A4. Supported-versus-unsupported execution analysis

Still outstanding.

Match rows on:

```text
coin
side
entry-price band
STC band
possibly CF probability/clearance band
```

Determine whether execution-supported rows are inherently worse, or whether archive coverage created a selection artifact.

### A5. Fixed PM-confirm diagnostic

Run the original fixed PM threshold once:

```text
PM confirms
PM does not confirm
PM unavailable
```

No threshold search. Report by chronology, coin, side, bursts, and executable P&L.

### A6. Coinbase normalization

Preserve:

```text
cb_confirm_raw_usd
```

and derive:

```text
cb_confirm_bps_vs_strike
```

Do not treat the old raw-dollar threshold as comparable across coins.

---

## Phase B — Audit Grok’s work independently

This should happen **after the exact reversal table exists**, but before we ask Grok to expand the project.

### B1. Obtain and freeze Grok’s exact package

Preserve:

```text
all source code
all 26 tests
requirements/environment
README and run commands
test transcript
real-window outputs
assumptions and unresolved issues
```

No new Grok development until the delivered version is frozen.

### B2. Adversarially audit Grok’s pipeline

Focus on:

```text
strike extraction
comparator handling
final-minute [T−60,T) boundaries
exact .000 settlement series
duplicates and amendments
301-versus-300/60-print mistakes
receive-time causality
future leakage
HYPE missing Coinbase
API expiration_value used only as label/validation
row reconciliation
chronological split correctness
```

We should independently reproduce its reported 26 passing tests and add hostile cases it did not anticipate.

### B3. Return defects to Grok

Give Grok a concise defect list and ask it to correct its own code and rerun its tests.

Then re-audit only the corrected areas plus regression coverage.

### B4. Decide what from Grok is worth incorporating

Compare Grok’s implementation with our independently developed CF reachability work.

Potential useful additions:

```text
probability calibration
alternative interpretable baseline
feature construction
purged/embargoed evaluation framework
book slope/curvature definitions
cross-venue alignment
```

Do not replace our working pipeline merely because Grok’s code looks more sophisticated.

---

## Phase C — Structured subgroup discovery

Only after the unified action table exists.

### C1. Economic WRAcc

Rank subgroups by:

$$
\text{coverage}\times
\left(
E[\text{executable exact-fee P\&L}\mid S]
-
E[\text{parent P\&L}]
\right)
$$

Support must include:

```text
rows
bursts
markets
days
```

### C2. PRIM

Find at most one or two dense, interpretable regions where:

```text
contrarian works
reversal works
or both should be skipped
```

No tiny lucky boxes.

### C3. EBM or GAM

Use it primarily for understanding smooth structure:

```text
probability edge
CF clearance
STC
spread
capacity
velocity
volatility
session
feed_stale
PM confirmation
```

This helps distinguish true nonlinear structure from arbitrary rectangular thresholds.

### C4. RuleFit and CORELS later

Only after the feature set is reduced.

RuleFit previously generated a substantial apparent lift in a null lane, so future lift must be judged relative to that null—not relative to zero. 

---

## Phase D — Freeze candidate policies

Freeze no more than three:

```text
1. CF skip-only
2. CF conditional reversal
3. at most one PRIM/WRAcc/EBM-derived refinement
```

Specify completely:

```text
entry condition
side
quantity rule
price rule
fee rule
capacity requirement
CF staleness limit
N_GATE
availability lag
eligible coins
all skip conditions
```

No further interpretation after freezing.

---

## Phase E — Robustness and final holdout

### E1. Sensitivity, not optimization

Test a small predeclared grid for:

```text
N_GATE
CF freshness
availability lag
half-touch versus full-touch sizing
```

We are checking fragility, not selecting whichever produces the largest P&L.

### E2. Statistical comparison

For the small frozen policy set:

```text
clustered uncertainty
Hansen SPA
Model Confidence Set
Deflated Sharpe Ratio
```

Use bursts/markets/days as appropriate—not raw rows as if independent.

### E3. Spend September 7–13 once

The 5,205 Probe2-only trades remain sealed.

Run the frozen policies once. Report every result, not only winning cells.

Then issue:

```text
GO TO REPAIRED SHADOW
CONDITIONAL / MORE FORWARD DATA
NO-GO
```

No threshold edits after seeing the holdout.

---

## Phase F — Only if the holdout supports continuation

Then:

```text
repair the paper bot’s persistent state
add restart-safe GCS checkpoints
implement lean live/shadow execution
install hard risk controls
run forward shadow
possibly begin tiny live size
```

No direct jump from development-period analysis to material live money.

---

## Later research queue

These remain important, but not before the paper-bot ruling:

1. Your handwritten TimesFM 3 / Chronos-2 notes.
2. TimesFM 3, Chronos-2, TiRex/Toto comparisons.
3. Fine-tuning and models trained from scratch.
4. Larger 16/24–32 channel designs.
5. Full slope/ladder repair and completion.
6. Explorer research track.

## The clean immediate instruction for Claude

> Tonight, finish the second backup and write the exact size-aware CF-conditioned reversal program with its adversarial tests. Do not start statistical mining, Grok expansion, or the holdout. After GPT reviews the reversal code, run it on development data. The Grok package audit is the next major independent workstream after the reversal table is complete.
