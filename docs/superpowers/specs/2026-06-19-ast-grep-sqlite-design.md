# ast-grep SQLite Structure Index Design

## Summary

Add an `ast-service` to repo-bot that uses ast-grep to build a persistent, SCIP-compatible structural code index for repositories under `REPOS_ROOT`.

The service runs offline preprocessing jobs over many repositories, executes predefined ast-grep YAML rules, stores SCIP-style documents, occurrences, symbols, relationships, and convenience symbols/calls/imports in SQLite, and exposes FastAPI query endpoints. Chat UI can then enrich the existing Qdrant + Sourcebot retrieval flow with structure-aware context.

This design intentionally does not introduce NetworkX or Neo4j. SQLite is the only structural persistence layer for this phase.

References:

- SCIP official repository: `https://github.com/scip-code/scip`
- SCIP protobuf contract: `https://raw.githubusercontent.com/sourcegraph/scip/main/scip.proto`

## Goals

- Use ast-grep's official matching model as much as possible.
- Run offline batch scans across many repositories.
- Store symbols, calls, and imports in SQLite.
- Store a SCIP-compatible internal model: documents, occurrences, symbol information, and relationships.
- Support exporting indexed data to SCIP protobuf format once the core index is stable.
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
- Do not require Chat UI to consume SCIP directly. Chat UI should use purpose-built FastAPI query endpoints.

## SCIP Compatibility Target

SCIP is the compatibility format for this design, not the primary Chat UI query API. The SQLite schema should keep enough normalized data to emit SCIP `Index`, `Document`, `Occurrence`, `SymbolInformation`, and `Relationship` records.

Compatibility requirements:

- Store one logical SCIP document per indexed source file.
- Store symbol occurrences with 0-based line and character ranges.
- Store symbol roles as integer bitsets compatible with SCIP `SymbolRole`.
- Store stable SCIP symbol strings in addition to display names.
- Store symbol information separately from occurrences.
- Store relationships separately from convenience call edges.
- Record `position_encoding`; use UTF-8 unless a later implementation proves another encoding is required.

The service may still keep repo-bot-specific convenience tables such as `symbols`, `calls`, and `imports`. Those tables exist for simple SQLite queries and Chat UI enrichment; they should be derived from, or at least consistent with, the SCIP-compatible rows.

Relevant SCIP concepts:

- `Index`: repository-level export container.
- `Document`: one source file and its occurrences.
- `Occurrence`: symbol occurrence with range, roles, syntax kind, and optional enclosing range.
- `SymbolInformation`: metadata for a symbol.
- `Relationship`: reference, implementation, type-definition, or definition relationship between symbols.

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
├── scip.py
├── scip_proto/
│   ├── __init__.py
│   └── scip_pb2.py
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
- `scip.py`: construct SCIP-compatible symbols, ranges, roles, symbol information, relationships, and export payloads.
- `scip_proto/`: generated Python protobuf bindings from the official SCIP `scip.proto`.
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

CREATE TABLE scip_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  repo TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  language TEXT,
  position_encoding TEXT NOT NULL DEFAULT 'UTF8',
  UNIQUE(file_id)
);

CREATE TABLE scip_symbols (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo TEXT NOT NULL,
  scip_symbol TEXT NOT NULL,
  display_name TEXT NOT NULL,
  kind TEXT,
  documentation TEXT,
  signature_documentation TEXT,
  enclosing_symbol TEXT,
  UNIQUE(repo, scip_symbol)
);

CREATE TABLE scip_occurrences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL REFERENCES scip_documents(id) ON DELETE CASCADE,
  repo TEXT NOT NULL,
  scip_symbol TEXT NOT NULL,
  range_start_line INTEGER NOT NULL,
  range_start_character INTEGER NOT NULL,
  range_end_line INTEGER NOT NULL,
  range_end_character INTEGER NOT NULL,
  symbol_roles INTEGER NOT NULL DEFAULT 0,
  syntax_kind TEXT,
  enclosing_range_json TEXT
);

CREATE TABLE scip_relationships (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo TEXT NOT NULL,
  source_symbol TEXT NOT NULL,
  target_symbol TEXT NOT NULL,
  is_reference INTEGER NOT NULL DEFAULT 0,
  is_implementation INTEGER NOT NULL DEFAULT 0,
  is_type_definition INTEGER NOT NULL DEFAULT 0,
  is_definition INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE symbols (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  repo TEXT NOT NULL,
  scip_symbol TEXT,
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
  caller_scip_symbol TEXT,
  callee_name TEXT NOT NULL,
  callee_symbol_id INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
  callee_scip_symbol TEXT,
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
CREATE INDEX idx_scip_documents_repo_path ON scip_documents(repo, relative_path);
CREATE INDEX idx_scip_symbols_repo_symbol ON scip_symbols(repo, scip_symbol);
CREATE INDEX idx_scip_occurrences_symbol ON scip_occurrences(repo, scip_symbol);
CREATE INDEX idx_scip_relationships_source ON scip_relationships(repo, source_symbol);
CREATE INDEX idx_scip_relationships_target ON scip_relationships(repo, target_symbol);
CREATE INDEX idx_symbols_repo_name ON symbols(repo, name);
CREATE INDEX idx_symbols_qualified ON symbols(repo, qualified_name);
CREATE INDEX idx_calls_repo_callee ON calls(repo, callee_name);
CREATE INDEX idx_calls_repo_caller ON calls(repo, caller_symbol_id);
CREATE INDEX idx_imports_repo_module ON imports(repo, module_path);
```

## SCIP Mapping Rules

The first implementation should use a deterministic repo-local SCIP symbol scheme. It does not need to claim package-manager-level precision yet, but it must be stable across indexing runs for unchanged files.

Recommended symbol shape:

```text
local <repo> <relative-path> / <descriptor-chain>.
```

Examples:

```text
local repo-bot chat-ui/app.py / search_qdrant().
local repo-bot chat-ui/app.py / UserService#get_user().
local repo-bot chat-ui/app.py / UserService#
```

Mapping rules:

- `scip_documents.relative_path` stores the path relative to `REPOS_ROOT`.
- `scip_occurrences` ranges are 0-based line and 0-based UTF-8 character offsets.
- Function and class definitions create both `scip_symbols` and definition occurrences.
- Call sites create reference occurrences when the callee can be mapped to a SCIP symbol.
- Imports create reference occurrences when they can be mapped to local symbols; otherwise they remain in the convenience `imports` table.
- `symbols.scip_symbol` links convenience symbol rows to `scip_symbols.scip_symbol`.
- `calls.caller_scip_symbol` and `calls.callee_scip_symbol` link convenience call edges to SCIP symbols when known.
- `scip_relationships` should represent symbol-to-symbol relationships. Convenience `calls` may be generated from occurrences and relationships, but it should not be the only representation.

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
GET /scip/documents?repo=repo-bot&limit=50
GET /scip/occurrences?repo=repo-bot&symbol=...&limit=50
GET /scip/symbols?repo=repo-bot&prefix=local%20repo-bot&limit=50
```

SCIP export:

```text
GET /scip/export?repo=repo-bot
  -> application/octet-stream SCIP protobuf payload

GET /scip/export.json?repo=repo-bot
  -> JSON debug view of the SCIP-shaped export payload
```

The JSON endpoint exists for testing and debugging. The protobuf export is the compatibility target.

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
   - Add DDL for repositories, index_runs, files, SCIP documents, SCIP symbols, SCIP occurrences, SCIP relationships, symbols, calls, and imports.
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

5. Implement SCIP mapping.
   - Generate stable repo-local SCIP symbols.
   - Convert line/column positions to 0-based SCIP ranges.
   - Map definitions, references, and imports into SCIP-compatible rows.

6. Implement full offline indexing.
   - Walk `/repos`.
   - Identify repository name from the first path segment.
   - Filter supported file extensions and skipped directories.
   - Execute language rules.
   - Batch insert extracted rows.
   - Record `index_runs`.

7. Implement incremental indexing.
   - Compare `size`, `mtime`, and `content_hash`.
   - Reindex changed files.
   - Mark deleted files.
   - Recompute best-effort `callee_symbol_id` links.

8. Implement query APIs.
   - `/symbols`
   - `/symbols/{id}`
   - `/calls`
   - `/imports`
   - `/scip/documents`
   - `/scip/occurrences`
   - `/scip/symbols`
   - `/status`
   - `/runs`

9. Implement SCIP export.
   - Add JSON debug export first.
   - Add protobuf `.scip` export once rows map cleanly to SCIP fields.
   - Add fixture tests that verify exported documents, occurrences, symbols, and relationships.

10. Implement restricted realtime `/search`.
   - Support `repo`, `language`, `pattern`, `path_glob`, and `limit`.
   - Enforce limits to avoid accidental full-repository scans during chat.

11. Add Docker and npm integration.
   - Add `ast-service` to compose.
   - Add `ast_data` volume.
   - Add npm scripts.
   - Add `.env.example` entries for AST configuration.

12. Integrate Chat UI.
    - Add `AST_SERVICE_URL`.
    - Add sidebar toggle.
    - Add ast-service client function.
    - Extract candidate symbols from query and merged results.
    - Inject structural context into `ask_llm()`.

13. Verify end to end.
    - Create a small fixture repository for Python and TypeScript.
    - Run full index.
    - Query symbols, calls, and imports.
    - Export SCIP JSON/protobuf and verify it contains expected documents and occurrences.
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
- A full offline index also writes SCIP documents, occurrences, symbols, and relationships where they can be inferred.
- Incremental indexing only processes changed or deleted files.
- FastAPI exposes working symbols, calls, imports, status, and health endpoints.
- FastAPI exposes SCIP debug query endpoints and a SCIP export endpoint.
- Chat UI can query structural data when enabled.
- Chat UI still works when ast-service is disabled or unavailable.
- No application code directly traverses tree-sitter ASTs.
- Rules live as ast-grep YAML files and can be debugged independently.
- Exported SCIP data uses stable symbols, 0-based ranges, and declared position encoding.
