#!/usr/bin/env bash
# Verify the repo inside the container is checked out at the expected parent commit.
#
# Usage:
#   verify_commit.sh [--container NAME] [--repo /app] [--expected <hash>]
#                    [--checkout] [--no-checkout]
#
# --expected defaults to base_commit_hash parsed from task_info.txt.
# By DEFAULT, if HEAD != expected and the expected commit exists in history, this script
# auto-checks-out the expected commit and re-verifies (date-frozen Dockerfiles routinely land
# on a different commit than the base_commit_hash, so requiring a manual flag just makes the
# first run fail spuriously).
# --checkout    force `git checkout <expected>` up front (legacy; auto-checkout also covers this).
# --no-checkout disable the auto-fix; surface a mismatch as PARENT_COMMIT_MISMATCH instead.
#
# Exit: 0 match; non-zero + STOP line on mismatch/missing.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

CONTAINER="${HILBENCH_CONTAINER:-hilbench_task}"
REPO="${HILBENCH_REPO:-/app}"
EXPECTED=""
FORCE_CHECKOUT=0
AUTO_CHECKOUT=1

while [ $# -gt 0 ]; do
  case "$1" in
    --container)   CONTAINER="$2"; shift 2 ;;
    --repo)        REPO="$2"; shift 2 ;;
    --expected)    EXPECTED="$2"; shift 2 ;;
    --checkout)    FORCE_CHECKOUT=1; shift ;;
    --no-checkout) AUTO_CHECKOUT=0; shift ;;
    *) hb_die "verify_commit.sh: unknown arg: $1" ;;
  esac
done

hb_container_running "$CONTAINER" || hb_die "CONTAINER_NOT_RUNNING ($CONTAINER). Run /hilbench-provision first."

if [ -z "$EXPECTED" ]; then
  TF="$(hb_resolve_task_files)" || hb_die "REQUIRED_INPUT_FILE_MISSING (task_info.txt)"
  EXPECTED="$(hb_field "$TF/task_info.txt" 'base[_ ]?commit[_ ]?hash')" || true
fi
[ -z "$EXPECTED" ] && hb_die "PARENT_COMMIT_MISMATCH: no base_commit_hash provided and none found in task_info.txt"

docker exec "$CONTAINER" git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || hb_die "PARENT_COMMIT_MISMATCH: $REPO in $CONTAINER is not a git work tree"

# hb_commit_exists: is the expected commit present in this repo's object store?
hb_commit_exists() {
  docker exec "$CONTAINER" git -C "$REPO" cat-file -e "${1}^{commit}" >/dev/null 2>&1
}

# hb_do_checkout: checkout expected; STOP on failure.
hb_do_checkout() {
  hb_log "Checking out $EXPECTED in $CONTAINER:$REPO ..."
  docker exec "$CONTAINER" git -C "$REPO" checkout -q "$EXPECTED" \
    || hb_die "PARENT_COMMIT_MISMATCH: could not checkout $EXPECTED"
}

[ "$FORCE_CHECKOUT" = "1" ] && hb_do_checkout

ACTUAL="$(docker exec "$CONTAINER" git -C "$REPO" rev-parse HEAD | tr -d '[:space:]')"
hb_log "expected: $EXPECTED"
hb_log "actual:   $ACTUAL"

case "$ACTUAL" in
  "$EXPECTED"*) hb_log "parent commit OK"; echo "COMMIT_OK $ACTUAL"; exit 0 ;;
esac

# Mismatch. Auto-fix if allowed and the commit is reachable.
if [ "$AUTO_CHECKOUT" = "1" ]; then
  if hb_commit_exists "$EXPECTED"; then
    hb_log "HEAD is not at the base commit; auto-checking out $EXPECTED ..."
    hb_do_checkout
    ACTUAL="$(docker exec "$CONTAINER" git -C "$REPO" rev-parse HEAD | tr -d '[:space:]')"
    hb_log "actual (after checkout): $ACTUAL"
    case "$ACTUAL" in
      "$EXPECTED"*) hb_log "parent commit auto-corrected"; echo "COMMIT_OK $ACTUAL"; exit 0 ;;
      *) hb_die "PARENT_COMMIT_MISMATCH: HEAD=$ACTUAL expected=$EXPECTED even after checkout" ;;
    esac
  fi
  hb_die "PARENT_COMMIT_MISMATCH: HEAD=$ACTUAL expected=$EXPECTED and $EXPECTED is not in this repo's history (re-check the image tag / that the clone includes the base commit)"
fi

hb_die "PARENT_COMMIT_MISMATCH: HEAD=$ACTUAL expected=$EXPECTED (--no-checkout set; not auto-fixing; patches will not apply against the wrong baseline)"
