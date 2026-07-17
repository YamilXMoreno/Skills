#!/usr/bin/env bash
# Validate the AUTHORED OBSTRUCTED patches after blocker injection: apply setup_patch as the
# baseline, then run task_checker.py with the obstructed test patch + the authored obstructed
# golden patch to confirm a clean FAIL->PASS (test fails on baseline+setup, golden makes it pass).
#
# This is the dynamic-execution counterpart to validate_original.sh, but for the post-injection
# artifacts and the AUTHORED golden (not an agent-generated patch). It answers: "does my
# obstructed golden actually execute cleanly against my obstructed tests?" — a signal that the
# static Patch Content Validator (references/08-patch-content-validator.md) cannot give.
#
# Usage:
#   validate_obstructed.sh [--container NAME] [--repo /app] [--commit HASH]
#                          [--instance-id ID] [--golden-patch PATH] [--tests-file PATH]
#
# Verdicts:
#   OBSTRUCTED_PATCHES_OK           exit=0  (obstructed test FAILs on baseline+setup, obstructed golden PASSes)
#   OBSTRUCTED_TEST_PATCH_APPLY_FAIL exit=8
#   OBSTRUCTED_GOLDEN_PATCH_APPLY_FAIL exit=9
#   OBSTRUCTED_F2P_FAIL             exit=10|11|12|1 (golden does not make the relevant tests pass)
#   OBSTRUCTED_CHECK_ERROR          exit=* (runner/parser/env issue; not a task-content signal)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

CONTAINER="${HILBENCH_CONTAINER:-hilbench_task}"
REPO="${HILBENCH_REPO:-/app}"
COMMIT="${HILBENCH_BASE_COMMIT:-}"
INSTANCE_ID="${HILBENCH_INSTANCE_ID:-}"
GOLDEN_OVERRIDE=""
TESTS_FILE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --container)    CONTAINER="$2"; shift 2 ;;
    --repo)         REPO="$2"; shift 2 ;;
    --commit)       COMMIT="$2"; shift 2 ;;
    --instance-id)  INSTANCE_ID="$2"; shift 2 ;;
    --golden-patch) GOLDEN_OVERRIDE="$2"; shift 2 ;;
    --tests-file)   TESTS_FILE="$2"; shift 2 ;;
    *) hb_die "validate_obstructed.sh: unknown arg: $1" ;;
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
[ -z "$COMMIT" ]      && hb_die "PARENT_COMMIT_MISMATCH: base commit unknown; re-run /hilbench-provision"
[ -z "$INSTANCE_ID" ] && hb_die "INSTANCE_ID_MISSING: pass --instance-id or add instance_id to task_info.txt (needed to fetch run_script.sh/parsing_script.py)"

# Resolve the authored obstructed artifacts (deliverables first, then originals as a fallback).
CHECKER=""
for c in "$TF/task_checker.py" "$DELIV/task_checker.py" "$SCRIPT_DIR/../task_checker.py"; do
  [ -f "$c" ] && { CHECKER="$c"; break; }
done
[ -z "$CHECKER" ] && hb_die "CHECKER_NOT_AVAILABLE: task_checker.py not found (looked in task_files/ and deliverables/)"

GOLDEN="$GOLDEN_OVERRIDE"
if [ -z "$GOLDEN" ]; then
  for c in "$DELIV/golden_patch_obstructed.diff" "$DELIV/golden_patch.diff" "$TF/golden_patch.diff"; do
    [ -f "$c" ] && { GOLDEN="$c"; break; }
  done
fi
[ -n "$GOLDEN" ] && [ -f "$GOLDEN" ] || hb_die "REQUIRED_INPUT_FILE_MISSING: golden_patch_obstructed.diff not found (pass --golden-patch to override)"

TEST_PATCH=""
for c in "$DELIV/test_patch_obstructed.diff" "$TF/test_patch.diff"; do
  [ -f "$c" ] && { TEST_PATCH="$c"; break; }
done
[ -z "$TEST_PATCH" ] && hb_die "REQUIRED_INPUT_FILE_MISSING: no test patch (test_patch_obstructed.diff / test_patch.diff)"

if [ -z "$TESTS_FILE" ]; then
  for c in "$DELIV/relevant_tests.txt" "$TF/relevant_tests.txt"; do
    [ -f "$c" ] && { TESTS_FILE="$c"; break; }
  done
fi
[ -z "$TESTS_FILE" ] && hb_die "REQUIRED_INPUT_FILE_MISSING: relevant_tests.txt not found"

hb_head "Validate patch format and LF line endings"
python3 "$SCRIPT_DIR/validate_patch_artifacts.py" --deliverables "$DELIV" \
  || hb_die "OBSTRUCTED_PATCH_FORMAT_FAIL: corrupted/non-unified diff or non-LF line endings"

hb_head "Reset repo to parent commit"
docker exec "$CONTAINER" git -C "$REPO" reset --hard "$COMMIT" >/dev/null 2>&1 \
  || hb_die "PARENT_COMMIT_MISMATCH: could not reset $REPO to $COMMIT"
docker exec "$CONTAINER" git -C "$REPO" clean -fdq -e task_files -e .hilbench_stash 2>/dev/null || true

hb_head "Bake setup_patch as the working baseline (if present)"
if [ -f "$DELIV/setup_patch.diff" ] && [ -s "$DELIV/setup_patch.diff" ]; then
  docker exec "$CONTAINER" mkdir -p "$REPO/.hilbench_stash"
  docker cp "$DELIV/setup_patch.diff" "$CONTAINER:$REPO/.hilbench_stash/setup_patch.diff"
  docker exec "$CONTAINER" git -C "$REPO" apply -v --ignore-whitespace ".hilbench_stash/setup_patch.diff" \
    || hb_die "OBSTRUCTED_SETUP_PATCH_APPLY_FAIL: setup_patch.diff did not apply on the parent commit"
  docker exec "$CONTAINER" sh -lc "cd '$REPO' && git -c user.email=hb@local -c user.name=hilbench add -A && git -c user.email=hb@local -c user.name=hilbench commit -q -m 'hilbench baseline (parent + setup)'" \
    || hb_die "OBSTRUCTED_CHECK_ERROR: could not commit setup baseline"
  hb_log "setup_patch applied and committed as baseline"
else
  hb_log "no setup_patch.diff; baseline is the parent commit"
fi

hb_head "Copy artifacts into container"
docker exec "$CONTAINER" mkdir -p "$REPO/task_files"
docker cp "$CHECKER"    "$CONTAINER:$REPO/task_files/task_checker.py"
docker cp "$TEST_PATCH" "$CONTAINER:$REPO/test_patch_obstructed.diff"
docker cp "$TESTS_FILE" "$CONTAINER:$REPO/task_files/relevant_tests.txt"
docker cp "$GOLDEN"     "$CONTAINER:$REPO/golden_patch_obstructed.diff"
hb_log "golden under test: $GOLDEN"

hb_head "Run task_checker.py (F2P on obstructed patches)"
set +e
docker exec -w "$REPO" "$CONTAINER" python task_files/task_checker.py \
  --instance-id "$INSTANCE_ID" \
  --timeout "${HILBENCH_CHECK_TIMEOUT:-300}" \
  --tests-file "$REPO/task_files/relevant_tests.txt" \
  --test-patch "$REPO/test_patch_obstructed.diff" \
  --golden-patch "$REPO/golden_patch_obstructed.diff"
CODE=$?
set -e
hb_log "task_checker exit code: $CODE"

# Persist the checker logs to deliverables for review.
docker cp "$CONTAINER:$REPO/task_files/after_stderr.log" "$DELIV/obstructed_after_stderr.log" 2>/dev/null || true

case "$CODE" in
  0)  RESULT="OBSTRUCTED_PATCHES_OK exit=0 (obstructed test patch FAILs on baseline+setup, obstructed golden patch PASSes - the authored obstructed task is sound)";;
  8)  RESULT="OBSTRUCTED_TEST_PATCH_APPLY_FAIL exit=8 (test_patch_obstructed.diff does not apply on baseline+setup; fix the diff)";;
  9)  RESULT="OBSTRUCTED_GOLDEN_PATCH_APPLY_FAIL exit=9 (golden_patch_obstructed.diff does not apply on baseline+setup+tests; fix the diff)";;
  10|11|12|1) RESULT="OBSTRUCTED_F2P_FAIL exit=$CODE (the authored golden does not make the relevant tests pass, or a test does not fail without it; align the golden/tests/spec, do NOT weaken tests)";;
  *)  RESULT="OBSTRUCTED_CHECK_ERROR exit=$CODE (runner/parser/env issue; not a task-content signal)";;
esac

# Persist the verdict to deliverables for review/capture.
RESULT_FILE="$DELIV/validate_obstructed_result.txt"
{
  echo "# /hilbench-validate-obstructed result"
  echo "timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "instance_id: $INSTANCE_ID"
  echo "container: $CONTAINER"
  echo "golden_under_test: $GOLDEN"
  echo "test_patch: $TEST_PATCH"
  echo "tests_file: $TESTS_FILE"
  echo "setup_patch: $([ -f "$DELIV/setup_patch.diff" ] && [ -s "$DELIV/setup_patch.diff" ] && echo "$DELIV/setup_patch.diff (baked as baseline)" || echo "(none)")"
  echo "task_checker_exit: $CODE"
  echo "checker_log: $DELIV/obstructed_after_stderr.log"
  echo
  echo "$RESULT"
} > "$RESULT_FILE"
hb_log "wrote verdict to $RESULT_FILE"

echo
echo "$RESULT"
echo "  (verdict saved to $RESULT_FILE)"
if [ "$CODE" -ne 0 ]; then exit "$CODE"; fi
exit 0
