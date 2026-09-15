# Operational notes

Rules earned by losing time to them on this project. Each entry says what
happened, not just what to do.

---

## Chaining long jobs

**The process exit code is the success gate. Logs are for diagnosis.**

```bash
python3 -u first_stage.py ... > first_stage.log 2>&1 &
FIRST_PID=$!

if wait "$FIRST_PID"; then
    echo "FIRST STAGE SUCCEEDED"
else
    rc=$?
    echo "FIRST STAGE FAILED -- next stage not started"
    exit "$rc"
fi

python3 -u second_stage.py ...
```

### Never chain with `pgrep -f`

`pgrep -f` matches whole command lines, so a wrapper that waits on a pattern
contained in its own command line **matches itself and waits forever**:

```bash
# BROKEN: this bash -c command line contains the string "stage cache",
# so pgrep finds the waiter and it never exits.
nohup bash -c 'while pgrep -f "stage cache" >/dev/null; do sleep 60; done; ...' &
```

### Never gate on grepping a log for `error`, `abort`, `fail`

A healthy log frequently contains those words, because scripts document their
own failure handling. The CF reachability cache banner says it *"aborts on
download/schema errors"*, so a guard of the form

```bash
grep -qE "ERROR|Traceback|abort" ~/cf_reach_cache.log
```

matches that sentence on every clean run.

### What actually happened on 2026-09-14, and what is not known

The cache finished cleanly — 14,809 of 14,815 objects, `cache-only complete` —
and the chained analysis never started. Both mechanisms in the wrapper were
defective:

1. `pgrep -f "stage cache"` could match the wrapper's own command line and
   wait forever.
2. The log-word guard could reject a healthy run because `abort` appears in
   explanatory banner text.

**The available logs did not establish which defect determined that specific
failure.** The analysis log contained only `nohup: ignoring input` and no
`CACHE STAGE FAILED` message — which the grep guard would have printed had it
fired. Both defects are prohibited regardless. Use the child PID and its exit
code.

An overnight run was lost. The cache survived on disk, so the cost was hours
rather than work — but the guard added to make the chain safe is what stopped
it.

---

## Pin the exact run

Before starting a long job, record:

- Git commit
- SHA-256 of every executed script
- complete command and arguments
- input paths and input manifests
- output directory
- UTC start time
- child PID

Write them to `RUN_MANIFEST.json` or `RUN_MANIFEST.txt`.

A file changed on disk after launch does not prove the running process
changed. The pre-run hash and command are the evidence.

---

## Long commands and wrappers

Do not launch important multi-stage jobs from a long command pasted into chat.

Commit a short `run_<task>.sh` wrapper, review it, then run:

```bash
bash -n run_<task>.sh      # syntax check without executing
bash run_<task>.sh
```

The wrapper should write the PID, exit code, command, and timestamps to disk.

This removes the transcription errors and the terminal/chat round trips that
long pasted commands invite — and it is what makes the run manifest above
possible without extra effort.

---

## Reading many objects from GCS

**Use one reused in-process `google.cloud.storage.Client`. Never
`subprocess` + `gsutil` in a loop.**

Measured on this project, on the CF backfill:

```
gsutil stat        8.0    s per call
in-process client  0.038  s per call      208x
```

That difference took one job from an estimated 5.7 days to 3 hours. The lesson
was then re-learned twice: the strike table builder ran 220 sessions at 30s
each, 3.6% CPU, entirely waiting on subprocess startup.

Download each object once into a resumable local cache with a manifest
recording generation, size and local path. Validate gzip and schema **before**
accepting a cached object — a truncated file that is not opened until analysis
time is a silent data loss.

---

## Progress output on long jobs

Print roughly **every 1% of the work**, with elapsed and remaining. A 40-minute
job that prints every 10% emits a line every four minutes and looks hung; the
first instinct is to kill it.

Include the running counts of whatever can go wrong (`FETCHED`, `ABSENT`,
`usable`, `incomplete`), so a spreading problem is visible while it spreads
rather than at the end.

---

## Multiple commands in one message

When giving someone several commands, **say up front whether they are
alternatives or a sequence.** Unlabelled options get read as a sequence and run
in order.

---

## Checking work

**Measure the distribution; do not generalise from one observation.** Errors
made on this project by doing exactly that:

- concluded DOGE was not collected, from a BTC-only extract
- concluded the strike-coverage gap clustered at session boundaries; it is
  diurnal and ignores them
- concluded each market's strike is the previous market's settlement value,
  from one BTC coincidence — BTC moves 1.5 bps per market so the hypothesis is
  untestable there, and the wider sample does not support it
- concluded the settlement offset was `.200` from one market; it is `.000`,
  and `.200` only won because the filter gave `.000` sixty-one points instead
  of sixty
- concluded interpolation would work for BTC and fail for thin coins; it is
  the reverse

The check is usually one query. Run it.

---

## Validating an external source before trusting it

Before filling 5,765 gaps from the Kalshi API, fetch 300 records whose values
are already known from another source and require exact agreement. Any
disagreement stops the run.

This is cheap and it is the difference between recovering data and importing
someone else's error.

---

## Fail closed, but let one bad row be one bad row

Two failure modes, both real on this project:

**Aborting the whole run on one malformed row.** A repricing tool raised
`ValueError` on any row with invalid economics. Exactly one row in 63,570 had
`cost=1.0` — a real market state, an empty book side — and it would have killed
a seven-hour analysis. The fix is skip-and-count with the reason reported
alongside every other rejection category.

**Silently dropping rows.** A left join that quietly discards non-matching rows
turns a coverage gap into a selection bias nobody notices. Every input row
should produce exactly one output row, carrying an explicit status, and the
totals should reconcile: rows in equals rows out.

---

## Post-settlement data

`expiration_value` and `result` exist only after a market settles. They are
labels and validation. They must never reach a decision-time feature, and the
code should make that structurally impossible rather than relying on
discipline.

This trap was walked into twice in one session: once by proposing to select the
CF settlement offset per market against `expiration_value`, and once by
suggesting per-market verification of the same. Both would have made the label
depend on the answer.

A recovered **strike** is different and is legitimate: it was a fixed contract
parameter, published before the market traded, and available through the same
API at decision time.

---

## Histograms, not normalised distributions

Store integer counts on **one shared bin grid** across every cell. Counts add;
densities do not, unless you carry the weight alongside — at which point you
have stored the counts anyway with extra steps.

Per-cell bins are the same mistake moved somewhere less obvious: they make the
counts unaddable.

Fine cells can always be summed into coarse ones. A pooled distribution can
never be split. So bucket finely — coin, horizon, session, weekday — and decide
what to merge at read time, with an explicit minimum-count rule and a documented
fallback when a cell is too thin.

---

## Overlapping samples are not independent samples

Building a reference distribution from every 5 Hz window would have inflated
`n` by roughly 4,000x with autocorrelated observations, and every confidence
interval computed from it would have been meaningless.

One observation per completed market per horizon. The sample size should count
independent events, not ticks.
