#!/usr/bin/env bash
# Install/verify repository dependencies inside an already-provisioned container.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

CONTAINER="${HILBENCH_CONTAINER:-hilbench_task}"
REPO="${HILBENCH_REPO:-/app}"

while [ $# -gt 0 ]; do
  case "$1" in
    --container) CONTAINER="$2"; shift 2 ;;
    --repo)      REPO="$2"; shift 2 ;;
    *) hb_die "ensure_repo_dependencies.sh: unknown arg: $1" ;;
  esac
done

hb_container_running "$CONTAINER" || hb_die "CONTAINER_NOT_RUNNING ($CONTAINER)"

set +e
docker exec -w "$REPO" "$CONTAINER" sh -lc '
set -eu
detected=0
installed=0
PYTHON=""
command -v python >/dev/null 2>&1 && PYTHON=python
[ -z "$PYTHON" ] && command -v python3 >/dev/null 2>&1 && PYTHON=python3

run_step() {
  label="$1"
  shift
  echo "DEPENDENCY_STEP $label"
  "$@"
  installed=1
}

# Python
if [ -f uv.lock ]; then
  detected=1
  command -v uv >/dev/null 2>&1 || {
    echo "DEPENDENCY_TOOL_MISSING uv (required by uv.lock)"; exit 20;
  }
  run_step python-uv uv sync --frozen
elif [ -f poetry.lock ]; then
  detected=1
  command -v poetry >/dev/null 2>&1 || {
    echo "DEPENDENCY_TOOL_MISSING poetry (required by poetry.lock)"; exit 20;
  }
  run_step python-poetry poetry install --no-interaction --no-root
elif [ -f requirements.txt ] || [ -f requirements-dev.txt ] || \
     [ -f requirements_test.txt ] || [ -f test-requirements.txt ]; then
  detected=1
  [ -n "$PYTHON" ] || {
    echo "DEPENDENCY_TOOL_MISSING python"; exit 20;
  }
  for req in requirements.txt requirements-dev.txt requirements_test.txt test-requirements.txt; do
    [ -f "$req" ] || continue
    run_step "python-$req" "$PYTHON" -m pip install -r "$req"
  done
elif [ -f pyproject.toml ] || [ -f setup.py ] || [ -f setup.cfg ]; then
  detected=1
  [ -n "$PYTHON" ] || {
    echo "DEPENDENCY_TOOL_MISSING python"; exit 20;
  }
  run_step python-project "$PYTHON" -m pip install -e .
fi

if [ -n "$PYTHON" ] && "$PYTHON" -m pip --version >/dev/null 2>&1; then
  "$PYTHON" -m pip check
fi

# Node.js
if [ -f pnpm-lock.yaml ]; then
  detected=1
  command -v corepack >/dev/null 2>&1 && corepack enable
  command -v pnpm >/dev/null 2>&1 || {
    echo "DEPENDENCY_TOOL_MISSING pnpm (required by pnpm-lock.yaml)"; exit 20;
  }
  run_step node-pnpm pnpm install --frozen-lockfile
elif [ -f yarn.lock ]; then
  detected=1
  command -v corepack >/dev/null 2>&1 && corepack enable
  command -v yarn >/dev/null 2>&1 || {
    echo "DEPENDENCY_TOOL_MISSING yarn (required by yarn.lock)"; exit 20;
  }
  case "$(yarn --version)" in
    1.*) run_step node-yarn yarn install --frozen-lockfile ;;
    *)   run_step node-yarn yarn install --immutable ;;
  esac
elif [ -f package-lock.json ]; then
  detected=1
  command -v npm >/dev/null 2>&1 || {
    echo "DEPENDENCY_TOOL_MISSING npm (required by package-lock.json)"; exit 20;
  }
  run_step node-npm npm ci
elif [ -f package.json ]; then
  detected=1
  command -v npm >/dev/null 2>&1 || {
    echo "DEPENDENCY_TOOL_MISSING npm (required by package.json)"; exit 20;
  }
  run_step node-npm npm install
fi

# Go
if [ -f go.mod ]; then
  detected=1
  command -v go >/dev/null 2>&1 || {
    echo "DEPENDENCY_TOOL_MISSING go (required by go.mod)"; exit 20;
  }
  run_step go-modules go mod download
  go mod verify
fi

# Rust
if [ -f Cargo.toml ]; then
  detected=1
  command -v cargo >/dev/null 2>&1 || {
    echo "DEPENDENCY_TOOL_MISSING cargo (required by Cargo.toml)"; exit 20;
  }
  run_step rust-cargo cargo fetch --locked
  cargo metadata --locked --no-deps --format-version 1 >/dev/null
fi

# Ruby
if [ -f Gemfile ]; then
  detected=1
  command -v bundle >/dev/null 2>&1 || {
    echo "DEPENDENCY_TOOL_MISSING bundler (required by Gemfile)"; exit 20;
  }
  run_step ruby-bundle bundle install
  bundle check
fi

# JVM
if [ -f pom.xml ]; then
  detected=1
  command -v mvn >/dev/null 2>&1 || {
    echo "DEPENDENCY_TOOL_MISSING maven (required by pom.xml)"; exit 20;
  }
  run_step java-maven mvn -q -DskipTests dependency:go-offline
elif [ -f gradlew ]; then
  detected=1
  chmod +x gradlew
  run_step java-gradle ./gradlew --no-daemon dependencies
fi

if [ "$detected" -eq 0 ]; then
  echo "DEPENDENCIES_NOT_APPLICABLE no supported dependency manifest found"
elif [ "$installed" -eq 1 ]; then
  echo "DEPENDENCIES_READY repository dependencies installed and verified"
else
  echo "DEPENDENCIES_READY manifests verified"
fi
'
CODE=$?
set -e

case "$CODE" in
  0) exit 0 ;;
  20)
    hb_die "DEPENDENCY_INSTALL_FAILED: required package manager/runtime is missing. Add it to deliverables/Dockerfile and re-run /hilbench-provision."
    ;;
  *)
    hb_die "DEPENDENCY_INSTALL_FAILED: repository dependency installation/verification failed (exit=$CODE). Fix deliverables/Dockerfile or dependency manifests."
    ;;
esac
