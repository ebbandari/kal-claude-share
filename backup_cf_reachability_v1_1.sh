#!/usr/bin/env bash
# Immutable, verified snapshot of CF Reachability v1.1 artifacts.
set -Eeuo pipefail
umask 077

BUCKET="gs://kalshi-data-vault-kalshi-collector-personal"
BASE="${BUCKET}/analysis/CF_REACHABILITY_V1_1"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${BASE}/snapshots/${STAMP}"
STAGE="$(mktemp -d "$HOME/cf_reachability_v1_1_snapshot.XXXXXX")"

cleanup() {
  rm -rf "$STAGE"
}
trap cleanup EXIT

mkdir -p "$STAGE"/{code,results,inputs,logs,provenance}
: > "$STAGE/provenance/MISSING_OPTIONAL.txt"

add_required() {
  local src="$1" rel="$2" dst="$STAGE/$2"
  if [[ ! -f "$src" ]]; then
    echo "REQUIRED FILE MISSING: $src" >&2
    exit 2
  fi
  mkdir -p "$(dirname "$dst")"
  ln "$src" "$dst" 2>/dev/null || cp -p "$src" "$dst"
}

add_optional() {
  local src="$1" rel="$2" dst="$STAGE/$2"
  if [[ ! -f "$src" ]]; then
    printf '%s\n' "$src" >> "$STAGE/provenance/MISSING_OPTIONAL.txt"
    return 0
  fi
  mkdir -p "$(dirname "$dst")"
  ln "$src" "$dst" 2>/dev/null || cp -p "$src" "$dst"
}

echo "Staging required files..."

# Exact reviewed analyzer and tests.
add_required "$HOME/cf_reachability_v1_1_gpt.py" \
  "code/cf_reachability_v1_1_gpt.py"
add_required "$HOME/test_cf_reachability_v1_1_adversarial.py" \
  "code/test_cf_reachability_v1_1_adversarial.py"

# Final CF reachability outputs.
add_required "$HOME/cf_reach_v1_1/CF_REACHABILITY_REPORT.md" \
  "results/CF_REACHABILITY_REPORT.md"
add_required "$HOME/cf_reach_v1_1/cf_reachability_summary.json" \
  "results/cf_reachability_summary.json"
add_required "$HOME/cf_reach_v1_1/cf_reference_counts.csv.gz" \
  "results/cf_reference_counts.csv.gz"
add_required "$HOME/cf_reach_v1_1/paper_bot_cf_reachability_rows.csv.gz" \
  "results/paper_bot_cf_reachability_rows.csv.gz"

# Logs and cache provenance. The 11 GB cache itself is intentionally excluded.
add_required "$HOME/cf_reach_v1_1.log" \
  "logs/cf_reach_v1_1.log"
add_required "$HOME/cf_reach_cache.log" \
  "logs/cf_reach_cache.log"
add_required "$HOME/cf_reach_v1_1/cf_cache/manifest.jsonl" \
  "provenance/cf_cache_manifest.jsonl"

# Compact opportunity base and the two raw logs needed to reproduce it.
add_required "$HOME/paper_bot_opportunity_base.csv" \
  "inputs/paper_bot_opportunity_base.csv"
add_required "$HOME/paper_bot_opportunity_base_rejected.csv" \
  "inputs/paper_bot_opportunity_base_rejected.csv"
add_required "$HOME/paper_bot_opportunity_base.csv.summary.txt" \
  "inputs/paper_bot_opportunity_base.csv.summary.txt"
add_required "$HOME/logs/paper_bot_logs_v2/trades_2026-07-15.csv" \
  "inputs/raw_paper_bot_logs/trades_2026-07-15.csv"
add_required "$HOME/logs/paper_bot_logs_v2/settled_2026-07-15.csv" \
  "inputs/raw_paper_bot_logs/settled_2026-07-15.csv"

# Strike extraction and exact API recovery, including the API cache and
# lifecycle-only coverage table for auditability.
add_required "$HOME/strike_recovery_v1_1/opportunity_strike_coverage_recovered.csv" \
  "inputs/strike_recovery/opportunity_strike_coverage_recovered.csv"
add_required "$HOME/strike_recovery_v1_1/recovered_strikes.csv" \
  "inputs/strike_recovery/recovered_strikes.csv"
add_required "$HOME/strike_recovery_v1_1/validation_sample.csv" \
  "inputs/strike_recovery/validation_sample.csv"
add_required "$HOME/strike_recovery_v1_1/SUMMARY.txt" \
  "inputs/strike_recovery/SUMMARY.txt"
add_required "$HOME/strike_recovery_v1_1/api_market_cache.jsonl" \
  "inputs/strike_recovery/api_market_cache.jsonl"
add_required "$HOME/strike_table/strike_table.csv" \
  "inputs/strike_table/strike_table.csv"
add_required "$HOME/strike_table/strike_events.csv" \
  "inputs/strike_table/strike_events.csv"
add_required "$HOME/strike_table/opportunity_strike_coverage.csv" \
  "inputs/strike_table/opportunity_strike_coverage_lifecycle_only.csv"
add_required "$HOME/strike_table/SUMMARY.txt" \
  "inputs/strike_table/SUMMARY.txt"

# Seven-hour depth reconstruction and repricing substrate.
add_required "$HOME/paper_bot_depth_reconstruction.csv" \
  "inputs/depth/paper_bot_depth_reconstruction.csv"
add_required "$HOME/paper_bot_depth_DOGE_XRP.csv" \
  "inputs/depth/paper_bot_depth_DOGE_XRP.csv"
add_required "$HOME/paper_bot_reprice_v1_2_rows.csv" \
  "inputs/reprice/paper_bot_reprice_v1_2_rows.csv"
add_required "$HOME/paper_bot_reprice_v1_2_summary.csv" \
  "inputs/reprice/paper_bot_reprice_v1_2_summary.csv"
add_required "$HOME/paper_bot_reprice_v1_2_rejected_rows.csv" \
  "inputs/reprice/paper_bot_reprice_v1_2_rejected_rows.csv"

echo "Staging useful optional files..."

add_optional "$HOME/paper_bot_depth_report.json" \
  "inputs/depth/paper_bot_depth_report.json"
add_optional "$HOME/paper_bot_depth_DOGE_XRP.json" \
  "inputs/depth/paper_bot_depth_DOGE_XRP.json"
add_optional "$HOME/paper_bot_reprice_v1_2_summary.json" \
  "inputs/reprice/paper_bot_reprice_v1_2_summary.json"
add_optional "$HOME/logs/paper_bot_logs_v2/triggers_2026-07-15.csv" \
  "inputs/raw_paper_bot_logs/triggers_2026-07-15.csv"
add_optional "$HOME/logs/paper_bot_logs_v2/equity_2026-07-15.csv" \
  "inputs/raw_paper_bot_logs/equity_2026-07-15.csv"
add_optional "$HOME/verify_settlement_offset_v1_1_gpt.py" \
  "code/verify_settlement_offset_v1_1_gpt.py"
add_optional "$HOME/settlement_offset_verification.csv" \
  "results/settlement_offset_verification.csv"
add_optional "$HOME/build_opportunity_base_v1_1_gpt.py" \
  "code/build_opportunity_base_v1_1_gpt.py"
add_optional "$HOME/test_build_opportunity_base_v1_1_adversarial.py" \
  "code/test_build_opportunity_base_v1_1_adversarial.py"
add_optional "$HOME/build_strike_table_v1_1_gpt.py" \
  "code/build_strike_table_v1_1_gpt.py"
add_optional "$HOME/test_build_strike_table_v1_1_adversarial.py" \
  "code/test_build_strike_table_v1_1_adversarial.py"
add_optional "$HOME/recover_strikes_from_api_v1_1_gpt.py" \
  "code/recover_strikes_from_api_v1_1_gpt.py"
add_optional "$HOME/test_recover_strikes_from_api_v1_1_adversarial.py" \
  "code/test_recover_strikes_from_api_v1_1_adversarial.py"
add_optional "$HOME/paper_bot_reprice_analysis_v1_2.py" \
  "code/paper_bot_reprice_analysis_v1_2.py"
add_optional "$HOME/CF_REACHABILITY_V1_1_FULL_RESULTS_AND_FOLLOWUP.md" \
  "results/CF_REACHABILITY_V1_1_FULL_RESULTS_AND_FOLLOWUP.md"

# Record the exact run and environment.
ANALYZER_SHA="$(sha256sum "$HOME/cf_reachability_v1_1_gpt.py" | awk '{print $1}')"
{
  echo "snapshot_utc=$STAMP"
  echo "host=$(hostname)"
  echo "user=$(id -un)"
  echo "python=$(python3 --version 2>&1)"
  echo "analyzer_sha256=$ANALYZER_SHA"
  echo "sealed_holdout=2026-09-07..2026-09-13"
  echo "analysis_command=python3 -u ~/cf_reachability_v1_1_gpt.py --stage all --no-fetch --base ~/paper_bot_opportunity_base.csv --coverage ~/strike_recovery_v1_1/opportunity_strike_coverage_recovered.csv --api-cache ~/strike_recovery_v1_1/api_market_cache.jsonl --out ~/cf_reach_v1_1 --cf-availability-lag-ms 250"
  echo "excluded_large_cache=$HOME/cf_reach_v1_1/cf_cache"
  echo "cache_manifest_included=yes"
  echo "source_repo=https://github.com/ebbandari/kal-claude-share"
} > "$STAGE/provenance/RUN_MANIFEST.txt"

# Make the six absent cache objects explicit.
python3 - "$STAGE/provenance/cf_cache_manifest.jsonl" \
           "$STAGE/provenance/CF_CACHE_ABSENT.jsonl" <<'PY'
import json, sys
src, dst = sys.argv[1:3]
n = 0
with open(src, encoding="utf-8") as f, open(dst, "w", encoding="utf-8") as g:
    for line in f:
        try:
            r = json.loads(line)
        except Exception:
            continue
        if str(r.get("status", "")).upper() == "ABSENT":
            g.write(json.dumps(r, sort_keys=True) + "\n")
            n += 1
print(f"absent cache objects recorded: {n}")
PY

# Create a complete local file inventory and hashes. SHA256SUMS does not hash
# itself, avoiding a circular manifest.
(
  cd "$STAGE"
  find code results inputs logs provenance -type f -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum > SHA256SUMS.txt
  find code results inputs logs provenance -type f -printf '%P\t%s\n' \
    | LC_ALL=C sort > FILES.tsv
)

LOCAL_COUNT="$(find "$STAGE" -type f | wc -l)"
LOCAL_BYTES="$(du -sb "$STAGE" | awk '{print $1}')"
MANIFEST_SHA="$(sha256sum "$STAGE/SHA256SUMS.txt" | awk '{print $1}')"

echo
echo "Snapshot destination:"
echo "  $DEST"
echo "Local files: $LOCAL_COUNT"
echo "Local bytes: $LOCAL_BYTES"
echo "Manifest SHA-256: $MANIFEST_SHA"

# A timestamped destination is immutable by convention; refuse any collision.
if gsutil ls "$DEST/**" >/dev/null 2>&1; then
  echo "REFUSING: destination already contains objects: $DEST" >&2
  exit 3
fi

echo
echo "Uploading snapshot..."
gsutil -m cp -r "$STAGE"/* "$DEST/"

echo
echo "Verifying every uploaded data object by SHA-256 readback..."
while read -r expected rel; do
  rel="${rel#./}"
  got="$(gsutil cat "$DEST/$rel" | sha256sum | awk '{print $1}')"
  if [[ "$got" != "$expected" ]]; then
    echo "CHECKSUM MISMATCH: $rel" >&2
    echo " expected $expected" >&2
    echo " got      $got" >&2
    exit 4
  fi
done < "$STAGE/SHA256SUMS.txt"

# Verify the two manifest files themselves.
cmp "$STAGE/SHA256SUMS.txt" <(gsutil cat "$DEST/SHA256SUMS.txt")
cmp "$STAGE/FILES.tsv" <(gsutil cat "$DEST/FILES.tsv")

# Manifest-last commit marker: its existence means the full snapshot was
# uploaded and byte-for-byte verified.
cat > "$STAGE/SNAPSHOT_COMPLETE.json" <<EOF
{
  "snapshot_utc": "$STAMP",
  "destination": "$DEST",
  "file_count_before_commit_marker": $LOCAL_COUNT,
  "local_bytes_before_commit_marker": $LOCAL_BYTES,
  "sha256sums_sha256": "$MANIFEST_SHA",
  "verified_by_remote_sha256_readback": true
}
EOF
gsutil cp "$STAGE/SNAPSHOT_COMPLETE.json" "$DEST/SNAPSHOT_COMPLETE.json"
cmp "$STAGE/SNAPSHOT_COMPLETE.json" \
    <(gsutil cat "$DEST/SNAPSHOT_COMPLETE.json")

echo
echo "BACKUP VERIFIED:"
echo "  $DEST"
echo "  commit marker: $DEST/SNAPSHOT_COMPLETE.json"
echo
gsutil ls -lh "$DEST/**"
