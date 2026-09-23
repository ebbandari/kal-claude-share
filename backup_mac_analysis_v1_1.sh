#!/usr/bin/env bash
# Mac component of the two-source snapshot.
# Every SOURCE_DIR is REQUIRED. Relative paths are preserved beneath unique,
# indexed source labels. Writes MAC_COMPLETE.json only after full readback.
#
# Usage: ./backup_mac_analysis_v1_1.sh <SNAPSHOT_ID> <SOURCE_DIR> [SOURCE_DIR ...]
set -Eeuo pipefail
umask 077

SNAPSHOT_ID="${1:-${SNAPSHOT_ID:-}}"
if [[ -z "$SNAPSHOT_ID" ]]; then
  echo "usage: $0 <SNAPSHOT_ID> <SOURCE_DIR> [SOURCE_DIR ...]" >&2
  exit 64
fi
shift || true
if ! [[ "$SNAPSHOT_ID" =~ ^20[0-9]{6}T[0-9]{6}Z$ ]]; then
  echo "SNAPSHOT_ID must look like 20260922T190000Z; got: $SNAPSHOT_ID" >&2
  exit 64
fi
if [[ $# -lt 1 ]]; then
  echo "REFUSING: pass the exact Mac directories to back up; no guessed default is used." >&2
  echo "Example: $0 $SNAPSHOT_ID \"$HOME/path/to/project/analysis\"" >&2
  exit 64
fi
SOURCES=("$@")

BUCKET="gs://kalshi-data-vault-kalshi-collector-personal"
SNAP="${BUCKET}/analysis/POST_REACHABILITY/snapshots/${SNAPSHOT_ID}"
DEST="${SNAP}/mac_analysis"
PARENT="${BUCKET}/analysis/CF_REACHABILITY_V1_1/snapshots/20260916T005907Z"
STAGE="${HOME}/kalshi_snapshot_${SNAPSHOT_ID}_mac"
LOG="${HOME}/backup_mac_${SNAPSHOT_ID}.log"

if [[ -e "$STAGE" ]]; then
  echo "REFUSING: local stage already exists: $STAGE" >&2
  exit 2
fi
mkdir -p "$STAGE"/{content,provenance,repository}
exec > >(tee -a "$LOG") 2>&1
trap 'rc=$?; echo; echo "MAC BACKUP FAILED at line $LINENO (rc=$rc) — MAC_COMPLETE.json NOT written."; exit "$rc"' ERR

if ! gsutil -q stat "${PARENT}/SNAPSHOT_COMPLETE.json" 2>/dev/null; then
  echo "REFUSING: verified parent marker absent: ${PARENT}/SNAPSHOT_COMPLETE.json" >&2
  exit 3
fi
if gsutil ls "${DEST}/**" >/dev/null 2>&1 || \
   gsutil -q stat "${SNAP}/MAC_COMPLETE.json" 2>/dev/null; then
  echo "REFUSING: Mac destination/marker already exists under $SNAP" >&2
  exit 4
fi

# Every source named on the command line is required.
for src in "${SOURCES[@]}"; do
  [[ -d "$src" ]] || { echo "REQUIRED SOURCE NOT FOUND: $src" >&2; exit 5; }
done

# Refuse known analysis writers. Exclude this shell and its ancestors so
# the invocation command itself cannot create a false positive.
RUNNING="$(python3 - "$$" <<'PYGUARD'
import re, subprocess, sys
shell_pid = int(sys.argv[1])
pat = re.compile(r'cf_reversal|cf_velocity|cf_reachability|run_audition|pm_kalshi|edge_harness|derivative|pynumdiff|analyze_cf|v9_', re.I)
text = subprocess.check_output(['ps','-axo','pid=,ppid=,command='], text=True)
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
  echo "REFUSING: a likely analysis writer is active:"
  echo "$RUNNING"
  exit 6
fi

# On macOS, lsof is normally present. Refuse any file open for write/update
# beneath the requested source trees. This is stronger than name-based checks.
if command -v lsof >/dev/null 2>&1; then
  OPEN_WRITERS=""
  for src in "${SOURCES[@]}"; do
    hit="$(lsof +D "$src" 2>/dev/null | awk 'NR>1 && $4 ~ /^[0-9]+[wu]/ {print}' || true)"
    [[ -z "$hit" ]] || OPEN_WRITERS+=$'\n'"SOURCE: $src"$'\n'"$hit"
  done
  if [[ -n "$OPEN_WRITERS" ]]; then
    echo "REFUSING: files are open for write/update under requested sources:"
    echo "$OPEN_WRITERS"
    exit 7
  fi
else
  echo "WARNING: lsof unavailable; relying on process guard plus rsync stability check"
fi

echo "--- staging required sources ---"
: > "$STAGE/provenance/SOURCE_MAP.tsv"
i=0
for src in "${SOURCES[@]}"; do
  i=$((i+1))
  base="$(basename "$src" | tr -cs '[:alnum:]_.-' '_')"
  label="$(printf '%03d_%s' "$i" "${base:-source}")"
  target="$STAGE/content/$label"
  mkdir -p "$target"
  rsync -a --delete \
    --exclude '.DS_Store' --exclude '__pycache__/' --exclude '*.pyc' \
    --exclude '.ipynb_checkpoints/' \
    "$src/" "$target/"
  # Second checksum pass, then dry-run. Any remaining change means the source
  # moved during capture and the component must not claim completeness.
  rsync -a --delete --checksum \
    --exclude '.DS_Store' --exclude '__pycache__/' --exclude '*.pyc' \
    --exclude '.ipynb_checkpoints/' \
    "$src/" "$target/"
  CHANGES="$(rsync -a --delete --checksum --dry-run --itemize-changes \
    --exclude '.DS_Store' --exclude '__pycache__/' --exclude '*.pyc' \
    --exclude '.ipynb_checkpoints/' \
    "$src/" "$target/")"
  if [[ -n "$CHANGES" ]]; then
    echo "REFUSING: source changed during staging: $src"
    echo "$CHANGES" | head -50
    exit 8
  fi
  n="$(find "$target" -type f | wc -l | tr -d ' ')"
  b="$(du -sk "$target" | awk '{print $1*1024}')"
  printf '%s\t%s\t%s\t%s\n' "$label" "$src" "$n" "$b" >> "$STAGE/provenance/SOURCE_MAP.tsv"
  echo "  $label -> $n files, $b bytes"
done

python3 - "$STAGE/content" "$STAGE/provenance/MAC_INVENTORY.csv" <<'PY'
import csv, hashlib, sys, datetime as dt
from pathlib import Path
root,out=Path(sys.argv[1]),Path(sys.argv[2]); rows=[]
for p in sorted(root.rglob('*')):
    if not p.is_file(): continue
    h=hashlib.sha256()
    with p.open('rb') as fh:
        for blk in iter(lambda: fh.read(1<<20),b''): h.update(blk)
    rows.append(dict(path=p.relative_to(root).as_posix(),size_bytes=p.stat().st_size,
        suffix=p.suffix.lower(),modified_utc=dt.datetime.fromtimestamp(p.stat().st_mtime,dt.timezone.utc).isoformat(),sha256=h.hexdigest()))
if not rows: raise SystemExit('nothing staged')
with out.open('w',newline='',encoding='utf-8') as fh:
    w=csv.DictWriter(fh,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(f"mac inventory: {len(rows)} files")
PY

{
  echo "component=mac_analysis"
  echo "snapshot_id=$SNAPSHOT_ID"
  echo "parent_snapshot=$PARENT"
  echo "destination=$DEST"
  echo "hostname=$(hostname)"
  echo "user=$(id -un)"
  echo "kernel=$(uname -a)"
  echo "active_account=$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || true)"
  echo "project=$(gcloud config get-value project 2>/dev/null || true)"
  for s in "${SOURCES[@]}"; do echo "required_source=$s"; done
} > "$STAGE/provenance/RUN_METADATA.txt"
python3 --version > "$STAGE/provenance/PYTHON_VERSION.txt" 2>&1
python3 -m pip freeze > "$STAGE/provenance/PIP_FREEZE.txt" 2>&1 || true
df -h "$HOME" > "$STAGE/provenance/DISK_STATE.txt"
ps -axo pid,etime,%cpu,%mem,command > "$STAGE/provenance/PROCESS_STATE.txt"

if [[ -d "$HOME/kalshi-share/.git" ]]; then
  git -C "$HOME/kalshi-share" rev-parse HEAD > "$STAGE/repository/HEAD.txt"
  git -C "$HOME/kalshi-share" status --short > "$STAGE/repository/STATUS.txt"
  git -C "$HOME/kalshi-share" remote -v > "$STAGE/repository/REMOTES.txt"
  git -C "$HOME/kalshi-share" bundle create "$STAGE/repository/kalshi-share-mac.bundle" --all
  tar --exclude='.git' -czf "$STAGE/repository/kalshi-share-mac-worktree.tar.gz" -C "$HOME/kalshi-share" .
fi

python3 - "$STAGE" mac_analysis "$SNAPSHOT_ID" <<'PY'
import hashlib,json,socket,sys,datetime as dt
from pathlib import Path
root,component,sid=Path(sys.argv[1]),sys.argv[2],sys.argv[3]; man=root/'provenance'/'MANIFEST.json'; items=[]
for p in sorted(root.rglob('*')):
    if not p.is_file() or p==man: continue
    h=hashlib.sha256()
    with p.open('rb') as fh:
        for blk in iter(lambda:fh.read(1<<20),b''): h.update(blk)
    items.append(dict(path=p.relative_to(root).as_posix(),bytes=p.stat().st_size,sha256=h.hexdigest()))
if not items: raise SystemExit('empty stage')
p=dict(schema_version=1,component=component,snapshot_id=sid,source_hostname=socket.gethostname(),
       created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),file_count=len(items),
       total_bytes=sum(i['bytes'] for i in items),files=items)
man.write_text(json.dumps(p,indent=2,sort_keys=True)+'\n',encoding='utf-8')
print(f"manifest: {p['file_count']} files, {p['total_bytes']:,} bytes")
PY
MSHA="$(shasum -a 256 "$STAGE/provenance/MANIFEST.json" | awk '{print $1}')"

WT="${SNAP}/_write_test_mac_${SNAPSHOT_ID}.txt"
printf 'write-test-%s\n' "$SNAPSHOT_ID" | gsutil -q cp - "$WT"
[[ "$(gsutil cat "$WT")" == "write-test-${SNAPSHOT_ID}" ]] || { echo "WRITE TEST MISMATCH"; exit 9; }
gsutil -q rm "$WT"

gsutil -m rsync -r "$STAGE" "$DEST"
python3 - "$STAGE/provenance/MANIFEST.json" "$DEST" "$MSHA" <<'PY'
import hashlib,json,subprocess,sys
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
man,dest,msha=Path(sys.argv[1]),sys.argv[2].rstrip('/'),sys.argv[3]
checks=list(json.loads(man.read_text())['files']); checks.append(dict(path='provenance/MANIFEST.json',sha256=msha))
def one(it):
    p=subprocess.Popen(['gsutil','cat',f"{dest}/{it['path']}"],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    h=hashlib.sha256(); assert p.stdout is not None
    for blk in iter(lambda:p.stdout.read(1<<20),b''): h.update(blk)
    _,err=p.communicate(); return it['path'],p.returncode,it['sha256'],h.hexdigest(),err.decode('utf-8','replace')[:300]
bad=[];done=0;every=max(1,len(checks)//20)
with ThreadPoolExecutor(max_workers=4) as pool:
    for fut in as_completed([pool.submit(one,it) for it in checks]):
        path,rc,exp,got,err=fut.result();done+=1
        if rc!=0 or got!=exp: bad.append((path,rc,exp,got,err))
        if done%every==0 or done==len(checks): print(f"  verified {done}/{len(checks)}",flush=True)
if bad:
    for x in bad[:20]: print(x)
    raise SystemExit(10)
print(f"REMOTE SHA-256 VERIFICATION PASSED: {len(checks)} objects")
PY

python3 - "$STAGE" "$DEST" "$PARENT" "$SNAPSHOT_ID" "$MSHA" <<'PY'
import json,socket,sys,datetime as dt
from pathlib import Path
stage,dest,parent,sid,msha=Path(sys.argv[1]),*sys.argv[2:6]
m=json.loads((stage/'provenance'/'MANIFEST.json').read_text())
marker=dict(component='mac_analysis',snapshot_id=sid,destination=dest,parent_snapshot=parent,
 source_hostname=socket.gethostname(),completed_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
 manifest_sha256=msha,file_count=m['file_count'],total_bytes=m['total_bytes'],
 verified_by_remote_sha256_readback=True)
(stage/'MAC_COMPLETE.json').write_text(json.dumps(marker,indent=2,sort_keys=True)+'\n',encoding='utf-8')
PY
gsutil -q cp "$STAGE/MAC_COMPLETE.json" "${SNAP}/MAC_COMPLETE.json"
LOCAL_MARKER_SHA="$(shasum -a 256 "$STAGE/MAC_COMPLETE.json" | awk '{print $1}')"
REMOTE_MARKER_SHA="$(gsutil cat "${SNAP}/MAC_COMPLETE.json" | shasum -a 256 | awk '{print $1}')"
[[ "$LOCAL_MARKER_SHA" == "$REMOTE_MARKER_SHA" ]] || { echo "MAC marker readback mismatch"; exit 11; }

echo "MAC COMPONENT COMPLETE — not yet the full snapshot"
echo "SNAPSHOT_ID=$SNAPSHOT_ID"
echo "stage kept at $STAGE"
echo "log=$LOG"
