## Ruling

**Do not run the submitted `cf_reachability_v1.py` overnight.** Its central formulation is right—it models the final-minute CF average rather than a point-price touch, and it correctly intends one historical observation per market and horizon.  But the production implementation had several answer-changing defects.

The most serious were:

* It accepted settlement windows with only **55 of 60 observations**, and its timestamp test could admit `.999` prints as though they were the `.000` settlement series. 
* It never actually used the intended May 27–July 14 prehistory; its historical universe came only from the later strike table. 
* It assigned the same session and weekday to all 51 horizons instead of using each horizon’s own decision time.
* Cells with only 100–299 reference observations were labeled “descriptive,” but could still trigger `near_impossible` and remove trades.  
* A missing displayed executable ask silently fell back to the optimistic logged paper-bot price. 
* Unknown sides failed open as YES; zero size became one contract.
* The sealed holdout was not refused when opportunities were loaded.
* Source-timestamped CF observations were treated as instantly available, without a latency allowance.
* Realized volatility, volatility-normalized distance, and the requested “how far does it clear K, with error bars?” outputs were absent.

## Reviewed replacement

[Download the complete CF Reachability v1.1 package](sandbox:/mnt/data/CF_REACHABILITY_V1_1_GPT.zip)

Individual files:

* [Reviewed analyzer](sandbox:/mnt/data/CF_REACHABILITY_V1_1_GPT/cf_reachability_v1_1_gpt.py)
* [Adversarial tests](sandbox:/mnt/data/CF_REACHABILITY_V1_1_GPT/test_cf_reachability_v1_1_adversarial.py)
* [Probe2 run instructions](sandbox:/mnt/data/CF_REACHABILITY_V1_1_GPT/README_FIRST.md)
* [Audit and changes](sandbox:/mnt/data/CF_REACHABILITY_V1_1_GPT/AUDIT_AND_CHANGES.md)
* [Short message for Claude/Opus](sandbox:/mnt/data/CF_REACHABILITY_V1_1_GPT/MESSAGE_TO_CLAUDE.md)
* [Test transcript](sandbox:/mnt/data/CF_REACHABILITY_V1_1_GPT/TEST_TRANSCRIPT.txt)

ZIP SHA-256:

```text
dd660af0db1ac45798b07d2428850da6f4a052d6f4f623c17d7ba69192b06dc7
```

## What v1.1 now does

* Uses the intended **seven-week causal prehistory**, starting May 27.
* Requires exactly **60 unique `.000` observations in `[T−60,T)`**.
* Uses cached Kalshi API `expiration_value` as label truth where available, isolated from all decision-time features.
* Reports API-versus-CF reconstruction error.
* Applies a configurable **250 ms CF availability lag** so a source print is not used before it could have arrived.
* Uses horizon-specific session, weekday, and ISO week.
* Stores additive weekly histogram counts for later regrouping.
* Requires **300 observations** before any trading gate can fire.
* Uses Wilson plus **day-clustered** uncertainty, conservatively.
* Uses only the reconstructed executable ask—never the logged price fallback.
* Emits velocity, realized volatility, acceleration, data-gap diagnostics, and volatility-normalized distance.
* Emits predicted final-average quantiles and DKW uncertainty bands:

  ```text
  pred_final_avg_p10
  pred_final_avg_p50
  pred_final_avg_p90
  side_clearance_vs_K_q50_bps
  side_clearance_vs_K_q90_bps
  side_projected_clearance_native_q90
  ```

  These directly address your request to see how far the projected settlement average clears or misses \(K\), and how reliable that estimate is.
* Preserves every input opportunity with an explicit status.
* Refuses any September 7–13 opportunity.
* Uses one in-process `google.cloud.storage.Client`; no `gsutil` or subprocess loop.

## Verification

```text
Compilation:                    PASS
Embedded tests:                 ALL PASS
Independent adversarial tests:  46 passed, 0 failed
Synthetic end-to-end run:       PASS
Rows-in = rows-out:             PASS
Clean ZIP extraction:           PASS
```

I did not run it against Probe2 or the real GCS bucket from this environment. The real-data cache and overnight run remain the final operational gate.

## Probe2 order

First:

```bash
python3 ~/cf_reachability_v1_1_gpt.py --self-test
python3 ~/test_cf_reachability_v1_1_adversarial.py
```

Then run the cache-only stage. After that succeeds, run the full analysis with `--no-fetch`. The exact commands are in `README_FIRST.md`.
