#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

docker compose stop chat-ui >/dev/null 2>&1 || true
docker compose up -d --no-build sourcebot qdrant ast-service

export QDRANT_URL="http://localhost:6333"
export SOURCEBOT_URL="http://localhost:3000"
export AST_SERVICE_URL="http://localhost:8502"
export REPOS_ROOT="${REPOS_ROOT:-$HOME/projects}"

PYTHON="${PYTHON:-python3}"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON="$ROOT_DIR/.venv/bin/python"
fi

if ! "$PYTHON" -c "import streamlit" >/dev/null 2>&1; then
  "$PYTHON" -m pip install -r chat-ui/requirements.txt
fi

exec "$PYTHON" -m streamlit run chat-ui/app.py --server.port 8501
