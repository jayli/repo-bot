# Neo4j Graph Index Design

## Summary

Add Neo4j as repo-bot's derived relationship graph store. The existing `ast-service` remains the structural indexing boundary: it scans source files with ast-grep, normalizes matches into symbols, calls, imports, and SCIP-compatible records, writes SQLite, links repository-level call relationships, and then refreshes Neo4j during the same offline indexing run.

Neo4j is not a replacement for SQLite. SQLite remains the local fact store, debug surface, and recovery source. Neo4j adds multi-hop relationship traversal for code impact analysis, call path explanation, module dependency exploration, and future GraphRAG context expansion.

## Goals

- Add a Docker Compose Neo4j service using the official upstream image.
- Update both normal developer Compose and `scripts/install.sh` generated Compose so new-user installs include Neo4j.
- Keep graph writes inside `ast-service`; do not introduce a separate graph-indexer service.
- Write Neo4j from the same normalized ast-grep output that populates SQLite.
- Preserve SQLite as the authoritative local structural index.
- Make Neo4j rebuildable from offline indexing if the graph volume is cleared.
- Keep Neo4j optional and configurable so existing AST indexing tests and local development do not require a running graph database.
- Support first-use graph queries for callers, callees, import dependencies, and bounded impact expansion.
- Provide a local `ast-service` development workflow so code changes can be tested without rebuilding the Docker image.

## Non-Goals

- Do not replace SQLite with Neo4j.
- Do not create a new Docker image for Neo4j.
- Do not add APOC or Graph Data Science in the first implementation.
- Do not attempt distributed transactions across SQLite and Neo4j.
- Do not make online chat requests run ast-grep scans.
- Do not promise type-perfect call graph resolution in the first graph release.
- Do not expose arbitrary Cypher execution through Chat UI.
- Do not create `ENCLOSED_BY` relationships in the first release. SQLite currently stores `symbols.parent_name` as a string, not a stable parent symbol id, so this relationship should wait for a reliable linker.

## Architecture

The target indexing flow is:

```text
ast-service /index
  -> discover source files
  -> run ast-grep YAML rules
  -> normalize entities + relations
  -> write SQLite records
  -> link SQLite call symbols
  -> refresh cross-file links and deleted-file state
  -> refresh Neo4j graph facts for indexed repos
```

The important architectural choice is that `ast-service` owns both persistence outputs:

```text
normalized ast-grep facts
  ├── SQLite: files, symbols, calls, imports, SCIP rows
  └── Neo4j: repositories, files, symbols, modules, call/import edges
```

This keeps the system small while avoiding two independent indexing pipelines. Neo4j consumes the same in-memory normalized records generated for SQLite, so there is one extraction implementation and one set of ast-grep rules.

## Failure Semantics

SQLite and Neo4j should not be treated as one atomic transaction. SQLite is local and transactional; Neo4j is a network service. Trying to make them commit atomically would add complexity without enough benefit for an offline, rebuildable index.

The first implementation should use these rules:

- SQLite writes happen inside the existing per-file SQLite transaction.
- SQLite cross-file linking and deleted-file marking happen before graph refresh.
- Neo4j writes happen after SQLite has committed and repo-level linking has run for the indexed repo.
- Neo4j failures mark the current `index_runs` row as `error`.
- SQLite data written before the Neo4j failure remains valid and queryable.
- A future successful full index can rebuild Neo4j from the same source repositories.
- If `NEO4J_ENABLED=false`, ast-service skips graph writes entirely.
- If `NEO4J_ENABLED=true` and Neo4j is unreachable, indexing should fail loudly instead of silently producing a stale graph.

This gives a simple and honest operational model: SQLite is the durable source of structural facts; Neo4j is a synchronized derived view that can be rebuilt.

## Docker Design

Neo4j should be added as a standard Compose service using the official remote image, similar to Qdrant and Sourcebot. No repo-local `Dockerfile` is needed for Neo4j. Pin the server major version instead of using a floating tag so the Python driver and Cypher behavior remain predictable.

Recommended service:

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

Add volumes:

```yaml
  neo4j_data:
  neo4j_logs:
  neo4j_plugins:
```

Add these environment variables to `ast-service`:

```yaml
      - NEO4J_ENABLED=${NEO4J_ENABLED:-true}
      - NEO4J_URI=bolt://neo4j:7687
      - NEO4J_USER=${NEO4J_USER:-neo4j}
      - NEO4J_PASSWORD=${NEO4J_PASSWORD:-repo-bot-neo4j}
      - NEO4J_DATABASE=${NEO4J_DATABASE:-neo4j}
```

`ast-service` should depend on Neo4j when graph indexing is enabled by default. Use a health condition so the container does not start while Neo4j is still initializing:

```yaml
    depends_on:
      neo4j:
        condition: service_healthy
```

`scripts/install.sh` must be updated in parallel because it embeds its own deployment Compose file, pulls images, writes `.env`, starts services, and performs health checks. A deployment created by `install.sh` must include the same Neo4j service, volumes, environment variables, and ast-service Neo4j environment as the repository `docker-compose.yml`.

## Graph Model

The first graph model should stay intentionally small.

Nodes:

```text
(:Repository {name})
(:File {repo, path, language, content_hash})
(:Symbol {repo, symbol_id, path, name, qualified_name, kind, start_line, end_line})
(:ExternalSymbol {repo, name})
(:Module {repo, module_path})
```

Relationships:

```text
(:Repository)-[:CONTAINS]->(:File)
(:File)-[:DEFINES]->(:Symbol)
(:Symbol)-[:CALLS {line, source, confidence}]->(:Symbol)
(:Symbol)-[:CALLS {line, source, confidence}]->(:ExternalSymbol)
(:File)-[:IMPORTS {line, imported_names_json}]->(:Module)
```

First-release constraints:

```cypher
CREATE CONSTRAINT repo_name IF NOT EXISTS
FOR (r:Repository) REQUIRE r.name IS UNIQUE;

CREATE CONSTRAINT file_key IF NOT EXISTS
FOR (f:File) REQUIRE (f.repo, f.path) IS UNIQUE;

CREATE CONSTRAINT symbol_key IF NOT EXISTS
FOR (s:Symbol) REQUIRE (s.repo, s.symbol_id) IS UNIQUE;

CREATE CONSTRAINT external_symbol_key IF NOT EXISTS
FOR (s:ExternalSymbol) REQUIRE (s.repo, s.name) IS UNIQUE;

CREATE CONSTRAINT module_key IF NOT EXISTS
FOR (m:Module) REQUIRE (m.repo, m.module_path) IS UNIQUE;
```

`symbol_id` should use the SQLite `symbols.id` after SQLite insert. That keeps Neo4j identity stable across a single SQLite database. A full rebuild may create new IDs if SQLite is rebuilt; this is acceptable because Neo4j is derived and rebuilt as a whole.

`Symbol.path` is intentionally denormalized even though `(:File)-[:DEFINES]->(:Symbol)` already exists. It supports direct symbol-result rendering and cheaper graph API payloads without an extra file traversal.

## Repo Refresh Strategy

Graph writes should be repo-scoped and idempotent in the first implementation. This is slightly less granular than per-file graph writes, but it produces a more accurate graph because the existing indexer only resolves cross-file `callee_symbol_id` values after all changed files have been processed.

For each indexed repo:

1. Ensure graph constraints exist.
2. In one explicit Neo4j write transaction, delete the existing graph for that repo:
   `MATCH (n) WHERE n.repo = $repo DETACH DELETE n`, plus `MATCH (r:Repository {name: $repo}) DETACH DELETE r`.
3. Merge the repository node once.
4. Merge active file nodes from SQLite.
5. Merge symbol nodes from active files.
6. Recreate `CONTAINS`, `DEFINES`, `IMPORTS`, and `CALLS` relationships.
7. Link unresolved calls to `ExternalSymbol` nodes.
8. Ignore rows whose file has `deleted_at IS NOT NULL`.

The repo delete is required in the first release. Without it, deleted files and removed symbols would remain in Neo4j because `MERGE` does not remove stale nodes or relationships.

All graph writes for a repo refresh should run inside one Neo4j transaction using `session.execute_write()` or an equivalent explicit transaction wrapper. If a batch fails, the whole repo graph refresh rolls back instead of leaving a half-updated graph.

Batch writes should use `UNWIND $rows AS row` with a bounded batch size. The default batch size should be `1000` rows to avoid excessive transaction memory usage on large repos.

This favors correctness and simplicity for the first graph release. A later optimization can replace repo refresh with changed-file refresh once graph drift and performance characteristics are known.

## Cross-File Linking

The current indexer already runs `link_callee_symbols(conn, indexed_repo)` after file indexing. Neo4j call edges should use the linked SQLite rows where possible.

Recommended first implementation:

- After SQLite cross-file linking completes for a repo, refresh Neo4j call edges for that repo from SQLite `calls`.
- Use `callee_symbol_id` when available to point to `(:Symbol)`.
- Use `ExternalSymbol` when `callee_symbol_id` is null.
- Run Neo4j refresh before `finish_index_run(..., "ok", ...)`. If graph refresh fails while enabled, the existing `try/except` path must mark the run as `error`.
- In incremental mode, refresh only repos that had indexed files or deleted files. Do not refresh every previously indexed repo on every incremental run.

This is more accurate than writing all call edges before cross-file linking has run.

## FastAPI Surface

Add minimal graph endpoints to `ast-service`:

```text
GET  /graph/health
POST /graph/sync?repo=<repo>
GET  /graph/impact?repo=<repo>&symbol=<name>&depth=2&limit=50
GET  /graph/call-paths?repo=<repo>&from_symbol=<name>&to_symbol=<name>&max_depth=4
```

The `/graph/sync` endpoint is a recovery path that rebuilds Neo4j from SQLite for one repo or all repos. It is not a separate service; it lives in `ast-service`.

Chat UI can later call `/graph/impact` or `/graph/call-paths` after Sourcebot/Qdrant/AST retrieval produces candidate symbols.

Neo4j driver lifecycle belongs in FastAPI lifespan. `main.py` should create one driver pool at startup when graph is enabled, store it on `app.state`, verify connectivity with bounded retries, and close it during shutdown. Request handlers should reuse `app.state.neo4j_driver`; they must not create a new driver per request.

Offline CLI indexing should also verify Neo4j connectivity with bounded retries before starting graph-enabled indexing. This covers `npm run index:ast`, where `docker compose` may have started the container before Neo4j is fully ready.

## Configuration

Add `.env.example` settings:

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

In container-to-container traffic, `ast-service` should override `NEO4J_URI` to `bolt://neo4j:7687`.

`NEO4J_ENABLED` intentionally has different defaults by context:

- Compose sets `NEO4J_ENABLED=${NEO4J_ENABLED:-true}` so deployed ast-service writes the graph by default.
- `GraphConfig.from_env()` should default to disabled when the variable is unset, so direct local tests do not require Neo4j.

## Local Development Workflow

`ast-service` should have the same kind of local development loop that `chat-ui` already has. Developers should not need to rebuild the `ast-service` Docker image for every Python change.

The desired `npm run dev` behavior is:

```text
npm run dev
  -> load .env
  -> stop chat-ui container
  -> stop ast-service container
  -> start shared dependency containers without rebuilding:
       sourcebot, qdrant, neo4j
  -> run ast-service locally with uvicorn on localhost:8502
  -> run chat-ui locally with streamlit on localhost:8501
  -> stop local ast-service when the dev script exits
```

Local development environment differences:

- `REPOS_ROOT` should remain the host path from `.env`, not `/repos`.
- `AST_DB_PATH` should default to a local ignored path such as `.data/ast.sqlite`.
- `NEO4J_URI` should default to `bolt://localhost:7687`.
- `AST_SERVICE_URL` for Chat UI should be `http://localhost:8502`.
- Python dependencies for both `chat-ui` and `ast-service` should be installed into the existing repo `.venv` when available.

The project should also provide a narrower command:

```text
npm run dev:ast
  -> stop ast-service container
  -> start dependency containers
  -> run uvicorn main:app --reload --port 8502 from ast-service/
```

This gives a fast loop for debugging graph writes, API routes, and indexing behavior without running Streamlit.

The full-stack dev script should clean up local uvicorn reliably. It should trap `EXIT`, `INT`, and `TERM`, kill the uvicorn child process, and wait for it to exit so Ctrl+C does not normally leave an orphaned local service.

## Testing

Existing AST tests must keep passing without a real Neo4j container. Tests should set or rely on `NEO4J_ENABLED=false` by default unless they are explicitly graph integration tests.

Unit tests should cover:

- Neo4j config parsing.
- Graph disabled branch.
- Generated Cypher parameters for repositories, files, symbols, imports, and calls.
- Graph sync behavior against a fake driver/session.
- Repo delete happens before graph recreation.
- Repo refresh is wrapped in one explicit write transaction.
- FastAPI lifespan creates and closes a shared driver.
- Incremental indexing refreshes only changed/deleted repos.
- Existing indexing flow still writes SQLite when graph is disabled.

Optional integration tests can run against Docker Compose Neo4j, but they should not be required by the default `python -m pytest -v` command.

## Rollout

1. Add Docker Compose Neo4j and environment templates.
2. Add optional Neo4j dependency to `ast-service`.
3. Add graph writer module with disabled-by-default test behavior.
4. Initialize constraints on ast-service startup or first graph write.
5. Wire graph refresh into offline indexing after SQLite writes and repo-level linking.
6. Add recovery endpoint to rebuild Neo4j from SQLite.
7. Add graph query endpoints.
8. Update Chat UI in a later change to use graph context.

## Open Decisions

- Whether Chat UI graph retrieval belongs in the first Neo4j implementation. Recommended: not in the first implementation; first prove indexing and query endpoints.
