#!/usr/bin/env bash
#
# Deploy the Yahoo Finance MCP server to a remote host over SSH and run it on
# port 8081 (HTTP transport). Designed to be invoked from CI (GitHub Actions)
# with credentials supplied as environment variables / secrets, but it also
# works when run by hand.
#
# Required environment variables (provided as GitHub secrets in CI):
#   DEPLOY_SSH_HOST      - remote endpoint (hostname or IP)
#   DEPLOY_SSH_PORT      - SSH port (e.g. 22)
#   DEPLOY_SSH_USER      - SSH username
#   DEPLOY_SSH_PASSWORD  - SSH password
#
# Optional:
#   APP_PORT             - port to serve on (default: 8081)
#   APP_DIR              - remote checkout directory (default: ~/yahoo-finance-mcp-server)
#   REPO_URL             - git URL to clone/pull (default: this repo)
#   GIT_REF              - branch/tag/sha to deploy (default: main)
#   PYTHON_BIN           - preferred python interpreter on the remote.
#                          If unset/too old, the script auto-discovers any
#                          installed Python >=3.10, and if none exists it
#                          builds one from source (one-time, see below).
#   PYTHON_BUILD_VERSION - CPython version to build when none is found
#                          (default: 3.11.9). Used on e.g. Raspberry Pi OS
#                          Bullseye, which ships only Python 3.9.
#
# Auto-provisioning: when no Python >=3.10 is present the script installs build
# dependencies (apt) and compiles CPython to /usr/local via `make altinstall`.
# This needs root: it works if the deploy user has passwordless sudo, otherwise
# it uses DEPLOY_SSH_PASSWORD for `sudo -S`. The build runs ONCE; later deploys
# discover the installed interpreter and skip it.
#
# Usage:  ./deploy/deploy.sh

set -euo pipefail

: "${DEPLOY_SSH_HOST:?DEPLOY_SSH_HOST is required}"
: "${DEPLOY_SSH_PORT:?DEPLOY_SSH_PORT is required}"
: "${DEPLOY_SSH_USER:?DEPLOY_SSH_USER is required}"
: "${DEPLOY_SSH_PASSWORD:?DEPLOY_SSH_PASSWORD is required}"

APP_PORT="${APP_PORT:-8081}"
APP_DIR="${APP_DIR:-yahoo-finance-mcp-server}"
REPO_URL="${REPO_URL:-https://github.com/christianGRogers/yahoo-finance-mcp-server.git}"
GIT_REF="${GIT_REF:-main}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PYTHON_BUILD_VERSION="${PYTHON_BUILD_VERSION:-3.11.9}"

command -v sshpass >/dev/null 2>&1 || {
  echo "ERROR: sshpass is not installed. Install it (apt-get install sshpass)." >&2
  exit 1
}

# Keepalives matter: a from-source Python build can hold the connection open
# for many minutes with no traffic.
SSH_OPTS=(
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=120
  -p "${DEPLOY_SSH_PORT}"
)

echo ">> Deploying ${REPO_URL}@${GIT_REF} to ${DEPLOY_SSH_USER}@${DEPLOY_SSH_HOST}:${DEPLOY_SSH_PORT} (port ${APP_PORT})"

# The remote-side script. APP_* values are interpolated locally and passed in.
sshpass -p "${DEPLOY_SSH_PASSWORD}" ssh "${SSH_OPTS[@]}" \
  "${DEPLOY_SSH_USER}@${DEPLOY_SSH_HOST}" \
  "APP_PORT='${APP_PORT}' APP_DIR='${APP_DIR}' REPO_URL='${REPO_URL}' GIT_REF='${GIT_REF}' PYTHON_BIN='${PYTHON_BIN}' PYTHON_BUILD_VERSION='${PYTHON_BUILD_VERSION}' SUDO_PASS='${DEPLOY_SSH_PASSWORD}' bash -s" <<'REMOTE'
set -euo pipefail

echo "   [remote] host: $(hostname)"

# --- ensure a Python >= 3.10 interpreter (auto-install if needed) ----------
py_ok() {  # $1 = interpreter name/path; ok if present and >=3.10
  command -v "$1" >/dev/null 2>&1 && \
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null
}

find_python() {  # print path to a usable interpreter, or return 1
  local c
  for c in "${PYTHON_BIN}" python3.13 python3.12 python3.11 python3.10 \
           /usr/local/bin/python3.13 /usr/local/bin/python3.12 \
           /usr/local/bin/python3.11 /usr/local/bin/python3.10; do
    if py_ok "$c"; then command -v "$c"; return 0; fi
  done
  return 1
}

run_sudo() {  # run as root: direct if already root, else sudo (password if needed)
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif sudo -n true 2>/dev/null; then
    sudo "$@"
  elif [ -n "${SUDO_PASS:-}" ]; then
    printf '%s\n' "${SUDO_PASS}" | sudo -S -p '' "$@"
  else
    echo "   [remote] ERROR: need root to install Python but no sudo access." >&2
    return 1
  fi
}

build_python() {
  local mm src
  mm="$(printf '%s' "${PYTHON_BUILD_VERSION}" | cut -d. -f1-2)"
  echo "   [remote] no Python >=3.10 found; building CPython ${PYTHON_BUILD_VERSION} from source"
  echo "   [remote] (one-time bootstrap; may take 15-40 min on a Raspberry Pi)"
  run_sudo apt-get update -y
  run_sudo apt-get install -y --no-install-recommends \
    build-essential wget ca-certificates tk-dev libssl-dev libffi-dev \
    zlib1g-dev libbz2-dev libsqlite3-dev libreadline-dev libncurses5-dev \
    libgdbm-dev liblzma-dev uuid-dev
  src="/tmp/Python-${PYTHON_BUILD_VERSION}"
  rm -rf "${src}" "${src}.tgz"
  wget -q -O "${src}.tgz" \
    "https://www.python.org/ftp/python/${PYTHON_BUILD_VERSION}/Python-${PYTHON_BUILD_VERSION}.tgz"
  tar -xf "${src}.tgz" -C /tmp
  (
    cd "${src}"
    ./configure --prefix=/usr/local --with-ensurepip=install >/dev/null
    make -j"$(nproc)" >/dev/null
    run_sudo make altinstall >/dev/null
  )
  rm -rf "${src}" "${src}.tgz"
  PYTHON_BIN="/usr/local/bin/python${mm}"
}

if RESOLVED="$(find_python)"; then
  PYTHON_BIN="${RESOLVED}"
else
  build_python
  if ! py_ok "${PYTHON_BIN}"; then
    echo "   [remote] ERROR: Python build finished but ${PYTHON_BIN} is unusable." >&2
    exit 1
  fi
fi
echo "   [remote] using ${PYTHON_BIN} ($(${PYTHON_BIN} -V 2>&1))"

# --- fetch / update source -------------------------------------------------
if [ -d "${APP_DIR}/.git" ]; then
  echo "   [remote] updating existing checkout in ${APP_DIR}"
  git -C "${APP_DIR}" fetch --all --prune
  git -C "${APP_DIR}" reset --hard "origin/${GIT_REF}"
else
  echo "   [remote] cloning ${REPO_URL} into ${APP_DIR}"
  git clone "${REPO_URL}" "${APP_DIR}"
  git -C "${APP_DIR}" checkout "${GIT_REF}"
fi
cd "${APP_DIR}"

# --- system runtime libraries ----------------------------------------------
# numpy/pandas wheels need OpenBLAS (libopenblas.so.0) + libgfortran at
# runtime; these are not Python packages. On a fresh Raspberry Pi they're
# often missing, which surfaces as "libopenblas.so.0: cannot open shared
# object file" when numpy imports. Install them if absent.
if ! ldconfig -p 2>/dev/null | grep -q 'libopenblas\.so\.0'; then
  echo "   [remote] installing OpenBLAS runtime (required by numpy)"
  run_sudo apt-get update -y
  # Package name varies across Raspberry Pi OS / Debian releases.
  run_sudo apt-get install -y libopenblas0 \
    || run_sudo apt-get install -y libopenblas0-pthread \
    || run_sudo apt-get install -y libopenblas-base \
    || run_sudo apt-get install -y libopenblas-dev
  run_sudo apt-get install -y libgfortran5 || true
fi

# --- install ----------------------------------------------------------------
# Recreate the venv if it is missing or was built with an old interpreter
# (e.g. a 3.9 venv left by an earlier deploy). `venv` reuses an existing
# directory without swapping the python symlinks, so we must clear it.
echo "   [remote] preparing virtualenv"
if [ -x .venv/bin/python ] && \
   .venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null; then
  echo "   [remote] reusing existing venv ($(.venv/bin/python -V 2>&1))"
else
  echo "   [remote] (re)creating venv with ${PYTHON_BIN}"
  rm -rf .venv
  "${PYTHON_BIN}" -m venv .venv
fi

echo "   [remote] installing dependencies"
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -e .

# --- stop any previous instance on this port --------------------------------
echo "   [remote] stopping previous instance (if any)"
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${APP_PORT}/tcp" 2>/dev/null || true
fi
pkill -f "yahoo_finance_mcp" 2>/dev/null || true
sleep 1

# --- start (detached so it survives SSH disconnect) -------------------------
echo "   [remote] starting server on 0.0.0.0:${APP_PORT}"
setsid env \
  MCP_TRANSPORT=streamable-http \
  MCP_HOST=0.0.0.0 \
  MCP_PORT="${APP_PORT}" \
  ./.venv/bin/python -m yahoo_finance_mcp \
  < /dev/null > "$(pwd)/server.log" 2>&1 &

# --- health check -----------------------------------------------------------
sleep 4
if command -v curl >/dev/null 2>&1; then
  code=$(curl -s -o /dev/null -w '%{http_code}' \
    -X POST "http://127.0.0.1:${APP_PORT}/mcp" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"deploy-healthcheck","version":"0"}}}' || true)
  echo "   [remote] health check HTTP status: ${code}"
  if [ "${code}" != "200" ]; then
    echo "   [remote] WARNING: unexpected status; last log lines:" >&2
    tail -n 20 server.log >&2 || true
    exit 1
  fi
fi

echo "   [remote] deploy complete; server listening on :${APP_PORT}"
REMOTE

echo ">> Done."
