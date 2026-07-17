#!/usr/bin/env bash
# Validate the ORIGINAL patches on the verified parent commit (Codebase Editing Workflow
# step 5: "Validate the Original Patches"). Confirms the baseline SWE task is sound BEFORE
# any blocker injection.
#
# Two modes:
#   - Full F2P (preferred): if an original relevant-tests JSON is available (--tests-file or
#     $TASK_FILES/relevant_tests_original.txt) AND an instance id is known, run
#     task_checker.py with the ORIGINAL test + golden patches (test fails -> golden passes).
#   - Apply-check fallback: otherwise just confirm both original patches apply cleanly on the
#     parent commit, and tell the caller to supply a tests file to run the full F2P.
#
# Usage:
#   validate_original.sh [--container NAME] [--repo /app] [--commit HASH]
#                        [--instance-id ID] [--tests-file PATH]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

CONTAINER="${HILBENCH_CONTAINER:-hilbench_task}"
REPO="${HILBENCH_REPO:-/app}"
COMMIT="${HILBENCH_BASE_COMMIT:-}"
INSTANCE_ID="${HILBENCH_INSTANCE_ID:-}"
TESTS_FILE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --container)   CONTAINER="$2"; shift 2 ;;
    --repo)        REPO="$2"; shift 2 ;;
    --commit)      COMMIT="$2"; shift 2 ;;
    --instance-id) INSTANCE_ID="$2"; shift 2 ;;
    --tests-file)  TESTS_FILE="$2"; shift 2 ;;
    *) hb_die "validate_original.sh: unknown arg: $1" ;;
  esac
done

TF="$(hb_resolve_task_files)" || hb_die "REQUIRED_INPUT_FILE_MISSING (task_info.txt)"
TF="$(cd "$TF" && pwd)"
DELIV="$(hb_resolve_deliverables "$TF")"
[ -f "$DELIV/.hilbench_env" ] && . "$DELIV/.hilbench_env"
CONTAINER="${HILBENCH_CONTAINER:-$CONTAINER}"
REPO="${HILBENCH_REPO:-$REPO}"
[ -z "$COMMIT" ]      && COMMIT="${HILBENCH_BASE_COMMIT:-$(hb_field "$TF/task_info.txt" 'base[_ ]?commit[_ ]?hash' || true)}"
[ -z "$INSTANCE_ID" ] && INSTANCE_ID="${HILBENCH_INSTANCE_ID:-$(hb_field "$TF/task_info.txt" 'instance[_ ]?id' || true)}"

hb_container_running "$CONTAINER" || hb_die "CONTAINER_NOT_RUNNING ($CONTAINER). Run /hilbench-provision first."
[ -z "$COMMIT" ] && hb_die "PARENT_COMMIT_MISMATCH: base commit unknown; re-run /hilbench-provision"

ORIG_TEST="$TF/test_patch.diff"
ORIG_GOLD="$TF/golden_patch.diff"
[ -f "$ORIG_TEST" ] || hb_die "REQUIRED_INPUT_FILE_MISSING: $ORIG_TEST"
[ -f "$ORIG_GOLD" ] || hb_die "REQUIRED_INPUT_FILE_MISSING: $ORIG_GOLD"

hb_head "Validate original patch format and LF line endings"
python3 "$SCRIPT_DIR/validate_patch_artifacts.py" --patch "$ORIG_TEST" --patch "$ORIG_GOLD" \
  || hb_die "ORIGINAL_PATCH_FORMAT_FAIL: corrupted/non-unified diff or non-LF line endings"

# Resolve a tests file if not passed explicitly.
if [ -z "$TESTS_FILE" ]; then
  for c in "$TF/relevant_tests_original.txt" "$DELIV/relevant_tests_original.txt"; do
    [ -f "$c" ] && { TESTS_FILE="$c"; break; }
  done
fi

hb_head "Reset repo to parent commit"
docker exec "$CONTAINER" git -C "$REPO" reset --hard "$COMMIT" >/dev/null 2>&1 \
  || hb_die "PARENT_COMMIT_MISMATCH: could not reset $REPO to $COMMIT"
docker exec "$CONTAINER" git -C "$REPO" clean -fdq -e task_files 2>/dev/null || true

hb_head "Copy original patches into container"
docker cp "$ORIG_TEST" "$CONTAINER:$REPO/orig_test_patch.diff"
docker cp "$ORIG_GOLD" "$CONTAINER:$REPO/orig_golden_patch.diff"

hb_head "Apply-check original patches on the parent commit"
docker exec -w "$REPO" "$CONTAINER" git apply --check --ignore-whitespace orig_test_patch.diff \
  || hb_die "ORIGINAL_TEST_PATCH_APPLY_FAIL: original test_patch.diff does not apply on $COMMIT"
docker exec -w "$REPO" "$CONTAINER" git apply --check --ignore-whitespace orig_golden_patch.diff \
  || hb_die "ORIGINAL_GOLDEN_PATCH_APPLY_FAIL: original golden_patch.diff does not apply on $COMMIT"
hb_log "both original patches apply cleanly"

if [ -z "$TESTS_FILE" ] || [ -z "$INSTANCE_ID" ]; then
  echo
  echo "ORIGINAL_APPLY_OK"
  echo "  Both original patches apply on the verified parent commit."
  [ -z "$INSTANCE_ID" ] && echo "  (F2P not run: instance_id unknown - pass --instance-id.)"
  [ -z "$TESTS_FILE" ]  && echo "  (F2P not run: no original relevant-tests file - pass --tests-file with a JSON array of the tests in the original test patch to run the full FAIL->PASS check.)"
  exit 0
fi

hb_head "Run F2P on original patches (task_checker.py)"
CHECKER=""
for c in "$TF/task_checker.py" "$DELIV/task_checker.py"; do
  [ -f "$c" ] && { CHECKER="$c"; break; }
done
[ -z "$CHECKER" ] && hb_die "CHECKER_NOT_AVAILABLE: task_checker.py not found in task_files/ or deliverables/"

docker exec "$CONTAINER" mkdir -p "$REPO/task_files"
docker cp "$CHECKER"    "$CONTAINER:$REPO/task_files/task_checker.py"
docker cp "$TESTS_FILE" "$CONTAINER:$REPO/task_files/relevant_tests_original.txt"

set +e
docker exec -w "$REPO" "$CONTAINER" python task_files/task_checker.py \
  --instance-id "$INSTANCE_ID" \
  --timeout "${HILBENCH_CHECK_TIMEOUT:-300}" \
  --tests-file "$REPO/task_files/relevant_tests_original.txt" \
  --test-patch "$REPO/orig_test_patch.diff" \
  --golden-patch "$REPO/orig_golden_patch.diff"
CODE=$?
set -e
hb_log "task_checker exit code: $CODE"

echo
case "$CODE" in
  0)  echo "ORIGINAL_PATCHES_OK exit=0 (original test patch FAILs, original golden patch PASSes - baseline task is sound)";;
  8)  echo "ORIGINAL_TEST_PATCH_APPLY_FAIL exit=8"; exit 8 ;;
  9)  echo "ORIGINAL_GOLDEN_PATCH_APPLY_FAIL exit=9"; exit 9 ;;
  10|11|12|1) echo "ORIGINAL_F2P_FAIL exit=$CODE (original patches do not show a clean FAIL->PASS for the given tests; check the tests list and the baseline before injecting)"; exit "$CODE" ;;
  *)  echo "ORIGINAL_CHECK_ERROR exit=$CODE (runner/parser/env issue; not a task-content signal)"; exit "$CODE" ;;
esac
