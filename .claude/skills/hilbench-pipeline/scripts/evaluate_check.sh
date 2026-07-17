#!/usr/bin/env bash
# Evaluate a scripted Attempter Check solve inside the container.
#
# Captures the agent's changes as a patch (if not already provided), copies the checker +
# obstructed test patch + relevant tests into the container, runs task_checker.py, and maps
# its exit code to a Check verdict.
#
# Usage: evaluate_check.sh <check1|check2> [--container NAME] [--repo /app]
#                          [--instance-id ID] [--agent-patch PATH]
#
# Verdicts:
#   check1: PASS (blockers NOT guessable) | FAIL_GUESSABLE (agent solved w/o resolutions)
#   check2: PASS (solvable with resolutions) | FAIL_UNSOLVABLE (tests fail even with resolutions)
#   both:   CHECK_ERROR_PATCH (patch apply failure) | CHECK_ERROR_ENV (runner/env failure)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

MODE="${1:-}"; shift || true
case "$MODE" in check1|check2) ;; *) hb_die "evaluate_check.sh: first arg must be check1 or check2";; esac

CONTAINER="${HILBENCH_CONTAINER:-hilbench_task}"
REPO="${HILBENCH_REPO:-/app}"
INSTANCE_ID="${HILBENCH_INSTANCE_ID:-}"
AGENT_PATCH=""
CONTAINER_SET=0
REPO_SET=0

while [ $# -gt 0 ]; do
  case "$1" in
    --container)   CONTAINER="$2"; CONTAINER_SET=1; shift 2 ;;
    --repo)        REPO="$2"; REPO_SET=1; shift 2 ;;
    --instance-id) INSTANCE_ID="$2"; shift 2 ;;
    --agent-patch) AGENT_PATCH="$2"; shift 2 ;;
    *) hb_die "evaluate_check.sh: unknown arg: $1" ;;
  esac
done

TF="$(hb_resolve_task_files)" || hb_die "REQUIRED_INPUT_FILE_MISSING (task_info.txt)"
TF="$(cd "$TF" && pwd)"
DELIV="$(hb_resolve_deliverables "$TF")"
[ -f "$DELIV/.hilbench_env" ] && . "$DELIV/.hilbench_env"
[ "$CONTAINER_SET" -eq 0 ] && CONTAINER="${HILBENCH_CONTAINER:-$CONTAINER}"
[ "$REPO_SET" -eq 0 ] && REPO="${HILBENCH_REPO:-$REPO}"
INSTANCE_ID="${INSTANCE_ID:-${HILBENCH_INSTANCE_ID:-}}"

hb_container_running "$CONTAINER" || hb_die "CONTAINER_NOT_RUNNING ($CONTAINER). Run /hilbench-provision first."
[ -z "$INSTANCE_ID" ] && INSTANCE_ID="$(hb_field "$TF/task_info.txt" 'instance[_ ]?id' || true)"
[ -z "$INSTANCE_ID" ] && hb_die "INSTANCE_ID_MISSING: pass --instance-id or add instance_id to task_info.txt (needed to fetch run_script.sh/parsing_script.py)"

# Locate checker + obstructed test patch + relevant tests on the sandbox side.
CHECKER=""
for c in "$TF/task_checker.py" "$DELIV/task_checker.py" "$SCRIPT_DIR/../task_checker.py"; do
  [ -f "$c" ] && { CHECKER="$c"; break; }
done
[ -z "$CHECKER" ] && hb_die "CHECKER_NOT_AVAILABLE: task_checker.py not found (looked in task_files/ and deliverables/)"

TEST_PATCH=""
for c in "$DELIV/test_patch_obstructed.diff" "$TF/test_patch.diff"; do
  [ -f "$c" ] && { TEST_PATCH="$c"; break; }
done
[ -z "$TEST_PATCH" ] && hb_die "REQUIRED_INPUT_FILE_MISSING: no test patch (test_patch_obstructed.diff / test_patch.diff)"

RELTESTS=""
for c in "$DELIV/relevant_tests.txt" "$TF/relevant_tests.txt"; do
  [ -f "$c" ] && { RELTESTS="$c"; break; }
done
[ -z "$RELTESTS" ] && hb_die "REQUIRED_INPUT_FILE_MISSING: relevant_tests.txt not found"

hb_head "Capture agent patch"
if [ -z "$AGENT_PATCH" ]; then
  for c in "$DELIV/${MODE}_agent_patch.diff" "$DELIV/agent_patch.diff"; do
    [ -f "$c" ] && [ -s "$c" ] && { AGENT_PATCH="$c"; break; }
  done
fi
if [ -z "$AGENT_PATCH" ] || [ ! -s "$AGENT_PATCH" ]; then
  hb_log "no agent patch file found; capturing from container working tree (git diff vs baseline)"
  AGENT_PATCH="$DELIV/${MODE}_agent_patch.diff"
  docker exec "$CONTAINER" sh -lc "cd '$REPO' && git add -A && git diff --cached HEAD" > "$AGENT_PATCH" || true
  docker exec "$CONTAINER" git -C "$REPO" reset -q >/dev/null 2>&1 || true
fi
[ -s "$AGENT_PATCH" ] || hb_die "CHECK_ERROR_PATCH: agent produced no changes (empty patch). Did the solve step run and edit $REPO?"
hb_log "agent patch: $AGENT_PATCH ($(wc -l < "$AGENT_PATCH") lines)"

hb_head "Copy artifacts into container"
docker exec "$CONTAINER" mkdir -p "$REPO/task_files"
docker cp "$CHECKER"    "$CONTAINER:$REPO/task_files/task_checker.py"
docker cp "$TEST_PATCH" "$CONTAINER:$REPO/test_patch_obstructed.diff"
docker cp "$RELTESTS"   "$CONTAINER:$REPO/task_files/relevant_tests.txt"
docker cp "$AGENT_PATCH" "$CONTAINER:$REPO/agent_patch.diff"

hb_head "Run task_checker.py (F2P)"
set +e
docker exec -w "$REPO" "$CONTAINER" python task_files/task_checker.py \
  --instance-id "$INSTANCE_ID" \
  --timeout "${HILBENCH_CHECK_TIMEOUT:-300}" \
  --tests-file "$REPO/task_files/relevant_tests.txt" \
  --test-patch "$REPO/test_patch_obstructed.diff" \
  --golden-patch "$REPO/agent_patch.diff"
CODE=$?
set -e
hb_log "task_checker exit code: $CODE"

# Persist the checker logs to deliverables for review.
docker cp "$CONTAINER:$REPO/task_files/after_stderr.log" "$DELIV/${MODE}_after_stderr.log" 2>/dev/null || true

hb_head "Verdict"
verdict=""
case "$CODE" in
  0)      [ "$MODE" = "check1" ] && verdict="FAIL_GUESSABLE" || verdict="PASS" ;;
  10|11|12|1) [ "$MODE" = "check1" ] && verdict="PASS" || verdict="FAIL_UNSOLVABLE" ;;
  8|9)    verdict="CHECK_ERROR_PATCH" ;;
  2|3|5|6) verdict="CHECK_ERROR_ENV" ;;
  *)      verdict="CHECK_ERROR_ENV" ;;
esac

echo
echo "CHECK_RESULT $MODE $verdict exit=$CODE"
case "$verdict" in
  PASS) echo "  ($MODE passed)";;
  FAIL_GUESSABLE)  echo "  Agent solved the obstructed task WITHOUT resolutions -> a blocker is reliably inferable. Redesign the guessable blocker (widen alternatives / remove leakage), then re-author.";;
  FAIL_UNSOLVABLE) echo "  Agent could NOT pass the relevant tests even WITH resolutions -> the spec is underspecified or the golden/tests are misaligned. Add the missing detail to the correct artifact (PS/requirements/interface/resolution) and regenerate.";;
  CHECK_ERROR_PATCH) echo "  Patch apply failure (agent or test patch). This is an env/diff issue, not a blocker-design signal. Fix the diff and re-run evaluate.";;
  CHECK_ERROR_ENV)   echo "  Runner/parser/env failure (download, parse, timeout). Not a blocker-design signal. Investigate and re-run evaluate.";;
esac
