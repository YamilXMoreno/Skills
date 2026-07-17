#!/usr/bin/env bash
# Provision the SWEAP repo/container for a HiL-Bench task and verify the parent commit.
#
# Closes gaps G1 (no repo), G2 (no parent-commit verification), G4 (HILBENCH_* env).
#
# What it does:
#   1. Resolve task_files + deliverables.
#   2. Parse docker image SOURCE, base_commit_hash, instance_id from task_info.txt (env/flag override).
#   3. Resolve the image source into a usable ref (build from a Dockerfile URL / docker load a
#      tarball / extract a pull command), then docker pull the ref once (skip if present).
#   4. Start a long-lived container (reuse if already running).
#   5. Locate the repo work tree (default /app) and verify HEAD == base_commit_hash, auto-checking
#      out the base commit if the built/pulled repo is not already at it.
#   6. Install/verify manifest-declared repo dependencies and snapshot a corrected runtime image.
#   7. Write deliverables/.hilbench_env with the HILBENCH_* exports and print them.
#
# Usage:
#   provision_repo.sh [--image REF] [--commit HASH] [--instance-id ID]
#                     [--container NAME] [--repo /app] [--checkout] [--no-checkout]
#                     [--skip-dependency-check]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

IMAGE="" COMMIT="" INSTANCE_ID="" REPO="${HILBENCH_REPO:-/app}"
CONTAINER="${HILBENCH_CONTAINER:-hilbench_task}"
DO_CHECKOUT=0 NO_CHECKOUT=0
SKIP_DEPENDENCY_CHECK=0

while [ $# -gt 0 ]; do
  case "$1" in
    --image)       IMAGE="$2"; shift 2 ;;
    --commit)      COMMIT="$2"; shift 2 ;;
    --instance-id) INSTANCE_ID="$2"; shift 2 ;;
    --container)   CONTAINER="$2"; shift 2 ;;
    --repo)        REPO="$2"; shift 2 ;;
    --checkout)    DO_CHECKOUT=1; shift ;;
    --no-checkout) NO_CHECKOUT=1; shift ;;
    --skip-dependency-check) SKIP_DEPENDENCY_CHECK=1; shift ;;
    --validate-original) shift ;;  # convenience flag handled by the /hilbench-provision command; ignored here
    *) hb_die "provision_repo.sh: unknown arg: $1" ;;
  esac
done

command -v docker >/dev/null 2>&1 || hb_die "docker not found on PATH (repo provisioning needs Docker)"

hb_head "Resolve inputs"
TF="$(hb_resolve_task_files)" || hb_die "REQUIRED_INPUT_FILE_MISSING (task_info.txt not found)"
TF="$(cd "$TF" && pwd)"
DELIV="$(hb_resolve_deliverables "$TF")"
INFO="$TF/task_info.txt"
hb_log "task_files:    $TF"
hb_log "deliverables:  $DELIV"

[ -z "$INSTANCE_ID" ] && INSTANCE_ID="${HILBENCH_INSTANCE_ID:-$(hb_field "$INFO" 'instance[_ ]?id' || true)}"

# A corrected build-from-source Dockerfile in deliverables/ OVERRIDES the task_info image
# source (precedence: --image/HILBENCH_IMAGE > deliverables/Dockerfile > task_info source).
# This is how a reviewer/contributor fixes an image that is missing dependencies: drop a real
# Dockerfile in deliverables/ and re-provision — the built image is what every later stage
# (and the grade) runs against. The tarball-stub / empty placeholder is ignored (no FROM).
USE_DELIV_DOCKERFILE=0
DELIV_DOCKERFILE="$DELIV/Dockerfile"
# Explicit override (flag or env) wins over everything, including deliverables/Dockerfile.
[ -z "$IMAGE" ] && [ -n "${HILBENCH_IMAGE:-}" ] && IMAGE="$HILBENCH_IMAGE"
if [ -z "$IMAGE" ] && hb_dockerfile_is_buildable "$DELIV_DOCKERFILE"; then
  USE_DELIV_DOCKERFILE=1
  hb_log "found a buildable deliverables/Dockerfile; it OVERRIDES the task_info image source"
else
  [ -z "$IMAGE" ] && IMAGE="$(hb_image_from_task_info "$INFO" || true)"
fi
[ -z "$COMMIT" ]      && COMMIT="$(hb_field "$INFO" 'base[_ ]?commit[_ ]?hash' || true)"

[ -z "$IMAGE" ] && [ "$USE_DELIV_DOCKERFILE" = 0 ] && hb_die "REQUIRED_INPUT_FILE_MISSING: no docker image (set --image or HILBENCH_IMAGE, add a buildable deliverables/Dockerfile, or add docker_pull_command to task_info.txt)"
[ -z "$COMMIT" ] && hb_die "PARENT_COMMIT_MISMATCH: no base_commit_hash (set --commit or add base_commit_hash to task_info.txt)"
[ -z "$INSTANCE_ID" ] && hb_log "WARNING: instance_id not found; checks/eval will need --instance-id explicitly (INSTANCE_ID_MISSING)."

hb_log "image source:  $([ "$USE_DELIV_DOCKERFILE" = 1 ] && echo "$DELIV_DOCKERFILE (deliverables override)" || echo "$IMAGE")"
hb_log "base_commit:   $COMMIT"
hb_log "instance_id:   ${INSTANCE_ID:-<unset>}"
hb_log "container:     $CONTAINER"

hb_head "Resolve image source"
if [ "$USE_DELIV_DOCKERFILE" = 1 ]; then
  # Build from the corrected deliverables/Dockerfile (content-hashed tag => edits rebuild).
  if ! IMAGE_RESOLVED="$(hb_build_deliverables_dockerfile "$DELIV_DOCKERFILE" "$INSTANCE_ID")"; then
    exit 1
  fi
else
  # Turn the raw source (registry ref | docker pull cmd | URL to Dockerfile/tarball/pull cmd)
  # into a usable local ref, building/loading as needed. STOPs on UNRECOGNIZED_IMAGE_SOURCE.
  if ! IMAGE_RESOLVED="$(hb_resolve_image "$IMAGE" "$INSTANCE_ID")"; then
    exit 1
  fi
fi
IMAGE="$IMAGE_RESOLVED"
hb_log "resolved image: $IMAGE"

hb_head "Pull image (once)"
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  hb_log "image already present; skipping pull"
else
  docker pull "$IMAGE" || docker pull --platform linux/amd64 "$IMAGE" \
    || hb_die "failed to docker pull $IMAGE"
fi

hb_head "Start container"
if hb_container_running "$CONTAINER"; then
  hb_log "container already running; reusing"
else
  hb_container_exists "$CONTAINER" && { hb_log "removing stopped container"; docker rm -f "$CONTAINER" >/dev/null; }
  # Keep it alive regardless of the image's default entrypoint. Always override the
  # entrypoint to sleep (some images set ENTRYPOINT ["/bin/bash"], which would treat
  # "sleep infinity" as a script and exit immediately). Try native arch first (locally
  # built images may be arm64), then fall back to forcing linux/amd64 for pulled images.
  docker run -d --name "$CONTAINER" -w "$REPO" \
    --entrypoint sleep "$IMAGE" infinity >/dev/null 2>&1 \
    || docker run -d --name "$CONTAINER" --platform linux/amd64 -w "$REPO" \
       --entrypoint sleep "$IMAGE" infinity >/dev/null 2>&1 \
    || hb_die "failed to start container from $IMAGE"
  hb_log "started $CONTAINER"
fi

hb_head "Locate repo work tree"
if ! docker exec "$CONTAINER" git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  hb_log "$REPO is not a git work tree; searching ..."
  FOUND="$(docker exec "$CONTAINER" sh -lc 'for d in /app /workspace /repo /src; do [ -d "$d/.git" ] && echo "$d" && break; done' 2>/dev/null | head -n1 || true)"
  [ -z "$FOUND" ] && hb_die "PARENT_COMMIT_MISMATCH: could not find a git repo in the container (tried /app /workspace /repo /src)"
  REPO="$FOUND"; hb_log "using repo: $REPO"
fi

hb_head "Verify parent commit"
# By default verify_commit.sh auto-checks-out the base commit when HEAD differs (common for
# date-frozen Dockerfiles that reset to a different commit). --checkout forces a checkout up
# front; --no-checkout disables the auto-fix and surfaces the mismatch instead.
CK=()
[ "$DO_CHECKOUT" = "1" ] && CK+=(--checkout)
[ "$NO_CHECKOUT" = "1" ] && CK+=(--no-checkout)
if [ "${#CK[@]}" -gt 0 ]; then
  HILBENCH_CONTAINER="$CONTAINER" HILBENCH_REPO="$REPO" \
    bash "$SCRIPT_DIR/verify_commit.sh" --container "$CONTAINER" --repo "$REPO" --expected "$COMMIT" "${CK[@]}"
else
  HILBENCH_CONTAINER="$CONTAINER" HILBENCH_REPO="$REPO" \
    bash "$SCRIPT_DIR/verify_commit.sh" --container "$CONTAINER" --repo "$REPO" --expected "$COMMIT"
fi

if [ "$SKIP_DEPENDENCY_CHECK" = "0" ]; then
  hb_head "Install and verify repository dependencies"
  DEP_LOG="$DELIV/dependency_install.log"
  set +e
  DEP_OUTPUT="$(HILBENCH_CONTAINER="$CONTAINER" HILBENCH_REPO="$REPO" \
    bash "$SCRIPT_DIR/ensure_repo_dependencies.sh" --container "$CONTAINER" --repo "$REPO" 2>&1)"
  DEP_CODE=$?
  set -e
  printf '%s\n' "$DEP_OUTPUT" | tee "$DEP_LOG"
  [ "$DEP_CODE" -eq 0 ] || exit "$DEP_CODE"

  case "$DEP_OUTPUT" in
    *"DEPENDENCIES_READY"*)
      hb_head "Snapshot dependency-complete runtime image"
      IMAGE_ID="$(docker commit "$CONTAINER")" \
        || hb_die "DEPENDENCY_IMAGE_COMMIT_FAILED: could not snapshot dependency-complete container"
      SHORT_ID="${IMAGE_ID#sha256:}"
      SHORT_ID="${SHORT_ID:0:12}"
      DEPS_TAG="$(hb_derive_tag "$INSTANCE_ID")"
      DEPS_TAG="${DEPS_TAG%:*}:deps-${SHORT_ID}"
      docker tag "$IMAGE_ID" "$DEPS_TAG" \
        || hb_die "DEPENDENCY_IMAGE_COMMIT_FAILED: could not tag $IMAGE_ID as $DEPS_TAG"
      IMAGE="$DEPS_TAG"
      hb_log "dependency-complete image: $IMAGE"
      ;;
    *"DEPENDENCIES_NOT_APPLICABLE"*)
      hb_log "no supported dependency manifest; runtime image unchanged"
      ;;
  esac
else
  hb_log "WARNING: dependency audit skipped by --skip-dependency-check"
fi

hb_head "Export environment"
ENV_FILE="$DELIV/.hilbench_env"
cat > "$ENV_FILE" <<EOF
export HILBENCH_TASK_FILES="$TF"
export HILBENCH_DELIVERABLES="$DELIV"
export HILBENCH_CONTAINER="$CONTAINER"
export HILBENCH_REPO="$REPO"
export HILBENCH_IMAGE="$IMAGE"
export HILBENCH_BASE_COMMIT="$COMMIT"
export HILBENCH_INSTANCE_ID="$INSTANCE_ID"
EOF
hb_log "wrote $ENV_FILE"
echo
echo "PROVISION_OK"
echo "Run this in your shell to load task context:"
echo "  source \"$ENV_FILE\""
cat "$ENV_FILE"
