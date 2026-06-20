# Neo4j Graph Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Neo4j as an optional derived relationship graph written by `ast-service` during offline AST indexing.

**Architecture:** Keep `ast-service` as the single structural indexing boundary. The indexer writes SQLite first, then writes or refreshes Neo4j graph facts from the same normalized ast-grep records and linked SQLite rows. Neo4j runs from the official Docker image and remains rebuildable from SQLite/source indexing.

**Tech Stack:** Python 3.12, FastAPI, SQLite, neo4j Python driver, Docker Compose, pytest, Streamlit later for graph context.

---

## Reference Spec

Implement from:

- `docs/superpowers/specs/2026-06-20-neo4j-graph-index-design.md`

Important constraints:

- Neo4j is a derived graph store, not the source of truth.
- Do not add a custom Neo4j Dockerfile.
- Do not introduce a separate graph-indexer service.
- Do not require Neo4j for the default ast-service pytest suite.
- Do not expose arbitrary Cypher execution to Chat UI or users.
- SQLite and Neo4j are not one distributed transaction.
- Repo graph refresh must delete stale repo graph data before recreating it.
- Neo4j graph refresh must run before `finish_index_run(..., "ok", ...)`.
- API routes must reuse a lifespan-managed Neo4j driver.

## Target File Structure

Create:

```text
ast-service/
├── graph.py
└── tests/
    └── test_graph.py
```

Modify:

```text
docker-compose.yml
.env.example
package.json
README.md
ast-service/requirements.txt
ast-service/indexer.py
ast-service/main.py
ast-service/models.py
ast-service/tests/test_indexer.py
ast-service/tests/test_api.py
scripts/dev-ast-service.sh
scripts/dev-chat-ui.sh
scripts/install.sh
ast-service/graph_cli.py
.gitignore
```

Responsibilities:

- `graph.py`: Neo4j configuration, optional driver creation, constraint initialization, file/repo graph refresh, graph health, graph query helpers.
- `indexer.py`: call graph refresh after SQLite indexing and repo-level call linking.
- `main.py`: expose graph health, sync, impact, and call-path endpoints.
- `models.py`: pydantic models for graph responses.
- `docker-compose.yml`: add official Neo4j service and ast-service graph environment.
- `.env.example`: document Neo4j configuration.
- `README.md`: document graph service, ports, and indexing behavior.
- `scripts/dev-ast-service.sh`: run `ast-service` locally while keeping dependency services in Docker.
- `scripts/dev-chat-ui.sh`: stop both `chat-ui` and `ast-service` containers, then launch local `ast-service` and local Streamlit for full-stack debugging.
- `scripts/install.sh`: update the generated new-user deployment to include Neo4j, graph env vars, image pull, and health checks.
- `graph_cli.py`: small CLI entrypoint for graph sync commands executed through Docker Compose.

## Task 1: Add Neo4j Docker Compose Service

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `scripts/install.sh`

- [ ] **Step 1: Add Neo4j service to Compose**

Add a service next to Qdrant:

```yaml
  neo4j:
    image: ${NEO4J_IMAGE:-neo4j:5-community}
    ports:
      - "7474:7474"
      - "7687:7687"
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs
      - neo4j_plugins:/plugins
    environment:
      - NEO4J_AUTH=neo4j/${NEO4J_PASSWORD:-repo-bot-neo4j}
      - NEO4J_server_memory_heap_initial__size=${NEO4J_HEAP_INITIAL:-512m}
      - NEO4J_server_memory_heap_max__size=${NEO4J_HEAP_MAX:-2G}
      - NEO4J_server_memory_pagecache_size=${NEO4J_PAGECACHE:-1G}
    healthcheck:
      test: ["CMD-SHELL", "cypher-shell -u neo4j -p \"$${NEO4J_PASSWORD:-repo-bot-neo4j}\" 'RETURN 1'"]
      interval: 5s
      timeout: 5s
      retries: 20
      start_period: 20s
    restart: unless-stopped
```

- [ ] **Step 2: Wire ast-service to Neo4j**

Add to `ast-service.environment`:

```yaml
      - NEO4J_ENABLED=${NEO4J_ENABLED:-true}
      - NEO4J_URI=bolt://neo4j:7687
      - NEO4J_USER=${NEO4J_USER:-neo4j}
      - NEO4J_PASSWORD=${NEO4J_PASSWORD:-repo-bot-neo4j}
      - NEO4J_DATABASE=${NEO4J_DATABASE:-neo4j}
```

Add a health-gated `neo4j` dependency to `ast-service.depends_on`:

```yaml
    depends_on:
      qdrant:
        condition: service_started
      sourcebot:
        condition: service_started
      neo4j:
        condition: service_healthy
```

- [ ] **Step 3: Add volumes**

Add:

```yaml
  neo4j_data:
  neo4j_logs:
  neo4j_plugins:
```

- [ ] **Step 4: Document environment variables**

Add to `.env.example`:

```text
# Neo4j relationship graph
NEO4J_ENABLED=true
NEO4J_IMAGE=neo4j:5-community
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=repo-bot-neo4j
NEO4J_DATABASE=neo4j
NEO4J_HEAP_INITIAL=512m
NEO4J_HEAP_MAX=2G
NEO4J_PAGECACHE=1G
```

- [ ] **Step 5: Update install.sh deployment template**

In `scripts/install.sh`:

- Add interactive configuration for `NEO4J_ENABLED`, `NEO4J_PASSWORD`, `NEO4J_HEAP_INITIAL`, `NEO4J_HEAP_MAX`, and `NEO4J_PAGECACHE`.
- Write those variables into the generated `.env`.
- Pull `neo4j:5-community` or `${NEO4J_IMAGE}` in the image pull phase.
- Add the same `neo4j` service and volumes to the embedded Compose template.
- Add Neo4j environment variables to the embedded `ast-service` service.
- Include `neo4j` in service startup/status loops.
- Add a health check that waits for `http://localhost:7474` or runs `cypher-shell RETURN 1`.
- Print Neo4j Browser in the final service URLs.

- [ ] **Step 6: Verify Compose parses**

Run:

```bash
docker compose config
```

Expected: command succeeds and rendered config contains `neo4j`, `neo4j_data`, `neo4j_logs`, and `neo4j_plugins`.

- [ ] **Step 7: Verify install.sh mentions Neo4j**

Run:

```bash
rg -n "neo4j|NEO4J|7474|7687" scripts/install.sh
```

Expected: output includes image pull, generated Compose service, env generation, health check, and final URL sections.

## Task 2: Add Graph Configuration and Disabled Mode

**Files:**
- Modify: `ast-service/requirements.txt`
- Create: `ast-service/graph.py`
- Create: `ast-service/tests/test_graph.py`

- [ ] **Step 1: Add Neo4j driver dependency**

Add to `ast-service/requirements.txt`:

```text
neo4j>=5.20,<6
```

- [ ] **Step 2: Write config tests**

Create `ast-service/tests/test_graph.py`:

```python
import os

from graph import GraphConfig


def test_graph_config_disabled_when_env_false(monkeypatch):
    monkeypatch.setenv("NEO4J_ENABLED", "false")

    config = GraphConfig.from_env()

    assert config.enabled is False


def test_graph_config_reads_connection_env(monkeypatch):
    monkeypatch.setenv("NEO4J_ENABLED", "true")
    monkeypatch.setenv("NEO4J_URI", "bolt://example:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")

    config = GraphConfig.from_env()

    assert config.enabled is True
    assert config.uri == "bolt://example:7687"
    assert config.user == "neo4j"
    assert config.password == "secret"
    assert config.database == "neo4j"
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
cd ast-service && python -m pytest tests/test_graph.py -v
```

Expected: FAIL because `graph.py` does not exist yet.

- [ ] **Step 4: Implement config**

Create `ast-service/graph.py`:

```python
import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class GraphConfig:
    enabled: bool
    uri: str
    user: str
    password: str
    database: str

    @classmethod
    def from_env(cls) -> "GraphConfig":
        return cls(
            enabled=_env_bool("NEO4J_ENABLED", default=False),
            uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            user=os.environ.get("NEO4J_USER", "neo4j"),
            password=os.environ.get("NEO4J_PASSWORD", "repo-bot-neo4j"),
            database=os.environ.get("NEO4J_DATABASE", "neo4j"),
        )
```

- [ ] **Step 5: Verify config tests pass**

Run:

```bash
cd ast-service && python -m pytest tests/test_graph.py -v
```

Expected: PASS.

## Task 3: Add Neo4j Driver Wrapper and Constraints

**Files:**
- Modify: `ast-service/graph.py`
- Modify: `ast-service/tests/test_graph.py`

- [ ] **Step 1: Add fake session tests for constraint statements**

Extend `test_graph.py` with a fake driver/session that records Cypher. Test that `ensure_constraints()` emits constraints for `Repository`, `File`, `Symbol`, `ExternalSymbol`, and `Module`.

Use this fake as the starting point:

```python
class FakeSession:
    def __init__(self):
        self.runs = []
        self.write_calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, statement, parameters=None, **kwargs):
        self.runs.append((statement, parameters or kwargs))
        return []

    def execute_write(self, fn, *args, **kwargs):
        self.write_calls.append((fn, args, kwargs))
        tx = FakeTransaction(self)
        return fn(tx, *args, **kwargs)


class FakeTransaction:
    def __init__(self, session):
        self.session = session

    def run(self, statement, parameters=None, **kwargs):
        self.session.runs.append((statement, parameters or kwargs))
        return []


class FakeDriver:
    def __init__(self):
        self.session_obj = FakeSession()
        self.closed = False

    def session(self, database=None):
        self.database = database
        return self.session_obj

    def close(self):
        self.closed = True
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
cd ast-service && python -m pytest tests/test_graph.py -v
```

Expected: FAIL because `ensure_constraints()` is missing.

- [ ] **Step 3: Implement driver wrapper**

Add to `graph.py`:

```python
import time

from neo4j import GraphDatabase


CONSTRAINTS = [
    "CREATE CONSTRAINT repo_name IF NOT EXISTS FOR (r:Repository) REQUIRE r.name IS UNIQUE",
    "CREATE CONSTRAINT file_key IF NOT EXISTS FOR (f:File) REQUIRE (f.repo, f.path) IS UNIQUE",
    "CREATE CONSTRAINT symbol_key IF NOT EXISTS FOR (s:Symbol) REQUIRE (s.repo, s.symbol_id) IS UNIQUE",
    "CREATE CONSTRAINT external_symbol_key IF NOT EXISTS FOR (s:ExternalSymbol) REQUIRE (s.repo, s.name) IS UNIQUE",
    "CREATE CONSTRAINT module_key IF NOT EXISTS FOR (m:Module) REQUIRE (m.repo, m.module_path) IS UNIQUE",
]


def create_driver(config: GraphConfig):
    if not config.enabled:
        return None
    return GraphDatabase.driver(config.uri, auth=(config.user, config.password))


def verify_connectivity(driver, retries: int = 5, delay_seconds: float = 1.0) -> None:
    if driver is None:
        return
    last_error = None
    for _ in range(retries):
        try:
            driver.verify_connectivity()
            return
        except Exception as exc:
            last_error = exc
            time.sleep(delay_seconds)
    if last_error is None:
        raise RuntimeError("verify_connectivity: no retries attempted")
    raise last_error


def ensure_constraints(driver, database: str) -> None:
    if driver is None:
        return
    with driver.session(database=database) as session:
        for statement in CONSTRAINTS:
            session.run(statement)
```

- [ ] **Step 4: Verify tests pass**

Run:

```bash
cd ast-service && python -m pytest tests/test_graph.py -v
```

Expected: PASS.

## Task 4: Implement Repo Graph Refresh from SQLite

**Files:**
- Modify: `ast-service/graph.py`
- Modify: `ast-service/tests/test_graph.py`

- [ ] **Step 1: Add test fixture for SQLite graph facts**

Use an in-memory SQLite database initialized by `db.init_db()`. Insert one repo, one file, two symbols, one linked call, one unresolved call, and one import.

- [ ] **Step 2: Test Cypher parameter batches**

Test that `refresh_repo_graph(conn, driver, database, repo)`:

- Runs a repo-scoped delete before recreating graph rows.
- Uses `session.execute_write()` or an explicit transaction wrapper for the whole repo refresh.
- Merges repository and file nodes.
- Merges symbol nodes using `symbol_id`.
- Creates `DEFINES`.
- Creates `CALLS` to a target `Symbol` when `callee_symbol_id` is present.
- Creates `CALLS` to `ExternalSymbol` when `callee_symbol_id` is null.
- Creates `IMPORTS` to `Module`.
- Does not create `ENCLOSED_BY` in the first release.

- [ ] **Step 3: Run test to verify failure**

Run:

```bash
cd ast-service && python -m pytest tests/test_graph.py::test_refresh_repo_graph_writes_expected_batches -v
```

Expected: FAIL because `refresh_repo_graph()` is missing.

- [ ] **Step 4: Implement `refresh_repo_graph()`**

Implement batch reads from SQLite with joins against `files` and `symbols`, filtering `files.deleted_at IS NULL`.

Start the repo write transaction by deleting stale graph data:

```cypher
MATCH (n)
WHERE n.repo = $repo
DETACH DELETE n
```

```cypher
MATCH (r:Repository {name: $repo})
DETACH DELETE r
```

Then merge the repository once:

```cypher
MERGE (:Repository {name: $repo})
```

Use Cypher with `UNWIND $rows AS row` for each batch:

```cypher
MATCH (r:Repository {name: row.repo})
MERGE (f:File {repo: row.repo, path: row.path})
SET f.language = row.language, f.content_hash = row.content_hash
MERGE (r)-[:CONTAINS]->(f)
```

```cypher
MATCH (f:File {repo: row.repo, path: row.path})
MERGE (s:Symbol {repo: row.repo, symbol_id: row.symbol_id})
SET s.path = row.path,
    s.name = row.name,
    s.qualified_name = row.qualified_name,
    s.kind = row.kind,
    s.start_line = row.start_line,
    s.end_line = row.end_line
MERGE (f)-[:DEFINES]->(s)
```

Use separate batches for files, symbols, linked calls, unresolved calls, and imports. Use a default `batch_size=1000`.

Use this import batch statement:

```cypher
UNWIND $rows AS row
MATCH (f:File {repo: row.repo, path: row.path})
MERGE (m:Module {repo: row.repo, module_path: row.module_path})
MERGE (f)-[rel:IMPORTS {
  line: row.import_line,
  imported_names_json: row.imported_names_json
}]->(m)
```

- [ ] **Step 5: Verify graph tests pass**

Run:

```bash
cd ast-service && python -m pytest tests/test_graph.py -v
```

Expected: PASS.

## Task 5: Wire Graph Refresh into Indexer

**Files:**
- Modify: `ast-service/indexer.py`
- Modify: `ast-service/tests/test_indexer.py`

- [ ] **Step 1: Add disabled-mode indexer test**

Add or update a test so `run_index()` succeeds with `NEO4J_ENABLED=false` and does not require a Neo4j driver.

- [ ] **Step 2: Add enabled-mode mock test**

Patch `indexer.create_driver`, `indexer.ensure_constraints`, and `indexer.refresh_repo_graph` or equivalent imports. Verify that after `link_callee_symbols(conn, indexed_repo)`, the indexer calls `refresh_repo_graph()` once per changed or deleted repo.

Also add a test where `refresh_repo_graph()` raises. Assert the latest `index_runs.status` is `error`, not `ok`.

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
cd ast-service && python -m pytest tests/test_indexer.py -v
```

Expected: FAIL until `indexer.py` is wired to graph helpers.

- [ ] **Step 4: Implement indexer wiring**

In `run_index()`:

- Load `GraphConfig.from_env()`.
- Create driver once at the start when enabled.
- Verify driver connectivity with bounded retries before graph-enabled indexing proceeds.
- Ensure constraints once.
- Track `repos_needing_graph_refresh`: repos with at least one indexed file, plus repos where `mark_deleted_files()` marked any file deleted.
- After `link_callee_symbols(conn, indexed_repo)` and deletion handling, call `refresh_repo_graph(conn, driver, config.database, indexed_repo)` only for repos in `repos_needing_graph_refresh`.
- Call graph refresh before `finish_index_run(conn, run_id, "ok", ...)` so Neo4j failures are caught by the existing `except` block and recorded as `error`.
- Close driver in `finally`.

Keep SQLite transaction boundaries unchanged.

- [ ] **Step 5: Verify indexer tests pass**

Run:

```bash
cd ast-service && python -m pytest tests/test_indexer.py -v
```

Expected: PASS.

## Task 6: Add Graph API Models and Routes

**Files:**
- Modify: `ast-service/models.py`
- Modify: `ast-service/main.py`
- Modify: `ast-service/tests/test_api.py`
- Modify: `ast-service/graph.py`

- [ ] **Step 1: Add API tests**

Add tests for:

- `GET /graph/health` when disabled returns `{enabled: false}`.
- `POST /graph/sync` calls `refresh_repo_graph()`.
- `GET /graph/impact` returns bounded graph facts.
- `GET /graph/call-paths` returns bounded paths.

Use monkeypatches/fakes; default tests should not connect to a real Neo4j.

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd ast-service && python -m pytest tests/test_api.py -v
```

Expected: FAIL because routes and models are missing.

- [ ] **Step 3: Add models**

Add to `models.py`:

```python
class GraphHealthResponse(BaseModel):
    enabled: bool
    status: str


class GraphSyncResponse(BaseModel):
    status: str
    repos_synced: list[str]


class GraphImpactResponse(BaseModel):
    facts: list[dict[str, Any]]


class GraphCallPathsResponse(BaseModel):
    paths: list[list[dict[str, Any]]]
```

- [ ] **Step 4: Implement route helpers**

In `graph.py`, add:

- `graph_health(config)`.
- `sync_graph_from_sqlite(conn, driver, database, repo=None)`.
- `query_impact(driver, database, repo, symbol, depth, limit)`.
- `query_call_paths(driver, database, repo, from_symbol, to_symbol, max_depth, limit)`.

Use the lifespan-managed driver from `app.state`; do not create a new driver per API request.

- [ ] **Step 5: Add FastAPI routes**

In `main.py`, update the existing FastAPI lifespan so it:

- Initializes SQLite as it does today.
- Loads `GraphConfig.from_env()`.
- Creates one Neo4j driver when enabled.
- Verifies connectivity with retry/backoff, for example 5 attempts with short sleeps.
- Stores `graph_config` and `neo4j_driver` on `app.state`.
- Closes the driver during shutdown.

Then expose:

```text
GET  /graph/health
POST /graph/sync
GET  /graph/impact
GET  /graph/call-paths
```

Bound query parameters:

- `depth`: 1 to 4, default 2.
- `max_depth`: 1 to 6, default 4.
- `limit`: 1 to 200, default 50.

- [ ] **Step 6: Verify API tests pass**

Run:

```bash
cd ast-service && python -m pytest tests/test_api.py -v
```

Expected: PASS.

## Task 7: Add npm Scripts and Documentation

**Files:**
- Modify: `package.json`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Create: `scripts/dev-ast-service.sh`
- Modify: `scripts/dev-chat-ui.sh`
- Modify: `.gitignore`
- Create: `ast-service/graph_cli.py`

- [ ] **Step 1: Add local ast-service dev script**

Create `scripts/dev-ast-service.sh`:

```bash
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
```

- [ ] **Step 2: Update full-stack dev script**

Modify `scripts/dev-chat-ui.sh` so `npm run dev` stops both local-development containers and runs `ast-service` locally in the background:

```bash
docker compose stop chat-ui ast-service >/dev/null 2>&1 || true
docker compose up -d --no-build sourcebot qdrant neo4j
```

Before launching Streamlit, start local ast-service:

```bash
export REPOS_ROOT="${REPOS_ROOT:-$HOME/projects}"
export AST_DB_PATH="${AST_DB_PATH:-$ROOT_DIR/.data/ast.sqlite}"
export NEO4J_URI="${NEO4J_URI:-bolt://localhost:7687}"
export NEO4J_ENABLED="${NEO4J_ENABLED:-true}"
mkdir -p "$(dirname "$AST_DB_PATH")"

(
  cd "$ROOT_DIR/ast-service"
  "$PYTHON" -m uvicorn main:app --host 0.0.0.0 --port 8502 --reload
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
```

Then keep:

```bash
export AST_SERVICE_URL="http://localhost:8502"
"$PYTHON" -m streamlit run chat-ui/app.py --server.port 8501
```

Do not use `exec` for Streamlit here. Replacing the shell with Streamlit would trigger the shell's `EXIT` trap first, which would kill the background uvicorn process before Streamlit starts.

- [ ] **Step 3: Ignore local dev data**

Add to `.gitignore`:

```text
.data/
```

- [ ] **Step 4: Add graph CLI**

Create `ast-service/graph_cli.py`:

```python
import argparse

from db import connect_db
from graph import GraphConfig, create_driver, ensure_constraints, sync_graph_from_sqlite


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["sync"])
    parser.add_argument("--repo")
    args = parser.parse_args()

    config = GraphConfig.from_env()
    if not config.enabled:
        raise SystemExit("Neo4j is disabled")

    conn = connect_db()
    driver = create_driver(config)
    try:
        ensure_constraints(driver, config.database)
        if args.command == "sync":
            sync_graph_from_sqlite(conn, driver, config.database, repo=args.repo)
    finally:
        if driver is not None:
            driver.close()
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Add npm scripts**

Add:

```json
"dev:ast": "bash scripts/dev-ast-service.sh",
"open:neo4j": "open http://localhost:7474",
"graph:sync": "docker compose exec -T ast-service python /app/graph_cli.py sync"
```

- [ ] **Step 6: Update docs**

Update service lists and command lists to include Neo4j:

- Browser: `http://localhost:7474`
- Bolt: `bolt://localhost:7687`
- Default user: `neo4j`
- Default password: `repo-bot-neo4j`

Document local development commands:

- `npm run dev`: stop `chat-ui` and `ast-service` containers, start dependency containers, then run local `ast-service` and local Chat UI.
- `npm run dev:ast`: stop `ast-service` container, start dependency containers, then run local `ast-service` only.

- [ ] **Step 7: Verify package JSON**

Run:

```bash
node -e "JSON.parse(require('fs').readFileSync('package.json', 'utf8')); console.log('ok')"
```

Expected: prints `ok`.

- [ ] **Step 8: Verify local ast-service dev command**

Run:

```bash
npm run dev:ast
```

Expected: `uvicorn` starts on `http://localhost:8502`; editing `ast-service/*.py` triggers reload.

In another terminal:

```bash
curl -fsSL http://localhost:8502/health
```

Expected: health JSON is returned.

## Task 8: Full Verification

**Files:**
- No new files unless fixes are required.

- [ ] **Step 1: Run default ast-service tests without Neo4j**

Run:

```bash
cd ast-service && NEO4J_ENABLED=false python -m pytest -v
```

Expected: all tests pass; no real Neo4j connection is attempted.

- [ ] **Step 2: Render Compose config**

Run:

```bash
docker compose config
```

Expected: Compose config is valid.

- [ ] **Step 3: Validate install script Neo4j coverage**

Run:

```bash
rg -n "neo4j|NEO4J|7474|7687" scripts/install.sh
```

Expected: install script contains Neo4j pull, generated Compose service, env generation, health checks, and final URLs.

- [ ] **Step 4: Start Neo4j and ast-service**

Run:

```bash
docker compose up -d neo4j ast-service
```

Expected: both containers start.

- [ ] **Step 5: Check graph health**

Run:

```bash
curl -fsSL http://localhost:8502/graph/health
```

Expected: JSON response with `enabled: true` and `status: ok`.

- [ ] **Step 6: Run AST index**

Run:

```bash
npm run index:ast
```

Expected: indexing completes, graph refresh runs after SQLite linking and before `index_runs` is marked `ok`.

- [ ] **Step 7: Query Neo4j count**

Open `http://localhost:7474`, login with `neo4j` / `repo-bot-neo4j`, and run:

```cypher
MATCH (n) RETURN labels(n), count(*) LIMIT 20;
```

Expected: repository, file, symbol, module, or external symbol nodes exist after indexing.

- [ ] **Step 8: Run graph API query**

Run:

```bash
curl -fsSL "http://localhost:8502/graph/impact?repo=<repo>&symbol=<symbol>&depth=2&limit=20"
```

Expected: bounded JSON facts for matching graph relationships, or an empty list if the symbol is absent.

## Implementation Notes

- Keep graph writes idempotent. Re-running indexing should not duplicate nodes or edges.
- Clear the repo graph before recreating it so deleted files and removed symbols do not leave stale nodes.
- Wrap each repo refresh in one Neo4j write transaction.
- Prefer `UNWIND $rows AS row` batches over one Cypher statement per entity.
- Limit batches to 1000 rows by default.
- Keep Cypher statements in `graph.py` constants to make tests and review straightforward.
- Use `files.deleted_at IS NULL` in all SQLite-to-graph sync queries.
- Add `source: "ast-grep"` and conservative `confidence` values to call edges. Suggested values:
  - `1.0` for `callee_symbol_id` linked calls.
  - `0.5` for unresolved calls to `ExternalSymbol`.
- Close Neo4j drivers explicitly in indexer and API recovery paths.

## Handoff

After this plan is implemented, the first user-visible result should be:

- `docker compose up -d` starts Neo4j.
- `npm run index:ast` writes SQLite and Neo4j.
- `GET /graph/health` reports graph status.
- `GET /graph/impact` and `/graph/call-paths` can return relationship context for future Chat UI GraphRAG integration.
