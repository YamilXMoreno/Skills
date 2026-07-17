#!/usr/bin/env bash
# Tear down the HiL-Bench container. Keeps the image and all deliverables.
#
# Usage:
#   teardown.sh [--container NAME] [--stop-only] [--keep-image]
#
#   --stop-only   stop the container but do not remove it (fast resume)
#   --keep-image  (default) never remove the pulled image
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

CONTAINER="${HILBENCH_CONTAINER:-hilbench_task}"
STOP_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --container) CONTAINER="$2"; shift 2 ;;
    --stop-only) STOP_ONLY=1; shift ;;
    --keep-image) shift ;;
    *) hb_die "teardown.sh: unknown arg: $1" ;;
  esac
done

command -v docker >/dev/null 2>&1 || hb_die "docker not found on PATH"

if ! hb_container_exists "$CONTAINER"; then
  echo "TEARDOWN_OK (no container named $CONTAINER)"
  exit 0
fi

if hb_container_running "$CONTAINER"; then
  hb_log "stopping $CONTAINER"
  docker stop "$CONTAINER" >/dev/null || true
fi

if [ "$STOP_ONLY" = "1" ]; then
  echo "TEARDOWN_OK (stopped, not removed; resume with docker start $CONTAINER)"
else
  hb_log "removing $CONTAINER"
  docker rm -f "$CONTAINER" >/dev/null || true
  echo "TEARDOWN_OK (container removed; image + deliverables kept)"
fi
