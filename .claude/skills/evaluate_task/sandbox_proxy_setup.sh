#!/usr/bin/env bash
set -uo pipefail

###############################################################################
# sandbox_proxy_setup.sh  —  route the HiL-Bench SWE-Agent runs through the
# sandbox "blind" anonymizer proxy (the same hardening tb-trial-runner uses).
#
# WHY: evaluate_task runs `harbor run ... -a swe-agent -m <model>` for three
# providers (openai/gpt-5.4, anthropic/claude-sonnet-4-6, gemini/gemini-3.1-pro).
# The sandbox has NO per-provider keys; it has an anonymizer proxy on host
# loopback (127.0.0.1) whose placeholder key lives in ~/.claude/settings.json.
# A bare harbor run lets litellm resolve provider-direct keys from the ambient
# container env — so gemini hard-fails (`GEMINI_API_KEY unset`) and the others
# only "work" by accident. This script fixes that for the SWE-Agent path only.
#
# It does three things (mirroring tb-trial-runner/run_trials.sh):
#   1. Resolves the anonymizer creds env-first, then ~/.claude/settings.json .env
#      (ANTHROPIC_BASE_URL / OPENAI_BASE_URL + placeholder key). One shared key.
#   2. Rewrites each loopback proxy base URL to the rootless-docker host alias
#      10.0.2.2 (slirp host-loopback), keeping the same port/path, so the
#      swe-agent CONTAINER can reach the host proxy. No on-host forwarder.
#   3. Preflight-probes the rewritten base URL from inside a throwaway container.
#
# It then writes two artifacts the SKILL.md flow embeds into each harbor JobConfig:
#   <out>/.hilbench_proxy_env.sh        — `source` it (exports keys + base URLs)
#   <out>/.hilbench_proxy_agent_env.json — the JobConfig `agents[].env` block
#
# IMPORTANT: this touches ONLY the SWE-Agent agentic-check path. It does NOT
# touch the LLM-judge/eval path (custom_eval.py -> LITELLM_BASE_URL /
# PUBLIC_LITELLM_BASE_URL with HIL_BENCH_* keys), which is a separate mechanism.
#
# Usage:
#   sandbox_proxy_setup.sh [--out-dir DIR] [--backend docker|modal]
#                          [--no-bridge] [--no-preflight] [--host-alias IP]
#                          [--probe-openai-model M] [--probe-anthropic-model M]
#   sandbox_proxy_setup.sh --stop [--out-dir DIR]     # no-op teardown (kept for compat)
#
# --no-bridge disables the rewrite (use the loopback base URL as-is).
#
# ASSUMPTION for the FDE to confirm: the anonymizer proxy is OpenAI- and
# Anthropic-compatible on one endpoint, and litellm's gemini calls are routed
# by pointing GEMINI_API_BASE at that same (rewritten) proxy base. If gemini uses
# a different endpoint, set GEMINI_API_BASE in the env / settings.json and it is
# honored below.
###############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BACKEND="docker"
USE_BRIDGE="true"
PREFLIGHT="true"
OUT_DIR="."
HOST_ALIAS="${SANDBOX_HOST_ALIAS:-10.0.2.2}"
MODE="setup"
PROBE_OPENAI_MODEL="gpt-5.4"
PROBE_ANTHROPIC_MODEL="claude-sonnet-4-6"
SETTINGS_JSON="${SETTINGS_JSON:-$HOME/.claude/settings.json}"
# This sandbox exposes TWO anonymizer endpoints on the same host: an OpenAI-format
# one (serves gpt-5.4 AND gemini via /v1/chat/completions) and an Anthropic-format
# one (serves claude via /v1/messages). settings.json only lists the Anthropic base,
# so we derive the OpenAI/Gemini base from the same host on OPENAI_PORT with a /v1
# path. Override any of these explicitly with the flags / env vars below.
OPENAI_PORT="${HILBENCH_OPENAI_PORT:-8090}"
OPENAI_PATH="${HILBENCH_OPENAI_PATH:-/v1}"
OPENAI_BASE_OVERRIDE=""
GEMINI_BASE_OVERRIDE=""
ANTHROPIC_BASE_OVERRIDE=""

log()  { echo "[proxy-setup] $*"; }
warn() { echo "[proxy-setup] WARNING: $*" >&2; }
die()  { echo "[proxy-setup] FATAL: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --out-dir)                OUT_DIR="${2:?}"; shift 2 ;;
    --out-dir=*)              OUT_DIR="${1#*=}"; shift ;;
    --backend)                BACKEND="${2:?}"; shift 2 ;;
    --backend=*)              BACKEND="${1#*=}"; shift ;;
    --no-bridge)              USE_BRIDGE="false"; shift ;;
    --no-preflight)           PREFLIGHT="false"; shift ;;
    --host-alias)             HOST_ALIAS="${2:?}"; shift 2 ;;
    --host-alias=*)           HOST_ALIAS="${1#*=}"; shift ;;
    --probe-openai-model)     PROBE_OPENAI_MODEL="${2:?}"; shift 2 ;;
    --probe-openai-model=*)   PROBE_OPENAI_MODEL="${1#*=}"; shift ;;
    --probe-anthropic-model)  PROBE_ANTHROPIC_MODEL="${2:?}"; shift 2 ;;
    --probe-anthropic-model=*) PROBE_ANTHROPIC_MODEL="${1#*=}"; shift ;;
    --openai-port)            OPENAI_PORT="${2:?}"; shift 2 ;;
    --openai-port=*)          OPENAI_PORT="${1#*=}"; shift ;;
    --openai-base)            OPENAI_BASE_OVERRIDE="${2:?}"; shift 2 ;;
    --openai-base=*)          OPENAI_BASE_OVERRIDE="${1#*=}"; shift ;;
    --gemini-base)            GEMINI_BASE_OVERRIDE="${2:?}"; shift 2 ;;
    --gemini-base=*)          GEMINI_BASE_OVERRIDE="${1#*=}"; shift ;;
    --anthropic-base)         ANTHROPIC_BASE_OVERRIDE="${2:?}"; shift 2 ;;
    --anthropic-base=*)       ANTHROPIC_BASE_OVERRIDE="${1#*=}"; shift ;;
    --stop)                   MODE="stop"; shift ;;
    -h|--help)                sed -n '4,45p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)                        die "unknown arg: $1" ;;
  esac
done

mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
PIDS_FILE="$OUT_DIR/.hilbench_proxy_bridges.pids"
ENV_SH="$OUT_DIR/.hilbench_proxy_env.sh"
ENV_JSON="$OUT_DIR/.hilbench_proxy_agent_env.json"

command -v python3 >/dev/null 2>&1 || die "python3 is required"

# ---------------------------------------------------------------------------
# --stop: tear down any bridges we started, then exit.
# ---------------------------------------------------------------------------
if [ "$MODE" = "stop" ]; then
  if [ -f "$PIDS_FILE" ]; then
    while read -r pid _; do
      [ -n "$pid" ] || continue
      kill "$pid" 2>/dev/null && log "stopped bridge pid $pid" || true
    done < "$PIDS_FILE"
    rm -f "$PIDS_FILE"
  else
    log "no bridge pid file at $PIDS_FILE; nothing to stop"
  fi
  exit 0
fi

case "$BACKEND" in docker|modal) ;; *) die "--backend must be docker or modal";; esac

# ---------------------------------------------------------------------------
# 1) Credential resolution: live env first, then settings.json .env block.
# ---------------------------------------------------------------------------
read_settings_env() {  # $1 = key -> value from settings.json .env, or empty
  [ -f "$SETTINGS_JSON" ] || return 0
  python3 - "$SETTINGS_JSON" "$1" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
v = (d.get("env") or {}).get(sys.argv[2])
if v:
    print(v)
PY
}

: "${ANTHROPIC_API_KEY:=$(read_settings_env ANTHROPIC_API_KEY)}"
: "${ANTHROPIC_BASE_URL:=$(read_settings_env ANTHROPIC_BASE_URL)}"
: "${OPENAI_API_KEY:=$(read_settings_env OPENAI_API_KEY)}"
: "${OPENAI_BASE_URL:=$(read_settings_env OPENAI_BASE_URL)}"
: "${OPENAI_API_BASE:=$(read_settings_env OPENAI_API_BASE)}"
: "${GEMINI_API_KEY:=$(read_settings_env GEMINI_API_KEY)}"
: "${GEMINI_API_BASE:=$(read_settings_env GEMINI_API_BASE)}"

# Shared placeholder key (the sandbox issues one anonymizer key for all providers).
ANON_KEY="${ANTHROPIC_API_KEY:-${OPENAI_API_KEY:-${GEMINI_API_KEY:-}}}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-$ANON_KEY}"
OPENAI_API_KEY="${OPENAI_API_KEY:-$ANON_KEY}"
GEMINI_API_KEY="${GEMINI_API_KEY:-$ANON_KEY}"

# Anthropic base: explicit override > env/settings.json > OpenAI base as last resort.
ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_OVERRIDE:-${ANTHROPIC_BASE_URL:-${OPENAI_BASE_URL:-${OPENAI_API_BASE:-}}}}"

# OpenAI-format anonymizer (serves gpt-5.4 AND gemini). Precedence:
#   --openai-base / OPENAI_BASE_URL / OPENAI_API_BASE, else DERIVE it from the
#   anthropic host on OPENAI_PORT (default 8090) with the OPENAI_PATH (default /v1).
if [ -n "$OPENAI_BASE_OVERRIDE" ]; then
  OPENAI_BASE_URL="$OPENAI_BASE_OVERRIDE"
elif [ -n "${OPENAI_BASE_URL:-}" ]; then
  :
elif [ -n "${OPENAI_API_BASE:-}" ]; then
  OPENAI_BASE_URL="$OPENAI_API_BASE"
else
  OPENAI_BASE_URL="$(python3 - "$ANTHROPIC_BASE_URL" "$OPENAI_PORT" "$OPENAI_PATH" <<'PY'
import sys
from urllib.parse import urlparse
u = urlparse(sys.argv[1])
scheme = u.scheme or "http"
host = u.hostname or "localhost"
print(f"{scheme}://{host}:{sys.argv[2]}{sys.argv[3]}")
PY
)"
fi

# Gemini is served by the SAME OpenAI-format endpoint unless told otherwise.
GEMINI_API_BASE="${GEMINI_BASE_OVERRIDE:-${GEMINI_API_BASE:-$OPENAI_BASE_URL}}"

[ -n "$ANTHROPIC_BASE_URL$OPENAI_BASE_URL" ] \
  || die "no anonymizer proxy base URL found (set ANTHROPIC_BASE_URL / OPENAI_BASE_URL in the env or $SETTINGS_JSON .env)."
[ -n "$ANON_KEY" ] \
  || die "no anonymizer proxy key found (set ANTHROPIC_API_KEY / OPENAI_API_KEY in the env or $SETTINGS_JSON .env)."

# ---------------------------------------------------------------------------
# 2) Rewrite each loopback proxy endpoint to the slirp host alias (rootless docker).
#
# Rootless dockerd runs containers in their own network namespace (RootlessKit),
# so a proxy on the VM's 127.0.0.1 is reachable from a container ONLY via slirp's
# host-loopback alias 10.0.2.2 — NOT the docker-bridge gateway 172.17.0.1 (that is
# the in-namespace bridge), and NOT an on-host forwarder (our shell runs in the
# real host netns, not RootlessKit's, so it cannot bind that gateway). So we simply
# rewrite the in-container host to the alias, keeping the SAME port/path. No bridge
# forwarder is started. Override the alias with --host-alias / $SANDBOX_HOST_ALIAS.
# ---------------------------------------------------------------------------
url_field() { python3 - "$1" "$2" <<'PY'
import sys
from urllib.parse import urlparse
u = urlparse(sys.argv[1]); print(getattr(u, sys.argv[2]) or "")
PY
}

: > "$PIDS_FILE"   # kept for --stop compatibility; no forwarders are started now

# in_container_url <base_url>  ->  echoes the URL the swe-agent container should use
in_container_url() {
  local base="$1"
  [ -n "$base" ] || { echo ""; return 0; }
  local scheme host port path
  scheme="$(url_field "$base" scheme)"; host="$(url_field "$base" hostname)"
  port="$(url_field "$base" port)";     path="$(url_field "$base" path)"

  # Not loopback, or rewrite disabled/non-docker -> use as-is.
  case "$host" in
    localhost|127.0.0.1|0.0.0.0) : ;;
    *) echo "$base"; return 0 ;;
  esac
  if [ "$BACKEND" != "docker" ] || [ "$USE_BRIDGE" != "true" ]; then
    echo "$base"; return 0
  fi

  # Rewrite the loopback host to the slirp host alias, keeping port + path.
  local hostport="$HOST_ALIAS"
  [ -n "$port" ] && hostport="$HOST_ALIAS:$port"
  log "rewrote loopback $host${port:+:$port} -> $hostport (rootless host alias)"
  echo "${scheme}://${hostport}${path}"
}

ANTHROPIC_BASE_INCONTAINER="$(in_container_url "$ANTHROPIC_BASE_URL")"
OPENAI_BASE_INCONTAINER="$(in_container_url "$OPENAI_BASE_URL")"
GEMINI_BASE_INCONTAINER="$(in_container_url "$GEMINI_API_BASE")"

# ---------------------------------------------------------------------------
# 3) Preflight probe from inside a throwaway container (docker backend only).
# ---------------------------------------------------------------------------
probe_openai() {  # $1 base, $2 key, $3 model
  docker run --rm -e U="${1%/}/chat/completions" -e K="$2" \
    -e D="{\"model\":\"$3\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_completion_tokens\":16}" \
    alpine:latest sh -c 'apk add --no-cache curl >/dev/null 2>&1; curl -s -m 25 -o /dev/null -w "%{http_code}" -X POST "$U" -H "Content-Type: application/json" -H "Authorization: Bearer $K" --data "$D"' 2>/dev/null
}
probe_anthropic() {  # $1 base, $2 key, $3 model
  docker run --rm -e U="${1%/}/v1/messages" -e K="$2" \
    -e D="{\"model\":\"$3\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}]}" \
    alpine:latest sh -c 'apk add --no-cache curl >/dev/null 2>&1; curl -s -m 25 -o /dev/null -w "%{http_code}" -X POST "$U" -H "Content-Type: application/json" -H "x-api-key: $K" -H "anthropic-version: 2023-06-01" --data "$D"' 2>/dev/null
}

if [ "$PREFLIGHT" = "true" ] && [ "$BACKEND" = "docker" ]; then
  reachable="false"
  if [ -n "$OPENAI_BASE_INCONTAINER" ] && [ -n "$OPENAI_API_KEY" ]; then
    code="$(probe_openai "$OPENAI_BASE_INCONTAINER" "$OPENAI_API_KEY" "$PROBE_OPENAI_MODEL")"
    case "$code" in 000|"") warn "openai preflight got no response at $OPENAI_BASE_INCONTAINER" ;;
                    *) log "openai preflight OK (HTTP $code) via $OPENAI_BASE_INCONTAINER"; reachable="true" ;; esac
  fi
  if [ -n "$ANTHROPIC_BASE_INCONTAINER" ] && [ -n "$ANTHROPIC_API_KEY" ]; then
    code="$(probe_anthropic "$ANTHROPIC_BASE_INCONTAINER" "$ANTHROPIC_API_KEY" "$PROBE_ANTHROPIC_MODEL")"
    case "$code" in 000|"") warn "anthropic preflight got no response at $ANTHROPIC_BASE_INCONTAINER" ;;
                    *) log "anthropic preflight OK (HTTP $code) via $ANTHROPIC_BASE_INCONTAINER"; reachable="true" ;; esac
  fi
  # Gemini is best-effort (endpoint shape depends on the proxy); warn only.
  if [ -n "$GEMINI_BASE_INCONTAINER" ]; then
    code="$(probe_openai "$GEMINI_BASE_INCONTAINER" "$GEMINI_API_KEY" "$PROBE_OPENAI_MODEL")"
    case "$code" in 000|"") warn "gemini proxy endpoint $GEMINI_BASE_INCONTAINER not reachable (openai-style probe); confirm the gemini route with the FDE" ;;
                    *) log "gemini proxy endpoint reachable (HTTP $code) via $GEMINI_BASE_INCONTAINER" ;; esac
  fi
  [ "$reachable" = "true" ] || die "preflight could not reach the proxy from a container. Check the base URL / host alias ($HOST_ALIAS requires rootless host-loopback), or re-run with --no-preflight to bypass."
else
  log "preflight skipped (backend=$BACKEND, preflight=$PREFLIGHT)"
fi

# ---------------------------------------------------------------------------
# 4) Emit artifacts for the SKILL.md flow.
# ---------------------------------------------------------------------------
{
  echo "# sourced by the evaluate_task flow before 'harbor run'. Exports the anonymizer"
  echo "# key(s) so harbor resolves \${VAR} templates, plus the in-container base URLs."
  echo "export ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:+\"$ANTHROPIC_API_KEY\"}"
  echo "export OPENAI_API_KEY=${OPENAI_API_KEY:+\"$OPENAI_API_KEY\"}"
  echo "export GEMINI_API_KEY=${GEMINI_API_KEY:+\"$GEMINI_API_KEY\"}"
  echo "export HILBENCH_ANTHROPIC_BASE_INCONTAINER=\"$ANTHROPIC_BASE_INCONTAINER\""
  echo "export HILBENCH_OPENAI_BASE_INCONTAINER=\"$OPENAI_BASE_INCONTAINER\""
  echo "export HILBENCH_GEMINI_BASE_INCONTAINER=\"$GEMINI_BASE_INCONTAINER\""
} > "$ENV_SH"

# The JobConfig agents[].env block. Keys are ${VAR} templates harbor resolves
# from its own env (source .hilbench_proxy_env.sh first); base URLs are literals
# (the host-alias-rewritten, container-reachable address).
ANTHROPIC_BASE_INCONTAINER="$ANTHROPIC_BASE_INCONTAINER" \
OPENAI_BASE_INCONTAINER="$OPENAI_BASE_INCONTAINER" \
GEMINI_BASE_INCONTAINER="$GEMINI_BASE_INCONTAINER" \
python3 - "$ENV_JSON" <<'PY'
import json, os, sys
env = {}
o = os.environ.get("OPENAI_BASE_INCONTAINER")
if o:
    env["OPENAI_API_KEY"] = "${OPENAI_API_KEY}"
    env["OPENAI_BASE_URL"] = o
    env["OPENAI_API_BASE"] = o
a = os.environ.get("ANTHROPIC_BASE_INCONTAINER")
if a:
    env["ANTHROPIC_API_KEY"] = "${ANTHROPIC_API_KEY}"
    env["ANTHROPIC_BASE_URL"] = a
g = os.environ.get("GEMINI_BASE_INCONTAINER")
if g:
    env["GEMINI_API_KEY"] = "${GEMINI_API_KEY}"
    env["GEMINI_API_BASE"] = g
json.dump(env, open(sys.argv[1], "w"), indent=2)
print(json.dumps(env, indent=2))
PY

echo
log "================ PROXY READY ================"
log "backend=$BACKEND  rewrite=$USE_BRIDGE  host-alias=$HOST_ALIAS"
log "anthropic in-container: ${ANTHROPIC_BASE_INCONTAINER:-<none>}"
log "openai    in-container: ${OPENAI_BASE_INCONTAINER:-<none>}"
log "gemini    in-container: ${GEMINI_BASE_INCONTAINER:-<none>}"
log "env exports  -> $ENV_SH   (source before harbor run)"
log "JobConfig env -> $ENV_JSON (embed as agents[].env)"
log "note          -> no on-host forwarder started (rootless host-alias rewrite)"
log "============================================="
