# ast-grep SQLite Structure Index Design

## Summary

Add an `ast-service` to repo-bot that uses ast-grep to build a persistent structural code index for repositories under `REPOS_ROOT`.

The service runs offline preprocessing jobs over many repositories, executes predefined ast-grep YAML rules, stores extracted symbols, calls, and imports in SQLite, and exposes FastAPI query endpoints. Chat UI can then enrich the existing Qdrant + Sourcebot retrieval flow with structure-aware context.

This design intentionally does not introduce NetworkX or Neo4j. SQLite is the only structural persistence layer for this phase.

## Goals

- Use ast-grep's official matching model as much as possible.
- Run offline batch scans across many repositories.
- Store symbols, calls, and imports in SQLite.
- Expose query APIs for structural lookup.
- Integrate structural context into the current Chat UI retrieval flow.
- Keep ast-grep integration optional and degradable so the existing Qdrant + Sourcebot flow remains usable if `ast-service` is unavailable.

## Non-Goals

- Do not introduce NetworkX.
- Do not introduce Neo4j yet.
- Do not directly traverse tree-sitter ASTs in application code.
- Do not write a custom rule DSL.
- Do not perform full-repository ast-grep scans during normal chat requests.
- Do not promise perfect type-aware call graph resolution in the first phase.

## Existing Context

The current repo-bot architecture has three main services:

- Sourcebot for Zoekt trigram search.
- Qdrant for vector semantic retrieval.
- Chat UI for Streamlit-based search, result fusion, and LLM answers.

Relevant files:

- `docker-compose.yml`
- `package.json`
- `chat-ui/app.py`
- `chat-ui/index_code.py`

The current Chat UI already performs:

```text
user question
  -> search_sourcebot()
  -> search_qdrant()
  -> merge_results()
  -> read_file_content()
  -> ask_llm()
```

The ast-grep integration should fit after retrieval fusion and before LLM context construction.

## Recommended Approach

Use an independent `ast-service` container:

```text
REPOS_ROOT/*
  |
  |-- Sourcebot
  |     `-- exact and regex-like search
  |
  |-- Qdrant
  |     `-- semantic vector search
  |
  |-- ast-service
  |     |-- offline ast-grep indexing
  |     |-- YAML rules for symbols/calls/imports
  |     |-- SQLite persistence
  |     `-- FastAPI query endpoints
  |
  `-- Chat UI
        |-- Qdrant retrieval
        |-- Sourcebot retrieval
        |-- ast-service SQLite queries
        `-- LLM answer generation
```

This keeps structural indexing separate from the Streamlit UI and makes the future SQLite-to-Neo4j transition easier.

## ast-grep Usage Model

Application code should interact with ast-grep through official abstractions:

- YAML rules.
- Patterns.
- Captures.
- Match ranges and source text.
- Language-specific ast-grep parsing.

Application code should not:

- Recursively walk tree-sitter nodes.
- Depend on raw tree-sitter AST APIs.
- Use tree-sitter queries directly.
- Hard-code extraction through `node.kind`, `field("name")`, or `child(0)` in Python.

tree-sitter remains an internal implementation detail of ast-grep.

The indexing flow should look like this:

```text
source file
  -> ast-grep YAML rule
  -> matches and captures
  -> normalizer
  -> symbols/calls/imports rows
  -> SQLite
```

## Offline Indexing Model

Offline preprocessing is the primary path.

Full indexing:

```text
python indexer.py --mode full
  -> scan REPOS_ROOT/*
  -> identify repositories
  -> filter supported source files
  -> choose rules by language
  -> execute ast-grep rules
  -> normalize captures
  -> write SQLite rows in transactions
  -> record index_runs status
```

Incremental indexing:

```text
python indexer.py --mode incremental
  -> scan REPOS_ROOT/*
  -> compare size, mtime, and content_hash
  -> reindex changed files
  -> mark deleted files
  -> refresh best-effort call symbol links
  -> record index_runs status
```

Online chat requests should query SQLite through FastAPI. They should not trigger full-repository ast-grep scans.

## Service Layout

```text
ast-service/
├── Dockerfile
├── requirements.txt
├── main.py
├── indexer.py
├── db.py
├── models.py
├── astgrep_runner.py
├── repository_scanner.py
├── normalizer.py
└── rules/
    ├── python-symbols.yml
    ├── python-calls.yml
    ├── python-imports.yml
    ├── ts-symbols.yml
    ├── ts-calls.yml
    ├── ts-imports.yml
    └── common.yml
```

Responsibilities:

- `main.py`: FastAPI app and route registration.
- `indexer.py`: full and incremental indexing orchestration.
- `db.py`: SQLite connection handling, schema creation, and transactions.
- `models.py`: request and response models.
- `astgrep_runner.py`: ast-grep-py execution wrapper, with room for CLI fallback if needed.
- `repository_scanner.py`: repository and file discovery under `REPOS_ROOT`.
- `normalizer.py`: convert ast-grep match/capture output into stable database records.
- `rules/`: ast-grep YAML rules. Rules should be independently debuggable with ast-grep CLI.

## Initial Language Scope

Start with a small, useful language set:

- Python.
- JavaScript.
- TypeScript.

The current vector indexer supports more extensions, but the structural index should expand only after the first rules are reliable.

Later candidates:

- Go.
- Rust.
- Java.
- C/C++.

## SQLite Schema

Use SQLite as the source of truth for the phase-one structural index.

```sql
CREATE TABLE repositories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  root_path TEXT NOT NULL
);

CREATE TABLE index_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo TEXT,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  files_seen INTEGER DEFAULT 0,
  files_indexed INTEGER DEFAULT 0,
  symbols_count INTEGER DEFAULT 0,
  calls_count INTEGER DEFAULT 0,
  imports_count INTEGER DEFAULT 0,
  error TEXT
);

CREATE TABLE files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo TEXT NOT NULL,
  path TEXT NOT NULL,
  language TEXT,
  size INTEGER,
  mtime REAL,
  content_hash TEXT,
  indexed_at TEXT,
  deleted_at TEXT,
  UNIQUE(repo, path)
);

CREATE TABLE symbols (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  repo TEXT NOT NULL,
  name TEXT NOT NULL,
  qualified_name TEXT,
  kind TEXT NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER,
  signature TEXT,
  parent_name TEXT
);

CREATE TABLE calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  repo TEXT NOT NULL,
  caller_symbol_id INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
  callee_name TEXT NOT NULL,
  callee_symbol_id INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
  call_line INTEGER NOT NULL
);

CREATE TABLE imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  repo TEXT NOT NULL,
  module_path TEXT NOT NULL,
  imported_names_json TEXT,
  import_line INTEGER NOT NULL
);
```

Indexes:

```sql
CREATE INDEX idx_files_repo_path ON files(repo, path);
CREATE INDEX idx_files_repo_hash ON files(repo, content_hash);
CREATE INDEX idx_symbols_repo_name ON symbols(repo, name);
CREATE INDEX idx_symbols_qualified ON symbols(repo, qualified_name);
CREATE INDEX idx_calls_repo_callee ON calls(repo, callee_name);
CREATE INDEX idx_calls_repo_caller ON calls(repo, caller_symbol_id);
CREATE INDEX idx_imports_repo_module ON imports(repo, module_path);
```

## Call Graph Semantics

The first version should store a useful, approximate call graph:

- `caller_symbol_id`: resolved when the call occurs inside a known symbol range.
- `callee_name`: always stored as a string from ast-grep captures.
- `callee_symbol_id`: best-effort link to a symbol in the same repository.

This is intentionally not a full type-aware call graph. Dynamic dispatch, imported aliases, monkey patching, and language-specific overload resolution are out of scope for the first phase.

## FastAPI Endpoints

Health and status:

```text
GET /health
GET /status
GET /runs?repo=repo-bot&limit=10
```

Indexing:

```text
POST /index
{
  "repo": "repo-bot",
  "mode": "full"
}

POST /index
{
  "repo": "repo-bot",
  "mode": "incremental"
}
```

Querying:

```text
GET /symbols?repo=repo-bot&name=foo&kind=function&limit=20
GET /symbols/{id}
GET /calls?repo=repo-bot&caller_name=foo&limit=50
GET /calls?repo=repo-bot&callee_name=bar&limit=50
GET /imports?repo=repo-bot&module=fastapi&limit=50
```

Restricted realtime ast-grep search:

```text
POST /search
{
  "repo": "repo-bot",
  "language": "Python",
  "pattern": "$FUNC($$$ARGS)",
  "path_glob": "**/*.py",
  "limit": 50
}
```

`/search` is an auxiliary feature. It should be bounded by `repo`, `path_glob`, and `limit`. It should not become the default chat-time full scan mechanism.

## Chat UI Integration

Add an optional structural retrieval step to `chat-ui/app.py`.

Proposed flow:

```text
user question
  -> search_sourcebot()
  -> search_qdrant()
  -> merge_results()
  -> extract candidate symbol names from query and retrieved snippets
  -> query ast-service for symbols/calls/imports
  -> append concise structural context
  -> ask_llm()
```

Example context injected into the LLM prompt:

```text
[structure] foo() definition: repo/path.py:L12-L48
[structure] foo() calls: bar(), baz()
[structure] foo() callers: handler() @ repo/api.py:L31
[structure] repo/path.py imports: fastapi.APIRouter, pydantic.BaseModel
```

The context should be short and factual. It should not replace source snippets from Qdrant or Sourcebot.

Add Chat UI controls:

- Sidebar checkbox: `AST 结构检索`.
- Sidebar caption for `AST_SERVICE_URL`.

Failure behavior:

- If `ast-service` is unavailable, return no structural results.
- Do not break Qdrant or Sourcebot retrieval.
- Do not surface noisy stack traces in the UI.

## Docker Compose Changes

Add a new service:

```yaml
ast-service:
  build: ./ast-service
  ports:
    - "8502:8502"
  volumes:
    - ${REPOS_ROOT:-~/projects}:/repos:ro
    - ast_data:/data
  environment:
    - REPOS_ROOT=/repos
    - AST_DB_PATH=/data/ast.sqlite
  restart: unless-stopped
```

Add to `chat-ui`:

```yaml
environment:
  - AST_SERVICE_URL=http://ast-service:8502
depends_on:
  - ast-service
```

Add a volume:

```yaml
volumes:
  ast_data:
```

## npm Scripts

Add:

```json
{
  "index:ast": "docker exec repo-bot-ast-service-1 python /app/indexer.py --mode full",
  "index:ast:incr": "docker exec repo-bot-ast-service-1 python /app/indexer.py --mode incremental",
  "open:ast": "open http://localhost:8502/docs"
}
```

## Implementation Plan

1. Create `ast-service` skeleton.
   - Add Dockerfile and requirements.
   - Add FastAPI app with `/health`.
   - Add SQLite initialization.

2. Implement SQLite schema.
   - Add DDL for repositories, index_runs, files, symbols, calls, and imports.
   - Add indexes.
   - Add transaction helpers for replacing one file's extracted records.

3. Implement ast-grep execution wrapper.
   - Use ast-grep-py as the primary execution path.
   - Load YAML rules from `rules/`.
   - Return normalized match, capture, range, and text data.
   - Keep CLI fallback possible if ast-grep-py lacks a needed YAML feature.

4. Write first YAML rules.
   - Python symbols, calls, imports.
   - TypeScript and JavaScript symbols, calls, imports.
   - Verify rules independently with ast-grep CLI where possible.

5. Implement full offline indexing.
   - Walk `/repos`.
   - Identify repository name from the first path segment.
   - Filter supported file extensions and skipped directories.
   - Execute language rules.
   - Batch insert extracted rows.
   - Record `index_runs`.

6. Implement incremental indexing.
   - Compare `size`, `mtime`, and `content_hash`.
   - Reindex changed files.
   - Mark deleted files.
   - Recompute best-effort `callee_symbol_id` links.

7. Implement query APIs.
   - `/symbols`
   - `/symbols/{id}`
   - `/calls`
   - `/imports`
   - `/status`
   - `/runs`

8. Implement restricted realtime `/search`.
   - Support `repo`, `language`, `pattern`, `path_glob`, and `limit`.
   - Enforce limits to avoid accidental full-repository scans during chat.

9. Add Docker and npm integration.
   - Add `ast-service` to compose.
   - Add `ast_data` volume.
   - Add npm scripts.
   - Add `.env.example` entries for AST configuration.

10. Integrate Chat UI.
    - Add `AST_SERVICE_URL`.
    - Add sidebar toggle.
    - Add ast-service client function.
    - Extract candidate symbols from query and merged results.
    - Inject structural context into `ask_llm()`.

11. Verify end to end.
    - Create a small fixture repository for Python and TypeScript.
    - Run full index.
    - Query symbols, calls, and imports.
    - Run incremental index after modifying a file.
    - Confirm Chat UI answers include structural context.
    - Confirm Chat UI degrades cleanly if `ast-service` is down.

## Verification Plan

Minimum checks:

```text
docker compose build ast-service
docker compose up -d ast-service
npm run index:ast
curl http://localhost:8502/health
curl "http://localhost:8502/symbols?repo=repo-bot&limit=10"
curl "http://localhost:8502/calls?repo=repo-bot&callee_name=foo&limit=10"
```

Functional checks:

- Known fixture functions appear in `/symbols`.
- Known fixture call edges appear in `/calls`.
- Known fixture imports appear in `/imports`.
- Incremental indexing updates changed files without rebuilding unchanged files.
- Deleted files no longer contribute active symbols or calls.
- Chat UI produces useful answers for "who calls X" and "what does X depend on" questions.

## Risks and Mitigations

Risk: ast-grep-py may not expose every YAML scan feature needed.

Mitigation: keep `astgrep_runner.py` isolated and allow a CLI fallback for rule execution without changing database or API layers.

Risk: YAML rules are language-specific and can be brittle.

Mitigation: start with Python and JavaScript/TypeScript, use fixtures, and expand language support incrementally.

Risk: call graph precision may be overestimated.

Mitigation: document call edges as best-effort and store unresolved `callee_name` even when no symbol link exists.

Risk: SQLite writes can become slow on very large repository sets.

Mitigation: use transactions, batch inserts, WAL mode, and per-file replacement rather than row-by-row commits.

Risk: Chat UI prompt context can become noisy.

Mitigation: cap structural context size and include only concise, ranked facts.

## Acceptance Criteria

- `ast-service` can build and start through Docker Compose.
- A full offline index over `REPOS_ROOT` writes files, symbols, calls, and imports to SQLite.
- Incremental indexing only processes changed or deleted files.
- FastAPI exposes working symbols, calls, imports, status, and health endpoints.
- Chat UI can query structural data when enabled.
- Chat UI still works when ast-service is disabled or unavailable.
- No application code directly traverses tree-sitter ASTs.
- Rules live as ast-grep YAML files and can be debugged independently.

