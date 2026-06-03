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
#   PYTHON_BIN           - python interpreter on the remote (default: python3).
#                          On Raspberry Pi OS Bullseye set this to e.g.
#                          python3.11 after installing a >=3.10 interpreter.
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

command -v sshpass >/dev/null 2>&1 || {
  echo "ERROR: sshpass is not installed. Install it (apt-get install sshpass)." >&2
  exit 1
}

SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p "${DEPLOY_SSH_PORT}")

echo ">> Deploying ${REPO_URL}@${GIT_REF} to ${DEPLOY_SSH_USER}@${DEPLOY_SSH_HOST}:${DEPLOY_SSH_PORT} (port ${APP_PORT})"

# The remote-side script. APP_* values are interpolated locally and passed in.
sshpass -p "${DEPLOY_SSH_PASSWORD}" ssh "${SSH_OPTS[@]}" \
  "${DEPLOY_SSH_USER}@${DEPLOY_SSH_HOST}" \
  "APP_PORT='${APP_PORT}' APP_DIR='${APP_DIR}' REPO_URL='${REPO_URL}' GIT_REF='${GIT_REF}' PYTHON_BIN='${PYTHON_BIN}' bash -s" <<'REMOTE'
set -euo pipefail

echo "   [remote] host: $(hostname)"

# --- verify interpreter is >= 3.10 -----------------------------------------
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "   [remote] ERROR: '${PYTHON_BIN}' not found on PATH." >&2
  echo "   [remote] Install Python >=3.10 and pass PYTHON_BIN (e.g. python3.11)." >&2
  exit 1
fi
if ! "${PYTHON_BIN}" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)'; then
  echo "   [remote] ERROR: ${PYTHON_BIN} is $(${PYTHON_BIN} -V 2>&1); need >=3.10." >&2
  echo "   [remote] On Raspberry Pi OS Bullseye, install python3.11 and set PYTHON_BIN=python3.11." >&2
  exit 1
fi
echo "   [remote] using $(${PYTHON_BIN} -V 2>&1)"

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

# --- install ----------------------------------------------------------------
echo "   [remote] installing dependencies"
"${PYTHON_BIN}" -m venv .venv
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
