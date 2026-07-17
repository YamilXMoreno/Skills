#!/usr/bin/env bash
# Prepare the container for a scripted Attempter Check solve (Check 1 or Check 2).
#
# Establishes a CLEAN, COMMITTED baseline the solving agent works from, and SCRUBS the
# answer-key files (test/golden patches, relevant tests, registry) from the container so
# the agent that solves via `docker exec` cannot read them. Closes gap G7.
#
# Usage:  prepare_check.sh <check1|check2> [--container NAME] [--repo /app] [--commit HASH]
#
# After this returns CHECK_PREPARED, the caller launches a FRESH subagent to solve in the
# container, then runs evaluate_check.sh <mode>.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

MODE="${1:-}"; shift || true
case "$MODE" in check1|check2) ;; *) hb_die "prepare_check.sh: first arg must be check1 or check2";; esac

CONTAINER="${HILBENCH_CONTAINER:-hilbench_task}"
REPO="${HILBENCH_REPO:-/app}"
COMMIT="${HILBENCH_BASE_COMMIT:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --container) CONTAINER="$2"; shift 2 ;;
    --repo)      REPO="$2"; shift 2 ;;
    --commit)    COMMIT="$2"; shift 2 ;;
    *) hb_die "prepare_check.sh: unknown arg: $1" ;;
  esac
done

TF="$(hb_resolve_task_files)" || hb_die "REQUIRED_INPUT_FILE_MISSING (task_info.txt)"
TF="$(cd "$TF" && pwd)"
DELIV="$(hb_resolve_deliverables "$TF")"
# Load provisioned context if present.
[ -f "$DELIV/.hilbench_env" ] && . "$DELIV/.hilbench_env"
CONTAINER="${HILBENCH_CONTAINER:-$CONTAINER}"
REPO="${HILBENCH_REPO:-$REPO}"
[ -z "$COMMIT" ] && COMMIT="${HILBENCH_BASE_COMMIT:-$(hb_field "$TF/task_info.txt" 'base[_ ]?commit[_ ]?hash' || true)}"

hb_container_running "$CONTAINER" || hb_die "CONTAINER_NOT_RUNNING ($CONTAINER). Run /hilbench-provision first."
[ -z "$COMMIT" ] && hb_die "PARENT_COMMIT_MISMATCH: base commit unknown; re-run /hilbench-provision"

hb_head "Reset repo to parent commit ($MODE)"
docker exec "$CONTAINER" git -C "$REPO" reset --hard "$COMMIT" >/dev/null 2>&1 \
  || hb_die "PARENT_COMMIT_MISMATCH: could not reset $REPO to $COMMIT"
# Remove untracked leftovers from any prior run, but keep task_files/ (runner + parser).
docker exec "$CONTAINER" git -C "$REPO" clean -fdq -e task_files -e .hilbench_stash 2>/dev/null || true

hb_head "Bake setup_patch as the working baseline (if present)"
if [ -f "$DELIV/setup_patch.diff" ] && [ -s "$DELIV/setup_patch.diff" ]; then
  docker exec "$CONTAINER" mkdir -p "$REPO/.hilbench_stash"
  docker cp "$DELIV/setup_patch.diff" "$CONTAINER:$REPO/.hilbench_stash/setup_patch.diff"
  docker exec "$CONTAINER" git -C "$REPO" apply -v --ignore-whitespace ".hilbench_stash/setup_patch.diff" \
    || hb_die "CHECK_ERROR_PATCH: setup_patch.diff did not apply on the parent commit"
  # Remove the patch before git add/commit. Committing it would let the solving agent recover
  # every removed clue with git show even if the working-tree copy were deleted afterward.
  docker exec "$CONTAINER" rm -f "$REPO/.hilbench_stash/setup_patch.diff"
  docker exec "$CONTAINER" rmdir "$REPO/.hilbench_stash" 2>/dev/null || true
  docker exec "$CONTAINER" sh -lc "cd '$REPO' && git -c user.email=hb@local -c user.name=hilbench add -A && git -c user.email=hb@local -c user.name=hilbench commit -q -m 'hilbench baseline (parent + setup)'" \
    || hb_die "CHECK_ERROR_ENV: could not commit setup baseline"
  hb_log "setup_patch applied and committed as baseline"
else
  hb_log "no setup_patch.diff; baseline is the parent commit"
fi

BASELINE="$(docker exec "$CONTAINER" git -C "$REPO" rev-parse HEAD | tr -d '[:space:]')"
printf '%s\n' "$BASELINE" > "$DELIV/.hilbench_check_baseline"
hb_log "baseline commit: $BASELINE"

hb_head "Scrub answer-key files from the container work tree"
# The solving agent operates via docker exec on $REPO; ensure it cannot see the answer key.
for f in test_patch.diff test_patch_obstructed.diff golden_patch.diff golden_patch_obstructed.diff \
         relevant_tests.txt blocker_registry.json blocker_registry.md agent_patch.diff \
         check1_agent_patch.diff check2_agent_patch.diff; do
  docker exec "$CONTAINER" sh -lc "rm -f '$REPO/$f' '$REPO/task_files/$f'" 2>/dev/null || true
done
hb_log "removed any stray test/golden/registry/relevant_tests files from $REPO and $REPO/task_files"

echo
echo "CHECK_PREPARED $MODE"
echo "container=$CONTAINER repo=$REPO baseline=$BASELINE"
echo
echo "Scoped inputs the solving subagent MAY receive:"
echo "  - \$DELIVERABLES/modified_problem_statement.txt"
echo "  - \$DELIVERABLES/modified_requirements.txt"
echo "  - \$DELIVERABLES/modified_public_interfaces.txt"
if [ "$MODE" = "check2" ]; then
  echo "  - blocker RESOLUTIONS (resolution text only) from \$DELIVERABLES/blocker_registry.md"
fi
echo "MUST NOT be shared with the solving subagent:"
echo "  - test_patch* , golden_patch* , relevant_tests.txt"
[ "$MODE" = "check1" ] && echo "  - blocker resolutions / blocker_registry.* (Check 1 is resolution-free)"
