#!/usr/bin/env bash
# Probe2 component of a two-source, immutable Kalshi research snapshot.
# Writes PROBE2_COMPLETE.json only after every uploaded object has been
# downloaded again and SHA-256 verified. Never writes SNAPSHOT_COMPLETE.json.
#
# Usage: ./backup_probe2_v1_1.sh <SNAPSHOT_ID>
set -Eeuo pipefail
umask 077

SNAPSHOT_ID="${1:-${SNAPSHOT_ID:-}}"
if [[ -z "$SNAPSHOT_ID" ]]; then
  echo "usage: $0 <SNAPSHOT_ID>" >&2
  exit 64
fi
if ! [[ "$SNAPSHOT_ID" =~ ^20[0-9]{6}T[0-9]{6}Z$ ]]; then
  echo "SNAPSHOT_ID must look like 20260922T190000Z; got: $SNAPSHOT_ID" >&2
  exit 64
fi

BUCKET="gs://kalshi-data-vault-kalshi-collector-personal"
SNAP="${BUCKET}/analysis/POST_REACHABILITY/snapshots/${SNAPSHOT_ID}"
DEST="${SNAP}/probe2"
PARENT="${BUCKET}/analysis/CF_REACHABILITY_V1_1/snapshots/20260916T005907Z"
STAGE="${HOME}/kalshi_snapshot_${SNAPSHOT_ID}_probe2"
LOG="${HOME}/backup_probe2_${SNAPSHOT_ID}.log"

if [[ -e "$STAGE" ]]; then
  echo "REFUSING: local stage already exists: $STAGE" >&2
  echo "Use a new SNAPSHOT_ID or inspect/remove the stale stage deliberately." >&2
  exit 2
fi
mkdir -p "$STAGE"/{code,code/unrun,tests,results,logs,runners,docs,repository,provenance}
MISSING="$STAGE/provenance/MISSING_OPTIONAL.txt"
: > "$MISSING"

exec > >(tee -a "$LOG") 2>&1
trap 'rc=$?; echo; echo "PROBE2 BACKUP FAILED at line $LINENO (rc=$rc) — PROBE2_COMPLETE.json NOT written."; exit "$rc"' ERR

echo "============================================================"
echo "PROBE2 COMPONENT"
echo "============================================================"
echo "snapshot id  $SNAPSHOT_ID"
echo "destination  $DEST"
echo "parent       $PARENT"
echo

# The delta is not restorable without its parent. Require the verified parent.
if ! gsutil -q stat "${PARENT}/SNAPSHOT_COMPLETE.json" 2>/dev/null; then
  echo "REFUSING: verified parent marker is absent: ${PARENT}/SNAPSHOT_COMPLETE.json" >&2
  exit 3
fi

# Refuse while any known producer of copied analysis artifacts is active.
# Exclude this backup shell and all of its ancestors, so a parent command that
# merely mentions an analysis filename cannot trigger a false refusal.
RUNNING="$(python3 - "$$" <<'PYGUARD'
import re, subprocess, sys
shell_pid = int(sys.argv[1])
pat = re.compile(r'cf_reversal_counterfactual|cf_velocity_alignment|cf_reachability|analyze_cf_skip_only|run_audition|pm_kalshi_(leadlag|agreement)|build_strike_table|recover_strikes_from_api|build_opportunity_base|paper_bot_reprice_analysis|reconstruct_paper_bot_depth|live_replay_policy_study|edge_harness|v9_(column_screen|confirm|sweep)')
text = subprocess.check_output(['ps','-eo','pid=,ppid=,args='], text=True)
rows = []
parents = {}
for line in text.splitlines():
    parts = line.strip().split(None, 2)
    if len(parts) < 3:
        continue
    try:
        pid, ppid = int(parts[0]), int(parts[1])
    except ValueError:
        continue
    rows.append((pid, ppid, parts[2]))
    parents[pid] = ppid
anc = set()
cur = shell_pid
while cur and cur not in anc:
    anc.add(cur)
    cur = parents.get(cur, 0)
for pid, ppid, args in rows:
    if pid in anc:
        continue
    if pat.search(args):
        print(f'{pid:7d} {args}')
PYGUARD
)"
if [[ -n "$RUNNING" ]]; then
  echo "REFUSING: an analysis appears to be writing copied artifacts:"
  echo "$RUNNING"
  exit 4
fi
echo "writer-process guard: clear"

# Immutable prefix: neither component payload nor marker may already exist.
if gsutil ls "${DEST}/**" >/dev/null 2>&1 || \
   gsutil -q stat "${SNAP}/PROBE2_COMPLETE.json" 2>/dev/null; then
  echo "REFUSING: probe2 destination/marker already exists under $SNAP" >&2
  exit 5
fi

req() {
  local src="$HOME/$1" dst="$STAGE/$2"
  [[ -e "$src" ]] || { echo "REQUIRED ARTIFACT MISSING: $src" >&2; exit 6; }
  mkdir -p "$(dirname "$dst")"
  cp -a "$src" "$dst"
  echo "REQ   $1"
}
opt() {
  local src="$1" dst="$STAGE/$2"
  [[ -e "$src" ]] || { printf '%s\n' "$src" >> "$MISSING"; return 0; }
  mkdir -p "$(dirname "$dst")"
  cp -a "$src" "$dst"
  echo "opt   ${src#$HOME/}"
}

# Critical post-reachability artifacts.
echo "--- required artifacts ---"
req "cf_reversal_v2_1_20260916T031510Z"       "results/cf_reversal_v2_1_20260916T031510Z"
req "cf_reversal_v2_1_20260916T031510Z.log"   "logs/cf_reversal_v2_1_20260916T031510Z.log"
req "cf_velocity_alignment_half_skip_v1_1"    "results/cf_velocity_alignment_half_skip_v1_1"
req "cf_velocity_alignment_reachability_v1_1" "results/cf_velocity_alignment_reachability_v1_1"
req "cf_reversal_counterfactual_v2_1_gpt.py"  "code/cf_reversal_counterfactual_v2_1_gpt.py"
req "test_cf_reversal_counterfactual_v2_1_adversarial.py" "tests/test_cf_reversal_counterfactual_v2_1_adversarial.py"
req "cf_velocity_alignment_v1_1_gpt.py"       "code/cf_velocity_alignment_v1_1_gpt.py"
req "test_cf_velocity_alignment_v1_1_adversarial.py" "tests/test_cf_velocity_alignment_v1_1_adversarial.py"
req "run_reversal.sh" "runners/run_reversal.sh"
req "run_velocity.sh" "runners/run_velocity.sh"

# Reachability products; the 11 GB reproducible cache is deliberately excluded.
echo "--- reachability products (cache excluded; manifest retained) ---"
for f in CF_REACHABILITY_REPORT.md cf_reachability_summary.json \
         cf_reference_counts.csv.gz paper_bot_cf_reachability_rows.csv.gz; do
  opt "$HOME/cf_reach_v1_1/$f" "results/cf_reach_v1_1/$f"
done
opt "$HOME/cf_reach_v1_1/cf_cache/manifest.jsonl" "provenance/cf_cache_manifest.jsonl"

# Logs, diagnostics, and compact inputs.
echo "--- logs and diagnostics ---"
shopt -s nullglob
for f in "$HOME"/velocity_*.log "$HOME"/run_velocity_wrapper.log \
         "$HOME"/cf_reach_v1_1.log "$HOME"/cf_reach_cache.log \
         "$HOME"/cf_dup_audit.log "$HOME"/strike_table.log \
         "$HOME"/strike_recovery.log "$HOME"/run_reversal_wrapper.log; do
  opt "$f" "logs/$(basename "$f")"
done
for f in "$HOME"/cf_reversal_q25_diagnostic.txt "$HOME"/cf_velocity_alignment*.txt \
         "$HOME"/settlement_offset_verification.csv "$HOME"/strike_metadata/*; do
  opt "$f" "results/$(basename "$f")"
done
for d in strike_table strike_recovery_v1_1; do
  [[ -d "$HOME/$d" ]] && opt "$HOME/$d" "results/$d"
done
for f in "$HOME"/paper_bot_opportunity_base.csv "$HOME"/paper_bot_reprice_v1_2_*.csv \
         "$HOME"/paper_bot_depth_*.csv; do
  opt "$f" "results/inputs/$(basename "$f")"
done
shopt -u nullglob

# Preserve paths so same-named documents cannot overwrite one another.
copy_named_docs() {
  local label="$1" root="$2" depth="$3"
  [[ -d "$root" ]] || return 0
  while IFS= read -r -d '' f; do
    [[ "$f" == "$STAGE"* ]] && continue
    local rel="${f#$root/}"
    [[ "$f" == "$root" ]] && rel="$(basename "$f")"
    opt "$f" "docs/$label/$rel"
  done < <(find "$root" -maxdepth "$depth" -type f \
    \( -name 'CURRENT_STATE*.md' -o -name 'CROSS_VENUE_STUDY_STATE*.md' \
       -o -name 'OPERATIONAL_NOTES.md' -o -name 'TRANSITION_REPORT*.md' \
       -o -name 'CF_REACHABILITY_V1_1_FULL_RESULTS_AND_FOLLOWUP.md' \
       -o -iname 'Perplexity*algorithm*differentiat*.md' \
       -o -iname 'Perplexity_noisy_signal_derivatives*.py' \
       -o -iname '*grok*.md' -o -iname '*grok*.py' \) -print0 2>/dev/null)
}
copy_named_docs home "$HOME" 1
copy_named_docs analysis "$HOME/analysis" 6
copy_named_docs kalshi-share "$HOME/kalshi-share" 6

# Complete top-level program sweep.
echo "--- complete top-level program sweep ---"
KNOWN_RUN="cf_reachability_v1_1_gpt.py cf_reversal_counterfactual_v2_1_gpt.py
cf_velocity_alignment_v1_1_gpt.py build_strike_table_v1_1_gpt.py
recover_strikes_from_api_v1_1_gpt.py build_opportunity_base_v1_1_gpt.py
verify_settlement_offset_v1_1_gpt.py paper_bot_reprice_analysis_v1_2.py
reconstruct_paper_bot_depth_v2_gpt.py extract_strike_metadata.py
capacity_sized_pnl.py live_replay_policy_study_v1_1.py"
shopt -s nullglob
for f in "$HOME"/*.py "$HOME"/*.sh "$HOME"/*.zip; do
  b="$(basename "$f")"
  case "$b" in
    test_*)         opt "$f" "tests/$b" ;;
    run_*|backup_*) opt "$f" "runners/$b" ;;
    *) if grep -qw -- "$b" <<<"$KNOWN_RUN"; then opt "$f" "code/$b"
       else opt "$f" "code/unrun/$b"; fi ;;
  esac
done
shopt -u nullglob

python3 - "$HOME" "$STAGE/provenance/PROGRAM_INVENTORY.csv" "$KNOWN_RUN" <<'PY'
import csv, hashlib, sys, datetime as dt
from pathlib import Path
home, out, known = Path(sys.argv[1]), Path(sys.argv[2]), set(sys.argv[3].split())
rows = []
for p in sorted(home.iterdir()):
    if not p.is_file() or p.suffix.lower() not in {'.py', '.sh', '.zip'}:
        continue
    h = hashlib.sha256()
    with p.open('rb') as fh:
        for blk in iter(lambda: fh.read(1 << 20), b''):
            h.update(blk)
    rows.append(dict(path=p.name, size_bytes=p.stat().st_size,
        modified_utc=dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc).isoformat(),
        sha256=h.hexdigest(), known_status=('RUN' if p.name in known else 'UNKNOWN')))
if not rows:
    raise SystemExit('no top-level programs found')
with out.open('w', newline='', encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(f"program inventory: {len(rows)} files; {sum(r['known_status']=='UNKNOWN' for r in rows)} UNKNOWN")
PY

# Environment and repository capture.
{
  echo "component=probe2"
  echo "snapshot_id=$SNAPSHOT_ID"
  echo "parent_snapshot=$PARENT"
  echo "destination=$DEST"
  echo "hostname=$(hostname)"
  echo "user=$(id -un)"
  echo "kernel=$(uname -a)"
  echo "active_account=$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || true)"
  echo "project=$(gcloud config get-value project 2>/dev/null || true)"
  echo "sealed_holdout=2026-09-07..2026-09-13"
} > "$STAGE/provenance/RUN_METADATA.txt"
echo "$PARENT" > "$STAGE/provenance/PARENT_SNAPSHOT.txt"
python3 --version > "$STAGE/provenance/PYTHON_VERSION.txt" 2>&1
python3 -m pip freeze > "$STAGE/provenance/PIP_FREEZE.txt" 2>&1 || true
df -h "$HOME" > "$STAGE/provenance/DISK_STATE.txt"
ps -eo pid,etime,%cpu,%mem,args > "$STAGE/provenance/PROCESS_STATE.txt"

if [[ -d "$HOME/kalshi-share/.git" ]]; then
  git -C "$HOME/kalshi-share" rev-parse HEAD > "$STAGE/repository/HEAD.txt"
  git -C "$HOME/kalshi-share" status --short > "$STAGE/repository/STATUS.txt"
  git -C "$HOME/kalshi-share" remote -v > "$STAGE/repository/REMOTES.txt"
  git -C "$HOME/kalshi-share" log --oneline -40 > "$STAGE/repository/RECENT_COMMITS.txt"
  git -C "$HOME/kalshi-share" bundle create "$STAGE/repository/kalshi-share.bundle" --all \
    || echo "git bundle failed" >> "$MISSING"
  tar --exclude='.git' -czf "$STAGE/repository/kalshi-share-worktree.tar.gz" \
      -C "$HOME/kalshi-share" .
else
  echo "$HOME/kalshi-share (no git repo on probe2)" >> "$MISSING"
fi

# Local cryptographic manifest.
python3 - "$STAGE" probe2 "$SNAPSHOT_ID" <<'PY'
import hashlib, json, socket, sys, datetime as dt
from pathlib import Path
root, component, sid = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
man = root/'provenance'/'MANIFEST.json'
items = []
for p in sorted(root.rglob('*')):
    if not p.is_file() or p == man:
        continue
    h = hashlib.sha256()
    with p.open('rb') as fh:
        for blk in iter(lambda: fh.read(1 << 20), b''):
            h.update(blk)
    items.append(dict(path=p.relative_to(root).as_posix(), bytes=p.stat().st_size, sha256=h.hexdigest()))
if not items:
    raise SystemExit('empty stage')
payload = dict(schema_version=1, component=component, snapshot_id=sid,
    source_hostname=socket.gethostname(), created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
    file_count=len(items), total_bytes=sum(i['bytes'] for i in items), files=items)
man.write_text(json.dumps(payload, indent=2, sort_keys=True)+'\n', encoding='utf-8')
print(f"manifest: {payload['file_count']} files, {payload['total_bytes']:,} bytes")
PY
MSHA="$(sha256sum "$STAGE/provenance/MANIFEST.json" | awk '{print $1}')"
echo "manifest sha256: $MSHA"

# Prove write access before the payload.
WT="${SNAP}/_write_test_probe2_${SNAPSHOT_ID}.txt"
printf 'write-test-%s\n' "$SNAPSHOT_ID" | gsutil -q cp - "$WT"
[[ "$(gsutil cat "$WT")" == "write-test-${SNAPSHOT_ID}" ]] || { echo "WRITE TEST MISMATCH"; exit 7; }
gsutil -q rm "$WT"
echo "GCS write/read/delete test: PASS"

# Upload and SHA-256 read every object back.
gsutil -m rsync -r "$STAGE" "$DEST"
python3 - "$STAGE/provenance/MANIFEST.json" "$DEST" "$MSHA" <<'PY'
import hashlib, json, subprocess, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
man, dest, msha = Path(sys.argv[1]), sys.argv[2].rstrip('/'), sys.argv[3]
checks = list(json.loads(man.read_text())['files'])
checks.append(dict(path='provenance/MANIFEST.json', sha256=msha))
def one(it):
    p = subprocess.Popen(['gsutil','cat',f"{dest}/{it['path']}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    h = hashlib.sha256()
    assert p.stdout is not None
    for blk in iter(lambda: p.stdout.read(1 << 20), b''):
        h.update(blk)
    _, err = p.communicate()
    return it['path'], p.returncode, it['sha256'], h.hexdigest(), err.decode('utf-8','replace')[:300]
bad=[]; done=0; every=max(1,len(checks)//20)
with ThreadPoolExecutor(max_workers=4) as pool:
    futures=[pool.submit(one,it) for it in checks]
    for fut in as_completed(futures):
        path,rc,exp,got,err=fut.result(); done+=1
        if rc != 0 or got != exp: bad.append((path,rc,exp,got,err))
        if done % every == 0 or done == len(checks): print(f"  verified {done}/{len(checks)}", flush=True)
if bad:
    print('REMOTE VERIFICATION FAILED')
    for path,rc,exp,got,err in bad[:20]:
        print(f"  {path}\n    rc {rc}\n    expected {exp}\n    got {got}\n    stderr {err}")
    raise SystemExit(8)
print(f"REMOTE SHA-256 VERIFICATION PASSED: {len(checks)} objects")
PY

# Component marker written at snapshot root only after readback verification.
python3 - "$STAGE" "$DEST" "$PARENT" "$SNAPSHOT_ID" "$MSHA" <<'PY'
import json, socket, sys, datetime as dt
from pathlib import Path
stage, dest, parent, sid, msha = Path(sys.argv[1]), *sys.argv[2:6]
m=json.loads((stage/'provenance'/'MANIFEST.json').read_text())
marker=dict(component='probe2', snapshot_id=sid, destination=dest, parent_snapshot=parent,
    source_hostname=socket.gethostname(), completed_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
    manifest_sha256=msha, file_count=m['file_count'], total_bytes=m['total_bytes'],
    verified_by_remote_sha256_readback=True)
(stage/'PROBE2_COMPLETE.json').write_text(json.dumps(marker,indent=2,sort_keys=True)+'\n',encoding='utf-8')
PY
gsutil -q cp "$STAGE/PROBE2_COMPLETE.json" "${SNAP}/PROBE2_COMPLETE.json"
LOCAL_MARKER_SHA="$(sha256sum "$STAGE/PROBE2_COMPLETE.json" | awk '{print $1}')"
REMOTE_MARKER_SHA="$(gsutil cat "${SNAP}/PROBE2_COMPLETE.json" | sha256sum | awk '{print $1}')"
[[ "$LOCAL_MARKER_SHA" == "$REMOTE_MARKER_SHA" ]] || { echo "PROBE2 marker readback mismatch"; exit 9; }

echo "=== PROBE2_COMPLETE.json ==="
gsutil cat "${SNAP}/PROBE2_COMPLETE.json"
echo "=== OPTIONAL ARTIFACTS NOT FOUND ==="
cat "$MISSING"
echo "PROBE2 COMPONENT COMPLETE — not yet the full snapshot"
echo "SNAPSHOT_ID=$SNAPSHOT_ID"
echo "stage kept at $STAGE"
echo "log=$LOG"
