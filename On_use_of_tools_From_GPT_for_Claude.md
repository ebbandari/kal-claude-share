Yes. **This is now the right point to use PRIM, WRAcc, and an interpretable nonlinear model—but not to throw every rule miner at all 190 columns.**

The CF run produced unusually rich material: row-level probabilities, several uncertainty intervals, empirical move quantiles, projected final-average values, clearance over/under \(K\), exact and scalable P&L, plus additive integer histograms.  The gate itself was highly discriminative: it removed 40% of eligible trades but captured 94.7% of the exact-fee losses, even though the surviving one-contract strategy remained slightly negative. 

That is enough for **structured subgroup discovery**.

## One correction to Opus’s report first

The report is valuable and belongs in GitHub. Suggested name:

```text
CF_REACHABILITY_V1_1_FULL_RESULTS_AND_FOLLOWUP.md
```

But its rough reversal arithmetic is too optimistic.

It estimates reversal as:

```text
2.03¢ recovered loss − spread
```

The exact relationship is:

$$
\operatorname{PnL}_{opp}
=
-\operatorname{PnL}_{current}
-
(\text{YES ask}+\text{NO ask}-1)
-
f_{current}
-
f_{opp}
$$

So reversal pays:

```text
negative of original P&L
minus the spread
minus both sides’ fees
```

At one-contract rounded fees, that likely changes the sign of many supposed reversal gains. The report correctly says the final answer requires each trade’s actual opposite-side ask, but its summary estimates of approximately +$98 to +$178 omit the fee toll. 

Therefore, the **exact per-trade reversal counterfactual** should be computed before treating reversal as promising.

## Which techniques are ready now?

| Technique                        | Use now?                           | Best job here                                                            |
| -------------------------------- | ---------------------------------- | ------------------------------------------------------------------------ |
| **WRAcc**                        | **Yes**                            | Rank broad subgroups by coverage × economic improvement                  |
| **PRIM**                         | **Yes**                            | Find one or two dense, interpretable “boxes” where expected P&L improves |
| **EBM / GAM**                    | **Yes, after the first summaries** | Show smooth nonlinear shapes and a few interactions                      |
| **RuleFit / generic RuleFinder** | **Later**                          | Sparse combination of a shortlisted feature set                          |
| **CORELS**                       | **Later**                          | Produce a compact deployable rule list after discretization              |
| **SPA / MCS / DSR**              | **Final validation**               | Compare a small frozen set of candidate policies                         |

### WRAcc: useful immediately

Classic WRAcc uses coverage times lift in a binary target. Here I would use an **economic version**:

$$
\text{Economic WRAcc}(S)
=
P(S)\left(
E[\text{exact ask PnL}\mid S]
-
E[\text{exact ask PnL}]
\right)
$$

That prevents a tiny lucky corner from ranking highly and avoids optimizing win rate while ignoring entry price and fees.

Report for every subgroup:

```text
rows
distinct markets
distinct bursts
distinct days
coverage
exact-fee P&L per trade
scalable-fee P&L per trade
incremental lift over parent
drawdown
chronological first-half / second-half result
```

### PRIM: also ready

PRIM is well suited to questions such as:

> In which dense region of CF probability, clearance, STC, price, session, and volatility does the kept strategy become economically positive?

But constrain it:

* one primary target: **executable-ask exact-fee P&L**;
* no tiny boxes;
* minimum support based on distinct markets/bursts and days, not raw opportunity count;
* at most one or two boxes retained;
* chronological stability required;
* no use of September 7–13.

The 47,268 rows are not 47,268 independent experiments. Opportunities within a market or trigger burst are correlated. The operating notes correctly insist that sample size count independent events, not overlapping observations. 

### EBM/GAM: probably the best tool for understanding

An Explainable Boosting Machine or GAM can answer:

* Does value improve smoothly as `edge_p` becomes positive?
* Is there a sharp threshold or a gradual curve?
* How does STC interact with required move?
* Does session still matter after velocity and volatility are included?
* Do DOGE/XRP behave differently only because their required moves and executable prices differ?

This is more informative than immediately generating hundreds of rectangular rules.

I would apply monotonicity where economically justified—for example, larger conservative probability edge should not systematically reduce predicted value—while allowing non-monotonic structure in velocity, volatility, session, and entry price.

## What should enter the first discovery pass?

Not all 190 columns. Start with roughly 12–18 economically distinct variables:

```text
p_win_hat - break_even_p
p_win_ci_hi_conservative - break_even_p
required_final_average_move_bps
side_clearance_vs_K_q50_bps
side_clearance_vs_K_q90_bps
reference_move_p50 / p90 / p95
reference_n
reference_dkw_epsilon
STC / reference horizon
CF velocity: 15s, 30s, 60s, 120s
CF realized volatility: 30s, 60s, 120s
executable ask
spread
capacity at touch
coin
purchased side
session
weekday
pm_confirm
cb_confirm_bps_vs_strike
```

Avoid giving the model seven near-duplicate quantiles or six strongly correlated velocity measurements without grouping them.

## Histograms versus the row table

Keep the histograms exactly as they are: they are the **probability engine**. Their shared-grid integer counts are additive and the reference cells are well populated—minimum 300, median 1,329. 

But run PRIM, WRAcc, and EBM on:

```text
paper_bot_cf_reachability_rows.csv.gz
```

Do **not** mine `cf_reference_counts.csv.gz` as if histogram cells were independent trades. That would mix the reference model with the trading outcomes and could double-count the same historical markets across horizons and cells.

## Why RuleFit should wait

Your instinct is right: **RuleFit is premature.**

In the earlier control work, RuleFit reported roughly **+1.87¢ of apparent lift even on the null lane**, and the +1.5¢ planted signal was almost indistinguishable from nothing. Any future RuleFit result must therefore be read relative to its null-lane lift, not relative to zero. 

RuleFit becomes reasonable only after WRAcc, PRIM, and EBM reduce the candidate space to perhaps 10–20 features and a handful of interactions.

## The bounded plan

1. **Exact reversal counterfactual**, using the actual opposite ask and its exact fee.
2. **Calibration and distribution graphics**:

   * actual win frequency versus `p_win_hat`;
   * exact P&L versus probability edge;
   * exact P&L versus q50/q90 clearance;
   * coin/side/session/STC heatmaps;
   * gains and losses separately.
3. **WRAcc ranking** on the fixed feature/bucket registry.
4. **PRIM**, limited to one or two dense boxes.
5. **EBM/GAM** to understand smooth effects and verify whether the boxes reflect real structure.
6. Freeze no more than **three policies**:

   * skip-only CF gate;
   * exact CF-conditioned reversal;
   * at most one discovered refinement.
7. Apply SPA/MCS/DSR or clustered walk-forward comparisons to those frozen policies.
8. Spend September 7–13 once.

So the answer is **yes**: these techniques can materially improve understanding and may find a better rule. But the best immediate trio is:

```text
WRAcc + PRIM + EBM/GAM
```

not an unrestricted RuleFit/CORELS search. The CF gate has already reduced the problem from “find an edge among everything” to the much tighter question:

> **Which prospective, dense, stable conditions turn the nearly break-even kept set into positive executable P&L?**
