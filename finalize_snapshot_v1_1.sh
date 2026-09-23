#!/usr/bin/env bash
# Finalizes the two-source snapshot only after validating marker contents,
# manifest contents, and every remote payload object a second time.
# Usage: ./finalize_snapshot_v1_1.sh <SNAPSHOT_ID>
set -Eeuo pipefail
umask 077
SNAPSHOT_ID="${1:-${SNAPSHOT_ID:-}}"
if [[ -z "$SNAPSHOT_ID" ]]; then echo "usage: $0 <SNAPSHOT_ID>" >&2; exit 64; fi
if ! [[ "$SNAPSHOT_ID" =~ ^20[0-9]{6}T[0-9]{6}Z$ ]]; then
  echo "SNAPSHOT_ID must look like 20260922T190000Z; got: $SNAPSHOT_ID" >&2; exit 64
fi
BUCKET="gs://kalshi-data-vault-kalshi-collector-personal"
SNAP="${BUCKET}/analysis/POST_REACHABILITY/snapshots/${SNAPSHOT_ID}"
PARENT="${BUCKET}/analysis/CF_REACHABILITY_V1_1/snapshots/20260916T005907Z"
WORK="$(mktemp -d "${HOME}/kalshi_finalize_${SNAPSHOT_ID}.XXXXXX")"
LOG="${HOME}/finalize_${SNAPSHOT_ID}.log"
exec > >(tee -a "$LOG") 2>&1

sha256_file() {
  python3 - "$1" <<'PYHASH'
import hashlib, sys
h=hashlib.sha256()
with open(sys.argv[1], 'rb') as f:
    for b in iter(lambda:f.read(1<<20), b''):
        h.update(b)
print(h.hexdigest())
PYHASH
}
sha256_stdin() {
  python3 -c 'import hashlib,sys; h=hashlib.sha256(); [h.update(b) for b in iter(lambda:sys.stdin.buffer.read(1<<20), b"")]; print(h.hexdigest())'
}
trap 'rc=$?; echo; echo "FINALIZE FAILED at line $LINENO (rc=$rc) — SNAPSHOT_COMPLETE.json NOT written."; exit "$rc"' ERR

if ! gsutil -q stat "${PARENT}/SNAPSHOT_COMPLETE.json" 2>/dev/null; then
  echo "REFUSING: parent snapshot is not verified complete: $PARENT" >&2; exit 2
fi
if gsutil -q stat "${SNAP}/SNAPSHOT_COMPLETE.json" 2>/dev/null; then
  echo "REFUSING: snapshot already finalized: $SNAP" >&2; exit 3
fi
for m in PROBE2_COMPLETE.json MAC_COMPLETE.json; do
  gsutil -q stat "${SNAP}/${m}" 2>/dev/null || { echo "REFUSING: missing $m" >&2; exit 4; }
  gsutil -q cp "${SNAP}/${m}" "$WORK/${m}"
done

# Validate marker fields, exact destinations, manifest metadata, then re-read and
# hash every payload object. This closes the gap where a manifest survives but
# a payload object is later truncated/deleted.
python3 - "$WORK" "$SNAP" "$PARENT" "$SNAPSHOT_ID" <<'PY'
import hashlib,json,subprocess,sys
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
work,snap,parent,sid=Path(sys.argv[1]),sys.argv[2].rstrip('/'),sys.argv[3].rstrip('/'),sys.argv[4]
plans=(('PROBE2_COMPLETE.json','probe2','probe2'),('MAC_COMPLETE.json','mac_analysis','mac_analysis'))
summary={}; problems=[]
for fname,component,subdir in plans:
    d=json.loads((work/fname).read_text())
    required=('component','snapshot_id','destination','parent_snapshot','source_hostname','completed_utc','manifest_sha256','file_count','total_bytes','verified_by_remote_sha256_readback')
    for k in required:
        if k not in d: problems.append(f'{fname}: missing {k}')
    expected_dest=f'{snap}/{subdir}'
    if d.get('component')!=component: problems.append(f'{fname}: wrong component')
    if d.get('snapshot_id')!=sid: problems.append(f'{fname}: wrong snapshot_id')
    if d.get('destination')!=expected_dest: problems.append(f'{fname}: destination {d.get("destination")!r} != {expected_dest!r}')
    if d.get('parent_snapshot','').rstrip('/')!=parent: problems.append(f'{fname}: wrong parent_snapshot')
    if d.get('verified_by_remote_sha256_readback') is not True: problems.append(f'{fname}: readback flag not true')
    p=subprocess.run(['gsutil','cat',f'{snap}/{subdir}/provenance/MANIFEST.json'],capture_output=True)
    if p.returncode or not p.stdout:
        problems.append(f'{fname}: remote manifest unreadable'); continue
    raw=p.stdout; got=hashlib.sha256(raw).hexdigest()
    if got!=d.get('manifest_sha256'): problems.append(f'{fname}: manifest hash mismatch')
    man=json.loads(raw)
    for k,expected in (('component',component),('snapshot_id',sid),('source_hostname',d.get('source_hostname')),('file_count',d.get('file_count')),('total_bytes',d.get('total_bytes'))):
        if man.get(k)!=expected: problems.append(f'{fname}: manifest {k}={man.get(k)!r} != marker {expected!r}')
    checks=list(man.get('files') or [])
    checks.append(dict(path='provenance/MANIFEST.json',sha256=got))
    def one(it):
        q=subprocess.Popen(['gsutil','cat',f"{snap}/{subdir}/{it['path']}"],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        h=hashlib.sha256(); assert q.stdout is not None
        for blk in iter(lambda:q.stdout.read(1<<20),b''): h.update(blk)
        _,err=q.communicate(); return it['path'],q.returncode,it['sha256'],h.hexdigest(),err.decode('utf-8','replace')[:200]
    bad=[]; done=0; every=max(1,len(checks)//20)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for fut in as_completed([pool.submit(one,it) for it in checks]):
            path,rc,exp,actual,err=fut.result(); done+=1
            if rc!=0 or actual!=exp: bad.append((path,rc,exp,actual,err))
            if done%every==0 or done==len(checks): print(f'  {component}: verified {done}/{len(checks)}',flush=True)
    if bad:
        problems.append(f'{fname}: {len(bad)} payload verification failures; first={bad[0]}')
    summary[component]=dict(file_count=d.get('file_count'),total_bytes=d.get('total_bytes'),manifest_sha256=d.get('manifest_sha256'),source_hostname=d.get('source_hostname'),completed_utc=d.get('completed_utc'))
if problems:
    print('COMPONENT VALIDATION FAILED')
    for p in problems: print('  '+p)
    raise SystemExit(5)
(work/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
print('component validation and full payload re-read: PASS')
PY

python3 - "$WORK" "$SNAP" "$PARENT" "$SNAPSHOT_ID" <<'PY'
import json,sys,datetime as dt
from pathlib import Path
work,snap,parent,sid=Path(sys.argv[1]),*sys.argv[2:5]
s=json.loads((work/'summary.json').read_text()); p=s['probe2']; m=s['mac_analysis']
readme=f'''# Kalshi snapshot {sid}

Verified two-source delta snapshot. Both component payloads and manifests were
read back and SHA-256 checked during component creation, then fully re-read and
verified again by the finalizer.

- Destination: `{snap}`
- Parent: `{parent}`
- Probe2 payload files: {p['file_count']:,}; bytes: {p['total_bytes']:,}
- Mac payload files: {m['file_count']:,}; bytes: {m['total_bytes']:,}

`SNAPSHOT_COMPLETE.json` is the only marker that means both components are whole.
The 11 GB reproducible CF cache is excluded; its manifest is retained in the
Probe2 component. A complete restore requires this snapshot and its parent.
'''
(work/'README.md').write_text(readme,encoding='utf-8')
PY
README_SHA="$(sha256_file "$WORK/README.md")"
gsutil -q cp "$WORK/README.md" "${SNAP}/README.md"
[[ "$(gsutil cat "${SNAP}/README.md" | sha256_stdin)" == "$README_SHA" ]] || { echo "README readback mismatch"; exit 6; }

python3 - "$WORK" "$SNAP" "$PARENT" "$SNAPSHOT_ID" "$README_SHA" <<'PY'
import json,socket,sys,datetime as dt
from pathlib import Path
work,snap,parent,sid,rsha=Path(sys.argv[1]),*sys.argv[2:6]
s=json.loads((work/'summary.json').read_text()); p=s['probe2']; m=s['mac_analysis']
marker=dict(snapshot_id=sid,destination=snap,parent_snapshot=parent,snapshot_type='two_source_delta',
 finalized_utc=dt.datetime.now(dt.timezone.utc).isoformat(),finalized_on=socket.gethostname(),components=s,
 total_file_count=p['file_count']+m['file_count'],total_bytes=p['total_bytes']+m['total_bytes'],
 readme_sha256=rsha,both_components_validated=True,component_payloads_rehashed_from_bucket=True)
(work/'SNAPSHOT_COMPLETE.json').write_text(json.dumps(marker,indent=2,sort_keys=True)+'\n',encoding='utf-8')
PY
MARKER_SHA="$(sha256_file "$WORK/SNAPSHOT_COMPLETE.json")"
gsutil -q cp "$WORK/SNAPSHOT_COMPLETE.json" "${SNAP}/SNAPSHOT_COMPLETE.json"
[[ "$(gsutil cat "${SNAP}/SNAPSHOT_COMPLETE.json" | sha256_stdin)" == "$MARKER_SHA" ]] || { echo "final marker readback mismatch"; exit 7; }

echo "SNAPSHOT FINALIZED: $SNAP"
gsutil cat "${SNAP}/SNAPSHOT_COMPLETE.json"
rm -rf "$WORK"
