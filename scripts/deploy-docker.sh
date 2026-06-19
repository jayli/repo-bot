#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

test -f .env || cp .env.example .env
test -f config/sourcebot.json || cp config/sourcebot.json.example config/sourcebot.json

docker compose up -d --build
docker compose ps
