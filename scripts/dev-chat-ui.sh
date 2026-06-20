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

docker compose stop chat-ui ast-service >/dev/null 2>&1 || true
docker compose up -d --no-build sourcebot qdrant neo4j

export QDRANT_URL="http://localhost:6333"
export SOURCEBOT_URL="http://localhost:3000"
if [ "${SOURCEBOT_ORG_DOMAIN:-}" = "$HOME" ]; then
  export SOURCEBOT_ORG_DOMAIN="~"
fi
export SOURCEBOT_ORG_DOMAIN="${SOURCEBOT_ORG_DOMAIN:-~}"
export REPOS_ROOT="${REPOS_ROOT:-$HOME/projects}"

PYTHON="${PYTHON:-python3}"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON="$ROOT_DIR/.venv/bin/python"
fi

if ! "$PYTHON" -c "import fastapi, uvicorn, neo4j" >/dev/null 2>&1; then
  "$PYTHON" -m pip install -r ast-service/requirements.txt
fi

if ! "$PYTHON" -c "import streamlit" >/dev/null 2>&1; then
  "$PYTHON" -m pip install -r chat-ui/requirements.txt
fi

# Start local ast-service in background
export AST_DB_PATH="${AST_DB_PATH:-$ROOT_DIR/.data/ast.sqlite}"

# Sync SQLite data from Docker volume if local is missing or empty
if [ ! -s "$AST_DB_PATH" ]; then
  mkdir -p "$(dirname "$AST_DB_PATH")"
  CONTAINER=$(docker ps -a --filter name=repo-bot-ast-service --format '{{.Names}}' | head -1)
  if [ -n "$CONTAINER" ]; then
    docker cp "$CONTAINER:/data/ast.sqlite" "$AST_DB_PATH" 2>/dev/null || true
    echo "Copied ast.sqlite from container ($(wc -c < "$AST_DB_PATH" 2>/dev/null || echo 0) bytes)"
  fi
fi

export NEO4J_URI="${NEO4J_URI:-bolt://localhost:7687}"
export NEO4J_ENABLED="${NEO4J_ENABLED:-true}"
mkdir -p "$(dirname "$AST_DB_PATH")"

AST_PID=""
cleanup() {
  if [ -n "$AST_PID" ]; then
    kill "$AST_PID" >/dev/null 2>&1 || true
    wait "$AST_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

(
  cd "$ROOT_DIR/ast-service"
  "$PYTHON" -m uvicorn main:app --host 0.0.0.0 --port 8502 --reload
) &
AST_PID=$!

export AST_SERVICE_URL="http://localhost:8502"

# Do not use exec for Streamlit here. Replacing the shell would trigger
# the shell's EXIT trap first, which would kill the background uvicorn
# process before Streamlit starts.
"$PYTHON" -m streamlit run chat-ui/app.py --server.port 8501
