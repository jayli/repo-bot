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

docker compose stop ast-service >/dev/null 2>&1 || true
docker compose up -d --no-build sourcebot qdrant neo4j

export REPOS_ROOT="${REPOS_ROOT:-$HOME/projects}"
export AST_DB_PATH="${AST_DB_PATH:-$ROOT_DIR/.data/ast.sqlite}"
export NEO4J_URI="${NEO4J_URI:-bolt://localhost:7687}"
export NEO4J_USER="${NEO4J_USER:-neo4j}"
export NEO4J_PASSWORD="${NEO4J_PASSWORD:-repo-bot-neo4j}"
export NEO4J_DATABASE="${NEO4J_DATABASE:-neo4j}"
export NEO4J_ENABLED="${NEO4J_ENABLED:-true}"

mkdir -p "$(dirname "$AST_DB_PATH")"

PYTHON="${PYTHON:-python3}"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON="$ROOT_DIR/.venv/bin/python"
fi

if ! "$PYTHON" -c "import fastapi, uvicorn, neo4j" >/dev/null 2>&1; then
  "$PYTHON" -m pip install -r ast-service/requirements.txt
fi

cd ast-service
exec "$PYTHON" -m uvicorn main:app --host 0.0.0.0 --port 8502 --reload
