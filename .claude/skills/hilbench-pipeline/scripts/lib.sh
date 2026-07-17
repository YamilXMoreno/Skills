#!/usr/bin/env bash
# Shared helpers for hilbench-pipeline scripts. Source this; do not execute directly.
# Intentionally dependency-light (bash + coreutils + docker + git).

hb_log()  { printf '  %s\n' "$*" >&2; }
hb_head() { printf '\n=== %s ===\n' "$*" >&2; }
hb_die()  { printf 'STOP - %s\n' "$*" >&2; exit 1; }

# Resolve the directory that holds task_info.txt. Prints the dir on stdout.
hb_resolve_task_files() {
  local c
  if [ -n "${HILBENCH_TASK_FILES:-}" ] && [ -f "$HILBENCH_TASK_FILES/task_info.txt" ]; then
    printf '%s\n' "$HILBENCH_TASK_FILES"; return 0
  fi
  for c in "/app/task_files" "/home/sandbox/task_files" "./task_files" "."; do
    if [ -f "$c/task_info.txt" ]; then printf '%s\n' "$c"; return 0; fi
  done
  c="$(find . /app /home/sandbox -maxdepth 3 -name task_info.txt 2>/dev/null | head -n1)"
  if [ -n "$c" ]; then printf '%s\n' "$(dirname "$c")"; return 0; fi
  return 1
}

# Resolve the deliverables output dir (created if missing). Prints the dir.
hb_resolve_deliverables() {
  local tf="$1" d
  if [ -n "${HILBENCH_DELIVERABLES:-}" ]; then d="$HILBENCH_DELIVERABLES"
  else d="$(cd "$tf/.." && pwd)/deliverables"; fi
  mkdir -p "$d"
  printf '%s\n' "$d"
}

# Extract a scalar field from task_info.txt. Tolerates "key: value", "key = value",
# "key <value>", and simple markdown table rows "| key | value |".
# Usage: hb_field <task_info_path> <key_regex>
hb_field() {
  local file="$1" key="$2" line val
  line="$(grep -iE "$key" "$file" 2>/dev/null | head -n1 || true)"
  [ -z "$line" ] && return 1
  # markdown table row
  if printf '%s' "$line" | grep -q '|'; then
    val="$(printf '%s' "$line" | awk -F'|' '{print $3}' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//; s/`//g')"
  else
    val="$(printf '%s' "$line" | sed -E "s/.*${key}[[:space:]]*[:=]?[[:space:]]*//I; s/^[<\"'\`]+//; s/[>\"'\`]+$//; s/[[:space:]]+$//")"
  fi
  [ -n "$val" ] && printf '%s\n' "$val"
}

# Extract the docker image SOURCE from task_info.txt. This may be a bare registry ref,
# a literal "docker pull <ref>" command, OR a URL (S3 etc.) that points to a Dockerfile /
# image tarball / pull command. Resolution of a URL into a usable image is done by
# hb_resolve_image below; this function only returns the raw value.
hb_image_from_task_info() {
  local file="$1" line img
  if [ -n "${HILBENCH_IMAGE:-}" ]; then printf '%s\n' "$HILBENCH_IMAGE"; return 0; fi
  line="$(grep -iE 'docker[ _]?pull' "$file" 2>/dev/null | head -n1 || true)"
  if [ -n "$line" ]; then
    img="$(printf '%s' "$line" | grep -oE 'docker[[:space:]]+pull[[:space:]]+[^[:space:]`|]+' | head -n1 | awk '{print $3}')"
    [ -z "$img" ] && img="$(hb_field "$file" 'docker[_ ]?pull[_ ]?command')"
    [ -n "$img" ] && { printf '%s\n' "$img"; return 0; }
  fi
  img="$(hb_field "$file" 'image')" || true
  [ -n "$img" ] && { printf '%s\n' "$img"; return 0; }
  return 1
}

# Derive a deterministic, docker-legal local image tag from an instance id.
# Lowercased, non [a-z0-9._-] collapsed to '-'. Used so URL-sourced builds are idempotent.
hb_derive_tag() {
  local id="${1:-}" slug
  slug="$(printf '%s' "$id" | tr '[:upper:]' '[:lower:]' | sed -E 's#[^a-z0-9._-]+#-#g; s#^[-.]+##; s#[-.]+$##')"
  [ -z "$slug" ] && slug="task"
  printf 'hilbench/%s:base\n' "$slug"
}

# Resolve a raw image source value into a locally-usable image ref, building/loading/pulling
# as needed. Prints ONLY the final image ref on stdout (all progress goes to stderr).
# Args: <raw_value> <instance_id>
# Decision tree:
#   "docker pull <ref>"        -> extract <ref>            (pulled later by the caller)
#   URL (http/https)           -> fetch, then sniff payload:
#        first non-comment line "FROM ..."  -> docker build -t <derived-tag>
#        gzip/tar (or docker load works)    -> docker load, use loaded ref (also tagged)
#        body contains "docker pull <ref>"  -> pull <ref>
#        else                               -> STOP UNRECOGNIZED_IMAGE_SOURCE
#   bare registry ref          -> return as-is             (pulled later by the caller)
#
# Build helper: some sandboxes run rootless Docker where the default buildx builder
# fails to initialize (it cannot chown ~/.docker/buildx/instances under an ACL mask ->
# "operation not permitted"). Try the default builder, then retry once with the legacy
# builder (DOCKER_BUILDKIT=0), which bypasses buildx. Non-zero only if BOTH fail.
# Args: <tag> <dockerfile> <context_dir>
hb_docker_build() {
  local tag="$1" dockerfile="$2" ctx="$3"
  docker build -t "$tag" -f "$dockerfile" "$ctx" >&2 && return 0
  hb_log "default docker build failed; retrying with legacy builder (DOCKER_BUILDKIT=0) — common under rootless Docker/buildx"
  DOCKER_BUILDKIT=0 docker build -t "$tag" -f "$dockerfile" "$ctx" >&2 && return 0
  return 1
}

# Short, portable content hash of a file (first 12 hex chars). Used to tag deliverables-built
# images so an edited Dockerfile produces a NEW tag and actually rebuilds (rather than reusing
# a stale image tagged only by instance_id).
hb_file_hash() {
  local f="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$f" | awk '{print substr($1,1,12)}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$f" | awk '{print substr($1,1,12)}'
  else
    cksum "$f" | awk '{print $1}'
  fi
}

# Is <path> a real, buildable Dockerfile (not the auto-generated tarball-stub comment or an
# empty placeholder)? True iff the file exists and has a real "FROM ..." instruction.
hb_dockerfile_is_buildable() {
  local df="$1"
  [ -f "$df" ] || return 1
  grep -qiE '^[[:space:]]*FROM[[:space:]]' "$df" 2>/dev/null || return 1
  return 0
}

# Build an image from a corrected deliverables/Dockerfile. Tag = derived instance slug +
# content hash, so edits rebuild and unchanged files are idempotent. Uses the Dockerfile's
# own directory as the build context. Prints ONLY the final image ref on stdout.
# Args: <dockerfile_path> <instance_id>
hb_build_deliverables_dockerfile() {
  local df="$1" instance_id="${2:-}" ctx tag sum
  ctx="$(cd "$(dirname "$df")" && pwd)"
  df="$ctx/$(basename "$df")"
  sum="$(hb_file_hash "$df")"
  tag="$(hb_derive_tag "$instance_id")"   # hilbench/<slug>:base
  tag="${tag%:*}:deliv-${sum}"            # hilbench/<slug>:deliv-<sum>
  if docker image inspect "$tag" >/dev/null 2>&1; then
    hb_log "reusing already-built deliverables image $tag (idempotent; Dockerfile unchanged)"
    printf '%s\n' "$tag"; return 0
  fi
  hb_log "building image from corrected deliverables/Dockerfile (context: $ctx) -> $tag"
  if ! hb_docker_build "$tag" "$df" "$ctx"; then
    hb_die "IMAGE_BUILD_FAILED: docker build failed for deliverables/Dockerfile (tried the default builder and the legacy DOCKER_BUILDKIT=0 builder). This is a build/environment failure in the corrected Dockerfile."
  fi
  printf '%s\n' "$tag"
}

hb_resolve_image() {
  local raw="$1" instance_id="${2:-}" ref tag tmp body first ctype loaded
  case "$raw" in
    "docker pull "*|*"docker pull "*)
      ref="$(printf '%s' "$raw" | grep -oE 'docker[[:space:]]+pull[[:space:]]+[^[:space:]|`]+' | head -n1 | awk '{print $3}')"
      [ -n "$ref" ] && { printf '%s\n' "$ref"; return 0; }
      ;;
  esac

  case "$raw" in
    http://*|https://*)
      tag="$(hb_derive_tag "$instance_id")"
      if docker image inspect "$tag" >/dev/null 2>&1; then
        hb_log "reusing already-built image $tag (idempotent)"
        printf '%s\n' "$tag"; return 0
      fi
      tmp="$(mktemp -d)"; body="$tmp/payload"
      hb_log "fetching image source: $raw"
      if ! { curl -fsSL "$raw" -o "$body" 2>/dev/null || wget -qO "$body" "$raw" 2>/dev/null; }; then
        rm -rf "$tmp"; hb_die "UNRECOGNIZED_IMAGE_SOURCE: could not fetch $raw (need curl or wget + network)"
      fi
      first="$(grep -vE '^[[:space:]]*(#|$)' "$body" 2>/dev/null | head -n1 || true)"
      if printf '%s' "$first" | grep -qiE '^[[:space:]]*FROM[[:space:]]'; then
        hb_log "source is a Dockerfile; building $tag ..."
        if ! hb_docker_build "$tag" "$body" "$tmp"; then
          rm -rf "$tmp"; hb_die "IMAGE_BUILD_FAILED: docker build failed for $raw (tried the default builder and the legacy DOCKER_BUILDKIT=0 builder). The source WAS recognized as a Dockerfile — this is a build/environment failure, not a bad source URL."
        fi
        rm -rf "$tmp"; printf '%s\n' "$tag"; return 0
      fi
      if file "$body" 2>/dev/null | grep -qiE 'gzip|tar archive|POSIX tar'; then
        hb_log "source is an image tarball; docker load ..."
        local loadout
        loadout="$(docker load -i "$body" 2>/dev/null)"
        # Case A: "Loaded image: repo:tag" -> use that ref, also alias to $tag.
        loaded="$(printf '%s\n' "$loadout" | grep -oiE 'Loaded image: .*' | sed -E 's/[Ll]oaded image: *//' | head -n1)"
        if [ -n "$loaded" ]; then
          docker tag "$loaded" "$tag" >/dev/null 2>&1 || true
          rm -rf "$tmp"; printf '%s\n' "$loaded"; return 0
        fi
        # Case B: "Loaded image ID: sha256:..." (untagged) -> tag the sha with $tag and use it.
        loaded="$(printf '%s\n' "$loadout" | grep -oiE 'sha256:[0-9a-f]+' | head -n1)"
        if [ -n "$loaded" ]; then
          docker tag "$loaded" "$tag" >/dev/null 2>&1
          rm -rf "$tmp"; printf '%s\n' "$tag"; return 0
        fi
        rm -rf "$tmp"; hb_die "IMAGE_LOAD_FAILED: docker load produced no image from $raw (the source WAS recognized as an image tarball — this is a load/environment failure, not a bad source URL)."
      fi
      ref="$(grep -oE 'docker[[:space:]]+pull[[:space:]]+[^[:space:]|`]+' "$body" 2>/dev/null | head -n1 | awk '{print $3}')"
      if [ -n "$ref" ]; then
        rm -rf "$tmp"; printf '%s\n' "$ref"; return 0
      fi
      ctype="$(file -b "$body" 2>/dev/null || echo unknown)"
      rm -rf "$tmp"
      hb_die "UNRECOGNIZED_IMAGE_SOURCE: fetched $raw but could not classify it (looks like: $ctype)"
      ;;
  esac

  # Bare registry ref (or already-local ref): return unchanged; caller pulls if absent.
  printf '%s\n' "$raw"; return 0
}

hb_container_running() {
  local name="$1"
  [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || echo false)" = "true" ]
}

hb_container_exists() {
  docker inspect "$1" >/dev/null 2>&1
}
