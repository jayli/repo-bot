# ast-grep SQLite Structure Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an `ast-service` that runs offline ast-grep scans over repositories, stores SCIP-compatible structure plus convenience symbols/calls/imports in SQLite, exposes FastAPI query/export endpoints, and lets Chat UI enrich answers with structural context.

**Architecture:** Add an independent Dockerized FastAPI service beside Sourcebot, Qdrant, and Chat UI. The service uses ast-grep YAML rules for extraction, normalizes matches into SCIP-style documents/occurrences/symbols/relationships plus convenience tables, persists everything in SQLite, and exposes bounded APIs for indexing, querying, SCIP export, and realtime pattern search. Chat UI calls the service optionally after Qdrant/Sourcebot fusion and degrades cleanly if unavailable.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, SQLite, pydantic, ast-grep-py, PyYAML, protobuf, requests, Docker Compose, Streamlit.

---

## Reference Spec

Implement from:

- `docs/superpowers/specs/2026-06-19-ast-grep-sqlite-design.md`

Important constraints:

- Do not introduce NetworkX.
- Do not introduce Neo4j.
- Do not directly traverse tree-sitter ASTs in application code.
- Do not write a custom rule DSL.
- Primary path is offline preprocessing into SQLite.
- Online chat should query SQLite through `ast-service`, not scan all repos.
- Keep SQLite as the local source of truth, but make its core rows mappable to SCIP `Index`, `Document`, `Occurrence`, `SymbolInformation`, and `Relationship`.
- Use stable SCIP symbols, 0-based ranges, symbol roles, and declared position encoding.

## Target File Structure

Create:

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
├── rules/
│   ├── python-symbols.yml
│   ├── python-calls.yml
│   ├── python-imports.yml
│   ├── ts-symbols.yml
│   ├── ts-calls.yml
│   └── ts-imports.yml
└── tests/
    ├── fixtures/
    │   ├── sample-python/
    │   │   └── app.py
    │   └── sample-ts/
    │       └── app.ts
    ├── test_db.py
    ├── test_repository_scanner.py
    ├── test_normalizer.py
    ├── test_scip.py
    ├── test_indexer.py
    └── test_api.py
```

Modify:

```text
docker-compose.yml
package.json
.env.example
chat-ui/app.py
README.md
```

Responsibilities:

- `db.py`: SQLite schema, connection helpers, transactions, per-file replacement.
- `models.py`: pydantic request/response models.
- `repository_scanner.py`: repository discovery and source file filtering.
- `astgrep_runner.py`: ast-grep-py wrapper and YAML rule loading. Keep this isolated so CLI fallback can be added later.
- `normalizer.py`: convert ast-grep match/capture output into database records.
- `scip.py`: build SCIP symbols, ranges, roles, symbol information, relationships, and export payloads.
- `scip_proto/`: generated Python protobuf bindings from the official SCIP `scip.proto`.
- `indexer.py`: full/incremental offline indexing.
- `main.py`: FastAPI routes.
- `rules/*.yml`: ast-grep official YAML rules.
- `chat-ui/app.py`: optional structural retrieval and prompt enrichment.

## Task 1: Add ast-service Python Skeleton

**Files:**
- Create: `ast-service/requirements.txt`
- Create: `ast-service/Dockerfile`
- Create: `ast-service/main.py`
- Create: `ast-service/models.py`
- Create: `ast-service/tests/test_api.py`

- [ ] **Step 1: Add dependencies**

Create `ast-service/requirements.txt`:

```text
fastapi>=0.115
uvicorn[standard]>=0.30
pydantic>=2.7
PyYAML>=6.0
ast-grep-py>=0.35
protobuf>=5.0
pytest>=8.0
httpx>=0.27
```

- [ ] **Step 2: Add Dockerfile**

Create `ast-service/Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8502
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8502"]
```

- [ ] **Step 3: Add initial pydantic models**

Create `ast-service/models.py` with:

```python
from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class IndexRequest(BaseModel):
    repo: str | None = None
    mode: Literal["full", "incremental"] = "incremental"


class IndexResponse(BaseModel):
    status: str
    run_id: int | None = None
    files_seen: int = 0
    files_indexed: int = 0
    symbols_count: int = 0
    calls_count: int = 0
    imports_count: int = 0


class SearchRequest(BaseModel):
    repo: str
    language: str
    pattern: str
    path_glob: str = "**/*"
    limit: int = Field(default=50, ge=1, le=200)
```

- [ ] **Step 4: Add FastAPI health route**

Create `ast-service/main.py` with:

```python
from fastapi import FastAPI

from models import HealthResponse


app = FastAPI(title="repo-bot ast-service")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()
```

- [ ] **Step 5: Add API smoke test**

Create `ast-service/tests/test_api.py`:

```python
from fastapi.testclient import TestClient

from main import app


def test_health_returns_ok():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 6: Run the test**

Run:

```bash
cd ast-service && pytest tests/test_api.py -v
```

Expected: one passing test.

- [ ] **Step 7: Commit**

```bash
git add ast-service
git commit -m "feat(ast-service): add FastAPI service skeleton"
```

## Task 2: Implement SQLite Schema and Data Access

**Files:**
- Create: `ast-service/db.py`
- Create: `ast-service/tests/test_db.py`
- Modify: `ast-service/main.py`

- [ ] **Step 1: Write failing database schema tests**

Create `ast-service/tests/test_db.py`:

```python
import sqlite3

from db import connect_db, init_db


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row[0] for row in rows}


def test_init_db_creates_expected_tables(tmp_path):
    db_path = tmp_path / "ast.sqlite"
    conn = connect_db(str(db_path))
    init_db(conn)

    assert {
        "repositories",
        "index_runs",
        "files",
        "scip_documents",
        "scip_symbols",
        "scip_occurrences",
        "scip_relationships",
        "symbols",
        "calls",
        "imports",
    }.issubset(table_names(conn))


def test_foreign_keys_are_enabled(tmp_path):
    conn = connect_db(str(tmp_path / "ast.sqlite"))
    enabled = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert enabled == 1
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd ast-service && pytest tests/test_db.py -v
```

Expected: fail because `db.py` does not exist.

- [ ] **Step 3: Implement `db.py` schema**

Create `ast-service/db.py`:

```python
import os
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone


DEFAULT_DB_PATH = "/data/ast.sqlite"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path() -> str:
    return os.environ.get("AST_DB_PATH", DEFAULT_DB_PATH)


def connect_db(path: str | None = None) -> sqlite3.Connection:
    selected_path = path or db_path()
    parent = os.path.dirname(selected_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(selected_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS repositories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  root_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS index_runs (
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

CREATE TABLE IF NOT EXISTS files (
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

CREATE TABLE IF NOT EXISTS scip_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  repo TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  language TEXT,
  position_encoding TEXT NOT NULL DEFAULT 'UTF8',
  UNIQUE(file_id)
);

CREATE TABLE IF NOT EXISTS scip_symbols (
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

CREATE TABLE IF NOT EXISTS scip_occurrences (
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

CREATE TABLE IF NOT EXISTS scip_relationships (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo TEXT NOT NULL,
  source_symbol TEXT NOT NULL,
  target_symbol TEXT NOT NULL,
  is_reference INTEGER NOT NULL DEFAULT 0,
  is_implementation INTEGER NOT NULL DEFAULT 0,
  is_type_definition INTEGER NOT NULL DEFAULT 0,
  is_definition INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS symbols (
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

CREATE TABLE IF NOT EXISTS calls (
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

CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  repo TEXT NOT NULL,
  module_path TEXT NOT NULL,
  imported_names_json TEXT,
  import_line INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_repo_path ON files(repo, path);
CREATE INDEX IF NOT EXISTS idx_files_repo_hash ON files(repo, content_hash);
CREATE INDEX IF NOT EXISTS idx_scip_documents_repo_path ON scip_documents(repo, relative_path);
CREATE INDEX IF NOT EXISTS idx_scip_symbols_repo_symbol ON scip_symbols(repo, scip_symbol);
CREATE INDEX IF NOT EXISTS idx_scip_occurrences_symbol ON scip_occurrences(repo, scip_symbol);
CREATE INDEX IF NOT EXISTS idx_scip_relationships_source ON scip_relationships(repo, source_symbol);
CREATE INDEX IF NOT EXISTS idx_scip_relationships_target ON scip_relationships(repo, target_symbol);
CREATE INDEX IF NOT EXISTS idx_symbols_repo_name ON symbols(repo, name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(repo, qualified_name);
CREATE INDEX IF NOT EXISTS idx_calls_repo_callee ON calls(repo, callee_name);
CREATE INDEX IF NOT EXISTS idx_calls_repo_caller ON calls(repo, caller_symbol_id);
CREATE INDEX IF NOT EXISTS idx_imports_repo_module ON imports(repo, module_path);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def start_index_run(conn: sqlite3.Connection, repo: str | None, mode: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO index_runs(repo, mode, status, started_at)
        VALUES (?, ?, 'running', ?)
        """,
        (repo, mode, utc_now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_index_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    files_seen: int = 0,
    files_indexed: int = 0,
    symbols_count: int = 0,
    calls_count: int = 0,
    imports_count: int = 0,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE index_runs
        SET status = ?, finished_at = ?, files_seen = ?, files_indexed = ?,
            symbols_count = ?, calls_count = ?, imports_count = ?, error = ?
        WHERE id = ?
        """,
        (
            status,
            utc_now(),
            files_seen,
            files_indexed,
            symbols_count,
            calls_count,
            imports_count,
            error,
            run_id,
        ),
    )
    conn.commit()
```

- [ ] **Step 4: Initialize DB at app startup**

Modify `ast-service/main.py`:

```python
from fastapi import FastAPI

from db import connect_db, init_db
from models import HealthResponse


app = FastAPI(title="repo-bot ast-service")


@app.on_event("startup")
def startup() -> None:
    conn = connect_db()
    init_db(conn)
    conn.close()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()
```

- [ ] **Step 5: Run tests**

Run:

```bash
cd ast-service && pytest tests/test_db.py tests/test_api.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add ast-service
git commit -m "feat(ast-service): add SQLite schema"
```

## Task 3: Add Repository Scanner

**Files:**
- Create: `ast-service/repository_scanner.py`
- Create: `ast-service/tests/test_repository_scanner.py`

- [ ] **Step 1: Write scanner tests**

Create `ast-service/tests/test_repository_scanner.py`:

```python
from pathlib import Path

from repository_scanner import discover_source_files, language_for_path


def test_language_for_path_detects_initial_languages():
    assert language_for_path(Path("a.py")) == "python"
    assert language_for_path(Path("a.ts")) == "typescript"
    assert language_for_path(Path("a.tsx")) == "typescript"
    assert language_for_path(Path("a.js")) == "javascript"
    assert language_for_path(Path("a.jsx")) == "javascript"
    assert language_for_path(Path("README.md")) is None


def test_discover_source_files_skips_ignored_dirs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def foo(): pass\n")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "bad.py").write_text("def bad(): pass\n")

    files = list(discover_source_files(tmp_path))

    assert len(files) == 1
    item = files[0]
    assert item.repo == "repo"
    assert item.rel_path == "repo/app.py"
    assert item.language == "python"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd ast-service && pytest tests/test_repository_scanner.py -v
```

Expected: fail because scanner does not exist.

- [ ] **Step 3: Implement scanner**

Create `ast-service/repository_scanner.py`:

```python
import os
from dataclasses import dataclass
from pathlib import Path


SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "target",
    "dist",
    "build",
    ".next",
    "vendor",
    "vendor_",
}

EXT_TO_LANGUAGE = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
}


@dataclass(frozen=True)
class SourceFile:
    repo: str
    abs_path: Path
    rel_path: str
    language: str
    size: int
    mtime: float


def language_for_path(path: Path) -> str | None:
    return EXT_TO_LANGUAGE.get(path.suffix)


def discover_source_files(repos_root: str | Path) -> list[SourceFile]:
    root = Path(repos_root)
    discovered: list[SourceFile] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        current_path = Path(current)
        for filename in files:
            abs_path = current_path / filename
            language = language_for_path(abs_path)
            if language is None:
                continue
            rel = abs_path.relative_to(root)
            parts = rel.parts
            if not parts:
                continue
            stat = abs_path.stat()
            discovered.append(
                SourceFile(
                    repo=parts[0],
                    abs_path=abs_path,
                    rel_path="/".join(parts),
                    language=language,
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                )
            )
    return discovered
```

- [ ] **Step 4: Run scanner tests**

Run:

```bash
cd ast-service && pytest tests/test_repository_scanner.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ast-service/repository_scanner.py ast-service/tests/test_repository_scanner.py
git commit -m "feat(ast-service): discover source files"
```

## Task 4: Add ast-grep Runner and Rules

**Files:**
- Create: `ast-service/astgrep_runner.py`
- Create: `ast-service/rules/python-symbols.yml`
- Create: `ast-service/rules/python-calls.yml`
- Create: `ast-service/rules/python-imports.yml`
- Create: `ast-service/rules/ts-symbols.yml`
- Create: `ast-service/rules/ts-calls.yml`
- Create: `ast-service/rules/ts-imports.yml`
- Create: `ast-service/tests/fixtures/sample-python/app.py`
- Create: `ast-service/tests/fixtures/sample-ts/app.ts`
- Create: `ast-service/tests/test_astgrep_runner.py`

- [ ] **Step 1: Add fixtures**

Create `ast-service/tests/fixtures/sample-python/app.py`:

```python
import os
from fastapi import APIRouter


class UserService:
    def get_user(self, user_id: str):
        return load_user(user_id)


def load_user(user_id: str):
    return os.getenv(user_id)


def handler():
    service = UserService()
    return service.get_user("42")
```

Create `ast-service/tests/fixtures/sample-ts/app.ts`:

```typescript
import { readFileSync } from "fs";

export class UserService {
  getUser(id: string) {
    return loadUser(id);
  }
}

export function loadUser(id: string) {
  return readFileSync(id, "utf8");
}

export function handler() {
  const service = new UserService();
  return service.getUser("42");
}
```

- [ ] **Step 2: Add initial YAML rules**

Create `ast-service/rules/python-symbols.yml`:

```yaml
id: python-symbols
language: Python
rule:
  any:
    - pattern: |
        def $NAME($$$PARAMS):
            $$$BODY
    - pattern: |
        class $NAME:
            $$$BODY
```

Create `ast-service/rules/python-calls.yml`:

```yaml
id: python-calls
language: Python
rule:
  pattern: $CALLEE($$$ARGS)
```

Create `ast-service/rules/python-imports.yml`:

```yaml
id: python-imports
language: Python
rule:
  any:
    - pattern: import $MODULE
    - pattern: from $MODULE import $$$NAMES
```

Create `ast-service/rules/ts-symbols.yml`:

```yaml
id: ts-symbols
language: TypeScript
rule:
  any:
    - pattern: |
        function $NAME($$$PARAMS) {
          $$$BODY
        }
    - pattern: |
        export function $NAME($$$PARAMS) {
          $$$BODY
        }
    - pattern: |
        class $NAME {
          $$$BODY
        }
    - pattern: |
        export class $NAME {
          $$$BODY
        }
```

Create `ast-service/rules/ts-calls.yml`:

```yaml
id: ts-calls
language: TypeScript
rule:
  pattern: $CALLEE($$$ARGS)
```

Create `ast-service/rules/ts-imports.yml`:

```yaml
id: ts-imports
language: TypeScript
rule:
  pattern: import $$$IMPORTS from $MODULE
```

- [ ] **Step 3: Write runner tests**

Create `ast-service/tests/test_astgrep_runner.py`:

```python
from pathlib import Path

from astgrep_runner import run_rule_file


def test_python_symbol_rule_finds_fixture_symbols():
    source = Path("tests/fixtures/sample-python/app.py")
    rule = Path("rules/python-symbols.yml")

    matches = run_rule_file(source, rule)
    texts = [match.text for match in matches]

    assert any("class UserService" in text for text in texts)
    assert any("def load_user" in text for text in texts)
    assert any("def handler" in text for text in texts)


def test_python_call_rule_finds_fixture_calls():
    matches = run_rule_file(
        Path("tests/fixtures/sample-python/app.py"),
        Path("rules/python-calls.yml"),
    )

    assert any("load_user(user_id)" in match.text for match in matches)
    assert any('service.get_user("42")' in match.text for match in matches)
```

- [ ] **Step 4: Run tests to verify failure**

Run:

```bash
cd ast-service && pytest tests/test_astgrep_runner.py -v
```

Expected: fail because runner does not exist.

- [ ] **Step 5: Implement ast-grep runner**

Create `ast-service/astgrep_runner.py`:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from ast_grep_py import SgRoot


@dataclass(frozen=True)
class AstGrepMatch:
    text: str
    start_line: int
    end_line: int
    captures: dict[str, str]


LANGUAGE_MAP = {
    "Python": "python",
    "TypeScript": "typescript",
    "JavaScript": "javascript",
    "python": "python",
    "typescript": "typescript",
    "javascript": "javascript",
}


def load_rule(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _line_range(source: str, matched_text: str) -> tuple[int, int]:
    start_idx = source.find(matched_text)
    if start_idx < 0:
        return 1, 1
    start_line = source[:start_idx].count("\n") + 1
    end_line = start_line + matched_text.count("\n")
    return start_line, end_line


def _captures(node: Any) -> dict[str, str]:
    get_multiple_matches = getattr(node, "get_multiple_matches", None)
    if get_multiple_matches is None:
        return {}

    captures: dict[str, str] = {}
    # ast-grep-py capture APIs differ across versions. Keep this wrapper
    # defensive and isolated so callers never depend on raw ast-grep nodes.
    for name in ("NAME", "CALLEE", "MODULE", "NAMES", "IMPORTS"):
        try:
            values = get_multiple_matches(name)
        except Exception:
            values = []
        if values:
            captures[name] = ", ".join(v.text() for v in values)
    return captures


def run_rule_file(source_path: Path, rule_path: Path) -> list[AstGrepMatch]:
    rule_doc = load_rule(rule_path)
    language = LANGUAGE_MAP[rule_doc["language"]]
    source = source_path.read_text(encoding="utf-8", errors="replace")
    root = SgRoot(source, language)

    rule = rule_doc["rule"]
    nodes = root.root().find_all(rule)
    matches: list[AstGrepMatch] = []
    for node in nodes:
        text = node.text()
        start_line, end_line = _line_range(source, text)
        matches.append(
            AstGrepMatch(
                text=text,
                start_line=start_line,
                end_line=end_line,
                captures=_captures(node),
            )
        )
    return matches
```

- [ ] **Step 6: Run runner tests**

Run:

```bash
cd ast-service && pytest tests/test_astgrep_runner.py -v
```

Expected: tests pass. If ast-grep-py rejects YAML rule dictionaries directly, keep `astgrep_runner.py` isolated and adapt only this file to the installed ast-grep-py API or invoke `ast-grep scan --json` as a fallback.

- [ ] **Step 7: Commit**

```bash
git add ast-service
git commit -m "feat(ast-service): run ast-grep YAML rules"
```

## Task 5: Normalize Matches into Structural Records

**Files:**
- Create: `ast-service/normalizer.py`
- Create: `ast-service/tests/test_normalizer.py`

- [ ] **Step 1: Write normalizer tests**

Create `ast-service/tests/test_normalizer.py`:

```python
from astgrep_runner import AstGrepMatch
from normalizer import normalize_calls, normalize_imports, normalize_symbols


def test_normalize_python_symbols_extracts_basic_names():
    matches = [
        AstGrepMatch("def load_user(user_id: str):\n    pass", 10, 11, {}),
        AstGrepMatch("class UserService:\n    pass", 1, 2, {}),
    ]

    symbols = normalize_symbols("repo", "repo/app.py", "python", matches)

    assert symbols[0].name == "load_user"
    assert symbols[0].kind == "function"
    assert symbols[1].name == "UserService"
    assert symbols[1].kind == "class"


def test_normalize_calls_ignores_definitions():
    matches = [
        AstGrepMatch("load_user(user_id)", 5, 5, {}),
        AstGrepMatch("def load_user(user_id):\n    pass", 10, 11, {}),
    ]

    calls = normalize_calls("repo", "repo/app.py", matches)

    assert [call.callee_name for call in calls] == ["load_user"]


def test_normalize_imports_extracts_modules():
    matches = [
        AstGrepMatch("import os", 1, 1, {}),
        AstGrepMatch("from fastapi import APIRouter", 2, 2, {}),
    ]

    imports = normalize_imports("repo", "repo/app.py", "python", matches)

    assert [item.module_path for item in imports] == ["os", "fastapi"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd ast-service && pytest tests/test_normalizer.py -v
```

Expected: fail because normalizer does not exist.

- [ ] **Step 3: Implement normalizer**

Create `ast-service/normalizer.py`:

```python
import json
import re
from dataclasses import dataclass

from astgrep_runner import AstGrepMatch


@dataclass(frozen=True)
class SymbolRecord:
    repo: str
    path: str
    name: str
    qualified_name: str | None
    kind: str
    start_line: int
    end_line: int
    signature: str | None = None
    parent_name: str | None = None


@dataclass(frozen=True)
class CallRecord:
    repo: str
    path: str
    callee_name: str
    call_line: int


@dataclass(frozen=True)
class ImportRecord:
    repo: str
    path: str
    module_path: str
    imported_names_json: str | None
    import_line: int


DEF_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?def\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)", re.M)
PY_CLASS_RE = re.compile(r"^\s*class\s+([A-Za-z_][\w]*)", re.M)
TS_FUNC_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)", re.M)
TS_CLASS_RE = re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)", re.M)
CALL_RE = re.compile(r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\s*\(")


def normalize_symbols(
    repo: str,
    path: str,
    language: str,
    matches: list[AstGrepMatch],
) -> list[SymbolRecord]:
    records: list[SymbolRecord] = []
    for match in matches:
        text = match.text
        if language == "python":
            func = DEF_RE.search(text)
            klass = PY_CLASS_RE.search(text)
        else:
            func = TS_FUNC_RE.search(text)
            klass = TS_CLASS_RE.search(text)

        if func:
            name = func.group(1)
            signature = f"({func.group(2)})"
            records.append(
                SymbolRecord(repo, path, name, name, "function", match.start_line, match.end_line, signature)
            )
        elif klass:
            name = klass.group(1)
            records.append(
                SymbolRecord(repo, path, name, name, "class", match.start_line, match.end_line)
            )
    return records


def normalize_calls(repo: str, path: str, matches: list[AstGrepMatch]) -> list[CallRecord]:
    records: list[CallRecord] = []
    for match in matches:
        stripped = match.text.lstrip()
        if stripped.startswith(("def ", "class ", "function ", "export function ", "export class ")):
            continue
        found = CALL_RE.search(match.text)
        if found:
            records.append(CallRecord(repo, path, found.group(1), match.start_line))
    return records


def normalize_imports(
    repo: str,
    path: str,
    language: str,
    matches: list[AstGrepMatch],
) -> list[ImportRecord]:
    records: list[ImportRecord] = []
    for match in matches:
        text = match.text.strip()
        module = None
        imported: list[str] = []
        if language == "python":
            if text.startswith("import "):
                module = text.removeprefix("import ").split()[0]
            elif text.startswith("from "):
                before, _, after = text.partition(" import ")
                module = before.removeprefix("from ").strip()
                imported = [name.strip() for name in after.split(",") if name.strip()]
        else:
            parts = text.split(" from ")
            if len(parts) == 2:
                module = parts[1].strip().strip("\"'")
                imported = [parts[0].removeprefix("import").strip()]
        if module:
            records.append(
                ImportRecord(
                    repo=repo,
                    path=path,
                    module_path=module,
                    imported_names_json=json.dumps(imported) if imported else None,
                    import_line=match.start_line,
                )
            )
    return records
```

- [ ] **Step 4: Run normalizer tests**

Run:

```bash
cd ast-service && pytest tests/test_normalizer.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ast-service/normalizer.py ast-service/tests/test_normalizer.py
git commit -m "feat(ast-service): normalize ast-grep matches"
```

## Task 6: Add SCIP Compatibility Layer

**Files:**
- Create: `ast-service/scip.py`
- Create: `ast-service/tests/test_scip.py`
- Modify: `ast-service/db.py`

- [ ] **Step 1: Write SCIP mapping tests**

Create `ast-service/tests/test_scip.py`:

```python
from normalizer import SymbolRecord
from scip import (
    DEFINITION_ROLE,
    Position,
    make_document_uri,
    make_occurrence_range,
    make_scip_symbol,
    symbol_to_scip_rows,
)


def test_make_scip_symbol_is_stable_and_repo_local():
    symbol = make_scip_symbol(
        repo="repo-bot",
        path="repo-bot/chat-ui/app.py",
        descriptor_chain=["search_qdrant()"],
    )

    assert symbol == "local repo-bot repo-bot/chat-ui/app.py / search_qdrant()."


def test_make_occurrence_range_is_zero_based():
    source = "def foo():\n    return 1\n"
    start = source.index("foo")
    end = start + len("foo")

    assert make_occurrence_range(source, start, end) == Position(0, 4, 0, 7)


def test_symbol_to_scip_rows_maps_definition_role():
    symbol = SymbolRecord(
        repo="repo-bot",
        path="repo-bot/app.py",
        name="foo",
        qualified_name="foo",
        kind="function",
        start_line=1,
        end_line=2,
    )

    scip_symbol, occurrence = symbol_to_scip_rows(
        symbol=symbol,
        document_id=1,
        source_text="def foo():\n    pass\n",
    )

    assert scip_symbol.scip_symbol.endswith("foo().")
    assert occurrence.symbol_roles & DEFINITION_ROLE
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd ast-service && pytest tests/test_scip.py -v
```

Expected: fail because `scip.py` does not exist.

- [ ] **Step 3: Implement SCIP helpers**

Create `ast-service/scip.py`:

```python
from dataclasses import dataclass

from normalizer import SymbolRecord


# SCIP SymbolRole bit values. Keep these isolated so they can be checked
# against scip.proto when protobuf export is implemented.
DEFINITION_ROLE = 1
IMPORT_ROLE = 2
READ_ACCESS_ROLE = 4
WRITE_ACCESS_ROLE = 8


@dataclass(frozen=True)
class Position:
    start_line: int
    start_character: int
    end_line: int
    end_character: int


@dataclass(frozen=True)
class ScipSymbolRow:
    repo: str
    scip_symbol: str
    display_name: str
    kind: str
    documentation: str | None = None
    signature_documentation: str | None = None
    enclosing_symbol: str | None = None


@dataclass(frozen=True)
class ScipOccurrenceRow:
    document_id: int
    repo: str
    scip_symbol: str
    range_start_line: int
    range_start_character: int
    range_end_line: int
    range_end_character: int
    symbol_roles: int
    syntax_kind: str | None = None
    enclosing_range_json: str | None = None


def make_document_uri(repo: str, path: str) -> str:
    return path if path.startswith(f"{repo}/") else f"{repo}/{path}"


def descriptor_for_symbol(symbol: SymbolRecord) -> str:
    suffix = "#" if symbol.kind == "class" else "()"
    return f"{symbol.qualified_name or symbol.name}{suffix}"


def make_scip_symbol(repo: str, path: str, descriptor_chain: list[str]) -> str:
    descriptors = " ".join(descriptor_chain)
    return f"local {repo} {path} / {descriptors}."


def make_occurrence_range(source_text: str, start_offset: int, end_offset: int) -> Position:
    before_start = source_text[:start_offset]
    before_end = source_text[:end_offset]
    start_line = before_start.count("\n")
    end_line = before_end.count("\n")
    start_line_start = before_start.rfind("\n") + 1
    end_line_start = before_end.rfind("\n") + 1
    return Position(
        start_line=start_line,
        start_character=start_offset - start_line_start,
        end_line=end_line,
        end_character=end_offset - end_line_start,
    )


def symbol_to_scip_rows(
    symbol: SymbolRecord,
    document_id: int,
    source_text: str,
) -> tuple[ScipSymbolRow, ScipOccurrenceRow]:
    descriptor = descriptor_for_symbol(symbol)
    scip_symbol = make_scip_symbol(symbol.repo, symbol.path, [descriptor])
    name_offset = source_text.find(symbol.name)
    if name_offset < 0:
        name_offset = 0
    position = make_occurrence_range(source_text, name_offset, name_offset + len(symbol.name))
    return (
        ScipSymbolRow(
            repo=symbol.repo,
            scip_symbol=scip_symbol,
            display_name=symbol.name,
            kind=symbol.kind,
            signature_documentation=symbol.signature,
            enclosing_symbol=symbol.parent_name,
        ),
        ScipOccurrenceRow(
            document_id=document_id,
            repo=symbol.repo,
            scip_symbol=scip_symbol,
            range_start_line=position.start_line,
            range_start_character=position.start_character,
            range_end_line=position.end_line,
            range_end_character=position.end_character,
            symbol_roles=DEFINITION_ROLE,
            syntax_kind=symbol.kind,
        ),
    )
```

- [ ] **Step 4: Add DB helpers for SCIP rows**

Extend `ast-service/db.py` with:

```python
from scip import ScipOccurrenceRow, ScipSymbolRow


def upsert_scip_document(
    conn: sqlite3.Connection,
    file_id: int,
    repo: str,
    relative_path: str,
    language: str,
    position_encoding: str = "UTF8",
) -> int:
    conn.execute(
        """
        INSERT INTO scip_documents(file_id, repo, relative_path, language, position_encoding)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(file_id) DO UPDATE SET
          repo = excluded.repo,
          relative_path = excluded.relative_path,
          language = excluded.language,
          position_encoding = excluded.position_encoding
        """,
        (file_id, repo, relative_path, language, position_encoding),
    )
    row = conn.execute(
        "SELECT id FROM scip_documents WHERE file_id = ?",
        (file_id,),
    ).fetchone()
    return int(row["id"])


def replace_scip_records(
    conn: sqlite3.Connection,
    document_id: int,
    repo: str,
    symbols: list[ScipSymbolRow],
    occurrences: list[ScipOccurrenceRow],
) -> tuple[int, int]:
    conn.execute("DELETE FROM scip_occurrences WHERE document_id = ?", (document_id,))
    for symbol in symbols:
        conn.execute(
            """
            INSERT INTO scip_symbols(
              repo, scip_symbol, display_name, kind, documentation,
              signature_documentation, enclosing_symbol
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo, scip_symbol) DO UPDATE SET
              display_name = excluded.display_name,
              kind = excluded.kind,
              documentation = excluded.documentation,
              signature_documentation = excluded.signature_documentation,
              enclosing_symbol = excluded.enclosing_symbol
            """,
            (
                symbol.repo,
                symbol.scip_symbol,
                symbol.display_name,
                symbol.kind,
                symbol.documentation,
                symbol.signature_documentation,
                symbol.enclosing_symbol,
            ),
        )
    for occurrence in occurrences:
        conn.execute(
            """
            INSERT INTO scip_occurrences(
              document_id, repo, scip_symbol, range_start_line,
              range_start_character, range_end_line, range_end_character,
              symbol_roles, syntax_kind, enclosing_range_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                occurrence.document_id,
                occurrence.repo,
                occurrence.scip_symbol,
                occurrence.range_start_line,
                occurrence.range_start_character,
                occurrence.range_end_line,
                occurrence.range_end_character,
                occurrence.symbol_roles,
                occurrence.syntax_kind,
                occurrence.enclosing_range_json,
            ),
        )
    return len(symbols), len(occurrences)
```

- [ ] **Step 5: Run SCIP tests**

Run:

```bash
cd ast-service && pytest tests/test_scip.py tests/test_db.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add ast-service/scip.py ast-service/tests/test_scip.py ast-service/db.py
git commit -m "feat(ast-service): add SCIP compatibility layer"
```

## Task 7: Implement Offline Indexer

**Files:**
- Create: `ast-service/indexer.py`
- Modify: `ast-service/db.py`
- Create: `ast-service/tests/test_indexer.py`

- [ ] **Step 1: Write indexer integration test**

Create `ast-service/tests/test_indexer.py`:

```python
import os
from pathlib import Path

from db import connect_db, init_db
from indexer import run_index


def copy_fixture(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def test_full_index_writes_symbols_calls_and_imports(tmp_path, monkeypatch):
    repo_root = tmp_path / "repos"
    copy_fixture(
        Path("tests/fixtures/sample-python/app.py"),
        repo_root / "sample-python" / "app.py",
    )
    db_path = tmp_path / "ast.sqlite"
    monkeypatch.setenv("AST_DB_PATH", str(db_path))
    monkeypatch.setenv("REPOS_ROOT", str(repo_root))

    conn = connect_db(str(db_path))
    init_db(conn)
    conn.close()

    result = run_index(mode="full", repo=None)

    conn = connect_db(str(db_path))
    assert result.files_indexed == 1
    assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] >= 3
    assert conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0] >= 2
    assert conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0] >= 2
    assert conn.execute("SELECT COUNT(*) FROM scip_documents").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM scip_symbols").fetchone()[0] >= 3
    assert conn.execute("SELECT COUNT(*) FROM scip_occurrences").fetchone()[0] >= 3
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
cd ast-service && pytest tests/test_indexer.py -v
```

Expected: fail because indexer does not exist.

- [ ] **Step 3: Add DB write helpers**

Extend `ast-service/db.py` with:

```python
from normalizer import CallRecord, ImportRecord, SymbolRecord


def upsert_file(
    conn: sqlite3.Connection,
    repo: str,
    path: str,
    language: str,
    size: int,
    mtime: float,
    content_hash: str,
) -> int:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO files(repo, path, language, size, mtime, content_hash, indexed_at, deleted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(repo, path) DO UPDATE SET
          language = excluded.language,
          size = excluded.size,
          mtime = excluded.mtime,
          content_hash = excluded.content_hash,
          indexed_at = excluded.indexed_at,
          deleted_at = NULL
        """,
        (repo, path, language, size, mtime, content_hash, now),
    )
    row = conn.execute(
        "SELECT id FROM files WHERE repo = ? AND path = ?",
        (repo, path),
    ).fetchone()
    return int(row["id"])


def replace_file_records(
    conn: sqlite3.Connection,
    file_id: int,
    repo: str,
    symbols: Iterable[SymbolRecord],
    calls: Iterable[CallRecord],
    imports: Iterable[ImportRecord],
) -> tuple[int, int, int]:
    conn.execute("DELETE FROM calls WHERE file_id = ?", (file_id,))
    conn.execute("DELETE FROM imports WHERE file_id = ?", (file_id,))
    conn.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))

    symbol_count = 0
    for symbol in symbols:
        conn.execute(
            """
            INSERT INTO symbols(
              file_id, repo, name, qualified_name, kind, start_line,
              end_line, signature, parent_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                repo,
                symbol.name,
                symbol.qualified_name,
                symbol.kind,
                symbol.start_line,
                symbol.end_line,
                symbol.signature,
                symbol.parent_name,
            ),
        )
        symbol_count += 1

    call_count = 0
    for call in calls:
        conn.execute(
            """
            INSERT INTO calls(file_id, repo, callee_name, call_line)
            VALUES (?, ?, ?, ?)
            """,
            (file_id, repo, call.callee_name, call.call_line),
        )
        call_count += 1

    import_count = 0
    for item in imports:
        conn.execute(
            """
            INSERT INTO imports(file_id, repo, module_path, imported_names_json, import_line)
            VALUES (?, ?, ?, ?, ?)
            """,
            (file_id, repo, item.module_path, item.imported_names_json, item.import_line),
        )
        import_count += 1

    return symbol_count, call_count, import_count
```

- [ ] **Step 4: Implement indexer**

Create `ast-service/indexer.py`:

```python
import argparse
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from astgrep_runner import run_rule_file
from db import (
    connect_db,
    finish_index_run,
    init_db,
    replace_file_records,
    replace_scip_records,
    start_index_run,
    transaction,
    upsert_file,
    upsert_scip_document,
)
from normalizer import normalize_calls, normalize_imports, normalize_symbols
from repository_scanner import SourceFile, discover_source_files
from scip import symbol_to_scip_rows


RULES_BY_LANGUAGE = {
    "python": {
        "symbols": Path("rules/python-symbols.yml"),
        "calls": Path("rules/python-calls.yml"),
        "imports": Path("rules/python-imports.yml"),
    },
    "typescript": {
        "symbols": Path("rules/ts-symbols.yml"),
        "calls": Path("rules/ts-calls.yml"),
        "imports": Path("rules/ts-imports.yml"),
    },
    "javascript": {
        "symbols": Path("rules/ts-symbols.yml"),
        "calls": Path("rules/ts-calls.yml"),
        "imports": Path("rules/ts-imports.yml"),
    },
}


@dataclass(frozen=True)
class IndexResult:
    run_id: int
    files_seen: int
    files_indexed: int
    symbols_count: int
    calls_count: int
    imports_count: int


def repos_root() -> Path:
    return Path(os.environ.get("REPOS_ROOT", "/repos"))


def content_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def should_index(conn, source: SourceFile, mode: str, digest: str) -> bool:
    if mode == "full":
        return True
    row = conn.execute(
        "SELECT size, mtime, content_hash, deleted_at FROM files WHERE repo = ? AND path = ?",
        (source.repo, source.rel_path),
    ).fetchone()
    if row is None:
        return True
    return (
        row["deleted_at"] is not None
        or row["size"] != source.size
        or row["mtime"] != source.mtime
        or row["content_hash"] != digest
    )


def index_one_file(conn, source: SourceFile) -> tuple[int, int, int]:
    digest = content_hash(source.abs_path)
    file_id = upsert_file(
        conn,
        source.repo,
        source.rel_path,
        source.language,
        source.size,
        source.mtime,
        digest,
    )
    rules = RULES_BY_LANGUAGE[source.language]
    symbol_matches = run_rule_file(source.abs_path, rules["symbols"])
    call_matches = run_rule_file(source.abs_path, rules["calls"])
    import_matches = run_rule_file(source.abs_path, rules["imports"])
    symbols = normalize_symbols(source.repo, source.rel_path, source.language, symbol_matches)
    calls = normalize_calls(source.repo, source.rel_path, call_matches)
    imports = normalize_imports(source.repo, source.rel_path, source.language, import_matches)
    document_id = upsert_scip_document(conn, file_id, source.repo, source.rel_path, source.language)
    source_text = source.abs_path.read_text(encoding="utf-8", errors="replace")
    scip_symbols = []
    scip_occurrences = []
    for symbol in symbols:
        scip_symbol, scip_occurrence = symbol_to_scip_rows(symbol, document_id, source_text)
        scip_symbols.append(scip_symbol)
        scip_occurrences.append(scip_occurrence)
    replace_scip_records(conn, document_id, source.repo, scip_symbols, scip_occurrences)
    return replace_file_records(conn, file_id, source.repo, symbols, calls, imports)


def run_index(mode: str = "incremental", repo: str | None = None) -> IndexResult:
    conn = connect_db()
    init_db(conn)
    run_id = start_index_run(conn, repo, mode)
    files_seen = 0
    files_indexed = 0
    symbols_count = 0
    calls_count = 0
    imports_count = 0
    try:
        files = discover_source_files(repos_root())
        for source in files:
            if repo and source.repo != repo:
                continue
            files_seen += 1
            digest = content_hash(source.abs_path)
            if not should_index(conn, source, mode, digest):
                continue
            with transaction(conn):
                s_count, c_count, i_count = index_one_file(conn, source)
            files_indexed += 1
            symbols_count += s_count
            calls_count += c_count
            imports_count += i_count
        finish_index_run(
            conn,
            run_id,
            "ok",
            files_seen,
            files_indexed,
            symbols_count,
            calls_count,
            imports_count,
        )
    except Exception as exc:
        finish_index_run(conn, run_id, "error", files_seen, files_indexed, symbols_count, calls_count, imports_count, str(exc))
        raise
    finally:
        conn.close()
    return IndexResult(run_id, files_seen, files_indexed, symbols_count, calls_count, imports_count)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["full", "incremental"], default="incremental")
    parser.add_argument("--repo")
    args = parser.parse_args()
    result = run_index(mode=args.mode, repo=args.repo)
    print(result)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run indexer tests**

Run:

```bash
cd ast-service && pytest tests/test_indexer.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add ast-service
git commit -m "feat(ast-service): add offline indexer"
```

## Task 8: Add Query APIs

**Files:**
- Modify: `ast-service/db.py`
- Modify: `ast-service/models.py`
- Modify: `ast-service/main.py`
- Modify: `ast-service/tests/test_api.py`

- [ ] **Step 1: Add API tests for empty query responses**

Append to `ast-service/tests/test_api.py`:

```python
def test_symbols_endpoint_returns_list():
    client = TestClient(app)
    resp = client.get("/symbols?repo=missing&limit=10")
    assert resp.status_code == 200
    assert resp.json() == {"symbols": []}


def test_calls_endpoint_returns_list():
    client = TestClient(app)
    resp = client.get("/calls?repo=missing&callee_name=foo&limit=10")
    assert resp.status_code == 200
    assert resp.json() == {"calls": []}


def test_imports_endpoint_returns_list():
    client = TestClient(app)
    resp = client.get("/imports?repo=missing&module=fastapi&limit=10")
    assert resp.status_code == 200
    assert resp.json() == {"imports": []}


def test_scip_debug_endpoints_return_lists():
    client = TestClient(app)
    assert client.get("/scip/documents?repo=missing").json() == {"documents": []}
    assert client.get("/scip/symbols?repo=missing").json() == {"symbols": []}
    assert client.get("/scip/occurrences?repo=missing").json() == {"occurrences": []}
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd ast-service && pytest tests/test_api.py -v
```

Expected: query endpoint tests fail with 404.

- [ ] **Step 3: Add response models**

Extend `ast-service/models.py`:

```python
from typing import Any


class SymbolItem(BaseModel):
    id: int
    repo: str
    path: str
    name: str
    qualified_name: str | None = None
    kind: str
    start_line: int
    end_line: int | None = None
    signature: str | None = None
    parent_name: str | None = None


class SymbolsResponse(BaseModel):
    symbols: list[SymbolItem]


class CallItem(BaseModel):
    id: int
    repo: str
    path: str
    callee_name: str
    call_line: int
    caller_symbol_id: int | None = None
    callee_symbol_id: int | None = None


class CallsResponse(BaseModel):
    calls: list[CallItem]


class ImportItem(BaseModel):
    id: int
    repo: str
    path: str
    module_path: str
    imported_names_json: str | None = None
    import_line: int


class ImportsResponse(BaseModel):
    imports: list[ImportItem]


class StatusResponse(BaseModel):
    latest_runs: list[dict[str, Any]]


class ScipDocumentsResponse(BaseModel):
    documents: list[dict[str, Any]]


class ScipSymbolsResponse(BaseModel):
    symbols: list[dict[str, Any]]


class ScipOccurrencesResponse(BaseModel):
    occurrences: list[dict[str, Any]]
```

- [ ] **Step 4: Add DB query helpers**

Extend `ast-service/db.py`:

```python
def query_symbols(
    conn: sqlite3.Connection,
    repo: str | None,
    name: str | None,
    kind: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    sql = """
    SELECT symbols.*, files.path
    FROM symbols
    JOIN files ON files.id = symbols.file_id
    WHERE files.deleted_at IS NULL
    """
    params: list[object] = []
    if repo:
        sql += " AND symbols.repo = ?"
        params.append(repo)
    if name:
        sql += " AND symbols.name = ?"
        params.append(name)
    if kind:
        sql += " AND symbols.kind = ?"
        params.append(kind)
    sql += " ORDER BY symbols.repo, files.path, symbols.start_line LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def query_calls(
    conn: sqlite3.Connection,
    repo: str | None,
    caller_name: str | None,
    callee_name: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    sql = """
    SELECT calls.*, files.path
    FROM calls
    JOIN files ON files.id = calls.file_id
    LEFT JOIN symbols caller ON caller.id = calls.caller_symbol_id
    WHERE files.deleted_at IS NULL
    """
    params: list[object] = []
    if repo:
        sql += " AND calls.repo = ?"
        params.append(repo)
    if caller_name:
        sql += " AND caller.name = ?"
        params.append(caller_name)
    if callee_name:
        sql += " AND calls.callee_name = ?"
        params.append(callee_name)
    sql += " ORDER BY calls.repo, files.path, calls.call_line LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def query_imports(
    conn: sqlite3.Connection,
    repo: str | None,
    module: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    sql = """
    SELECT imports.*, files.path
    FROM imports
    JOIN files ON files.id = imports.file_id
    WHERE files.deleted_at IS NULL
    """
    params: list[object] = []
    if repo:
        sql += " AND imports.repo = ?"
        params.append(repo)
    if module:
        sql += " AND imports.module_path LIKE ?"
        params.append(f"%{module}%")
    sql += " ORDER BY imports.repo, files.path, imports.import_line LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def query_scip_documents(conn: sqlite3.Connection, repo: str | None, limit: int) -> list[sqlite3.Row]:
    sql = "SELECT * FROM scip_documents WHERE 1 = 1"
    params: list[object] = []
    if repo:
        sql += " AND repo = ?"
        params.append(repo)
    sql += " ORDER BY repo, relative_path LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def query_scip_symbols(
    conn: sqlite3.Connection,
    repo: str | None,
    prefix: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM scip_symbols WHERE 1 = 1"
    params: list[object] = []
    if repo:
        sql += " AND repo = ?"
        params.append(repo)
    if prefix:
        sql += " AND scip_symbol LIKE ?"
        params.append(f"{prefix}%")
    sql += " ORDER BY repo, scip_symbol LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def query_scip_occurrences(
    conn: sqlite3.Connection,
    repo: str | None,
    symbol: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM scip_occurrences WHERE 1 = 1"
    params: list[object] = []
    if repo:
        sql += " AND repo = ?"
        params.append(repo)
    if symbol:
        sql += " AND scip_symbol = ?"
        params.append(symbol)
    sql += " ORDER BY repo, document_id, range_start_line, range_start_character LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()
```

- [ ] **Step 5: Add FastAPI routes**

Modify `ast-service/main.py`:

```python
from fastapi import FastAPI, Query

from db import (
    connect_db,
    init_db,
    query_calls,
    query_imports,
    query_scip_documents,
    query_scip_occurrences,
    query_scip_symbols,
    query_symbols,
)
from models import (
    CallsResponse,
    HealthResponse,
    ImportsResponse,
    ScipDocumentsResponse,
    ScipOccurrencesResponse,
    ScipSymbolsResponse,
    SymbolsResponse,
)


app = FastAPI(title="repo-bot ast-service")


@app.on_event("startup")
def startup() -> None:
    conn = connect_db()
    init_db(conn)
    conn.close()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.get("/symbols", response_model=SymbolsResponse)
def symbols(
    repo: str | None = None,
    name: str | None = None,
    kind: str | None = None,
    limit: int = Query(default=20, ge=1, le=200),
) -> SymbolsResponse:
    conn = connect_db()
    try:
        rows = query_symbols(conn, repo, name, kind, limit)
        return SymbolsResponse(symbols=[dict(row) for row in rows])
    finally:
        conn.close()


@app.get("/calls", response_model=CallsResponse)
def calls(
    repo: str | None = None,
    caller_name: str | None = None,
    callee_name: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> CallsResponse:
    conn = connect_db()
    try:
        rows = query_calls(conn, repo, caller_name, callee_name, limit)
        return CallsResponse(calls=[dict(row) for row in rows])
    finally:
        conn.close()


@app.get("/imports", response_model=ImportsResponse)
def imports(
    repo: str | None = None,
    module: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> ImportsResponse:
    conn = connect_db()
    try:
        rows = query_imports(conn, repo, module, limit)
        return ImportsResponse(imports=[dict(row) for row in rows])
    finally:
        conn.close()


@app.get("/scip/documents", response_model=ScipDocumentsResponse)
def scip_documents(repo: str | None = None, limit: int = Query(default=50, ge=1, le=200)) -> ScipDocumentsResponse:
    conn = connect_db()
    try:
        rows = query_scip_documents(conn, repo, limit)
        return ScipDocumentsResponse(documents=[dict(row) for row in rows])
    finally:
        conn.close()


@app.get("/scip/symbols", response_model=ScipSymbolsResponse)
def scip_symbols(
    repo: str | None = None,
    prefix: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> ScipSymbolsResponse:
    conn = connect_db()
    try:
        rows = query_scip_symbols(conn, repo, prefix, limit)
        return ScipSymbolsResponse(symbols=[dict(row) for row in rows])
    finally:
        conn.close()


@app.get("/scip/occurrences", response_model=ScipOccurrencesResponse)
def scip_occurrences(
    repo: str | None = None,
    symbol: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> ScipOccurrencesResponse:
    conn = connect_db()
    try:
        rows = query_scip_occurrences(conn, repo, symbol, limit)
        return ScipOccurrencesResponse(occurrences=[dict(row) for row in rows])
    finally:
        conn.close()
```

- [ ] **Step 6: Run API tests**

Run:

```bash
cd ast-service && pytest tests/test_api.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add ast-service
git commit -m "feat(ast-service): add structure query APIs"
```

## Task 9: Add Index, SCIP Export, and Search APIs

**Files:**
- Create: `ast-service/scip_proto/__init__.py`
- Create: `ast-service/scip_proto/scip_pb2.py`
- Modify: `ast-service/scip.py`
- Modify: `ast-service/main.py`
- Modify: `ast-service/models.py`
- Modify: `ast-service/tests/test_api.py`

- [ ] **Step 1: Add route tests for request validation**

Append to `ast-service/tests/test_api.py`:

```python
def test_search_requires_bounded_limit():
    client = TestClient(app)
    resp = client.post(
        "/search",
        json={
            "repo": "repo",
            "language": "Python",
            "pattern": "$A($$$ARGS)",
            "limit": 1000,
        },
    )
    assert resp.status_code == 422


def test_scip_export_json_returns_index_shape():
    client = TestClient(app)
    resp = client.get("/scip/export.json?repo=missing")
    assert resp.status_code == 200
    data = resp.json()
    assert data["metadata"]["tool_info"]["name"] == "repo-bot ast-service"
    assert data["documents"] == []
```

- [ ] **Step 2: Extend models for search and SCIP export responses**

Extend `ast-service/models.py`:

```python
from typing import Any


class SearchMatch(BaseModel):
    repo: str
    path: str
    start_line: int
    end_line: int
    text: str


class SearchResponse(BaseModel):
    matches: list[SearchMatch]


class ScipExportJsonResponse(BaseModel):
    metadata: dict[str, Any]
    documents: list[dict[str, Any]]
```

- [ ] **Step 3: Add SCIP export helpers**

Add generated protobuf bindings:

```text
ast-service/scip_proto/__init__.py
ast-service/scip_proto/scip_pb2.py
```

Generate `scip_pb2.py` from the official SCIP `scip.proto`. Keep the generated file isolated under `ast-service/scip_proto/` and do not hand-edit generated protobuf code.

Extend `ast-service/scip.py`:

```python
def build_scip_export_json(conn, repo: str) -> dict:
    documents = conn.execute(
        "SELECT * FROM scip_documents WHERE repo = ? ORDER BY relative_path",
        (repo,),
    ).fetchall()
    payload_documents = []
    for document in documents:
        occurrences = conn.execute(
            """
            SELECT * FROM scip_occurrences
            WHERE document_id = ?
            ORDER BY range_start_line, range_start_character
            """,
            (document["id"],),
        ).fetchall()
        payload_documents.append(
            {
                "relative_path": document["relative_path"],
                "language": document["language"],
                "position_encoding": document["position_encoding"],
                "occurrences": [dict(row) for row in occurrences],
            }
        )
    return {
        "metadata": {
            "version": "0.1",
            "tool_info": {"name": "repo-bot ast-service"},
            "project_root": repo,
        },
        "documents": payload_documents,
    }


def build_scip_export_protobuf(conn, repo: str) -> bytes:
    # Use generated classes from the official scip.proto, isolated under
    # ast-service/scip_proto/. The exact import path depends on the generation
    # command used by the worker.
    from scip_proto import scip_pb2

    debug_payload = build_scip_export_json(conn, repo)
    index = scip_pb2.Index()
    index.metadata.version = scip_pb2.ProtocolVersion.UnspecifiedProtocolVersion
    index.metadata.tool_info.name = "repo-bot ast-service"
    index.metadata.project_root = repo

    for document_payload in debug_payload["documents"]:
        document = index.documents.add()
        document.relative_path = document_payload["relative_path"]
        document.language = document_payload["language"] or ""
        document.position_encoding = scip_pb2.PositionEncoding.UTF8
        for item in document_payload["occurrences"]:
            occurrence = document.occurrences.add()
            occurrence.symbol = item["scip_symbol"]
            occurrence.range.extend([
                item["range_start_line"],
                item["range_start_character"],
                item["range_end_line"],
                item["range_end_character"],
            ])
            occurrence.symbol_roles = item["symbol_roles"]

    for row in conn.execute(
        "SELECT * FROM scip_symbols WHERE repo = ? ORDER BY scip_symbol",
        (repo,),
    ).fetchall():
        info = index.external_symbols.add()
        info.symbol = row["scip_symbol"]
        info.display_name = row["display_name"]
        info.documentation.append(row["documentation"] or "")

    return index.SerializeToString()
```

Note: this JSON is a debug view shaped after SCIP concepts. The protobuf `.scip` endpoint is the compatibility target and must be implemented before Task 9 is complete.

- [ ] **Step 4: Add `/index`, `/scip/export.json`, `/scip/export`, and `/search` routes**

Modify `ast-service/main.py`:

```python
from fastapi.responses import Response
from indexer import run_index
from models import IndexRequest, IndexResponse, ScipExportJsonResponse, SearchRequest, SearchResponse
from scip import build_scip_export_json, build_scip_export_protobuf


@app.post("/index", response_model=IndexResponse)
def index(request: IndexRequest) -> IndexResponse:
    result = run_index(mode=request.mode, repo=request.repo)
    return IndexResponse(
        status="ok",
        run_id=result.run_id,
        files_seen=result.files_seen,
        files_indexed=result.files_indexed,
        symbols_count=result.symbols_count,
        calls_count=result.calls_count,
        imports_count=result.imports_count,
    )


@app.get("/scip/export.json", response_model=ScipExportJsonResponse)
def scip_export_json(repo: str) -> ScipExportJsonResponse:
    conn = connect_db()
    try:
        return ScipExportJsonResponse(**build_scip_export_json(conn, repo))
    finally:
        conn.close()


@app.get("/scip/export")
def scip_export(repo: str) -> Response:
    conn = connect_db()
    try:
        payload = build_scip_export_protobuf(conn, repo)
        return Response(
            content=payload,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{repo}.scip"'},
        )
    finally:
        conn.close()


@app.post("/search", response_model=SearchResponse)
def search(request: SearchRequest) -> SearchResponse:
    # Implement as a bounded auxiliary path only. It must never scan every repo
    # without repo/path/limit constraints.
    return SearchResponse(matches=[])
```

Note: Task 9 can leave `/search` returning an empty list after validation. `/scip/export` must return a real SCIP protobuf `Index` payload before this task is considered complete. Use the official `scip.proto` as the contract and keep generated protobuf code isolated under `ast-service/scip_proto/` if generation is needed.

- [ ] **Step 5: Run API tests**

Run:

```bash
cd ast-service && pytest tests/test_api.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add ast-service
git commit -m "feat(ast-service): add indexing API"
```

## Task 10: Add Docker Compose, npm, and Environment Integration

**Files:**
- Modify: `docker-compose.yml`
- Modify: `package.json`
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: Modify Docker Compose**

In `docker-compose.yml`, add service:

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

Add to `chat-ui.environment`:

```yaml
      - AST_SERVICE_URL=http://ast-service:8502
```

Add `ast-service` to `chat-ui.depends_on`.

Add volume:

```yaml
  ast_data:
```

- [ ] **Step 2: Modify package scripts**

In `package.json`, add:

```json
"index:ast": "docker exec repo-bot-ast-service-1 python /app/indexer.py --mode full",
"index:ast:incr": "docker exec repo-bot-ast-service-1 python /app/indexer.py --mode incremental",
"open:ast": "open http://localhost:8502/docs"
```

- [ ] **Step 3: Modify `.env.example`**

Add:

```text
# ast-grep structural index service
AST_SERVICE_URL=http://localhost:8502
```

- [ ] **Step 4: Update README**

Add `ast-service` to the architecture diagram and daily commands table:

```text
| `npm run index:ast` | 全量重建 ast-grep SQLite 结构索引 |
| `npm run index:ast:incr` | 增量更新 ast-grep SQLite 结构索引 |
| `npm run open:ast` | 打开 ast-service API 文档 |
```

- [ ] **Step 5: Validate JSON**

Run:

```bash
node -e "JSON.parse(require('fs').readFileSync('package.json','utf8')); console.log('ok')"
```

Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml package.json .env.example README.md
git commit -m "build(ast-service): add compose and npm integration"
```

## Task 11: Integrate Chat UI Structural Context

**Files:**
- Modify: `chat-ui/app.py`
- Modify: `chat-ui/requirements.txt` only if needed

- [ ] **Step 1: Add AST service sidebar controls**

In `chat-ui/app.py`, inside the sidebar after Sourcebot caption, add:

```python
    st.caption(f"AST: {os.environ.get('AST_SERVICE_URL', 'http://ast-service:8502')}")
```

Near existing search checkboxes, add:

```python
    use_ast = st.checkbox("AST 结构检索", value=True)
```

- [ ] **Step 2: Add AST client helpers**

Add below `search_sourcebot`:

```python
def candidate_symbols(query: str, results: list[dict], limit: int = 8) -> list[str]:
    import re

    names: list[str] = []
    pattern = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?\b")
    for text in [query] + [r.get("content", "") for r in results]:
        for match in pattern.findall(text or ""):
            if len(match) < 3:
                continue
            if match in {"the", "and", "for", "return", "class", "function", "def"}:
                continue
            if match not in names:
                names.append(match)
            if len(names) >= limit:
                return names
    return names


def search_ast_structure(query: str, results: list[dict], limit: int = 8) -> list[str]:
    import requests

    url = os.environ.get("AST_SERVICE_URL", "http://ast-service:8502").rstrip("/")
    symbols = candidate_symbols(query, results, limit=limit)
    facts: list[str] = []
    seen: set[str] = set()
    for name in symbols:
        repos = [r.get("repo") for r in results if r.get("repo")]
        repos = list(dict.fromkeys(repos))[:3] or [None]
        for repo in repos:
            params = {"callee_name": name, "limit": 5}
            if repo:
                params["repo"] = repo
            try:
                resp = requests.get(f"{url}/calls", params=params, timeout=3)
                resp.raise_for_status()
                for call in resp.json().get("calls", []):
                    fact = (
                        f"[structure] {name} called at "
                        f"{call.get('repo')}/{call.get('path')}:L{call.get('call_line')}"
                    )
                    if fact not in seen:
                        seen.add(fact)
                        facts.append(fact)
                    if len(facts) >= limit:
                        return facts
            except Exception:
                return []
    return facts
```

- [ ] **Step 3: Include structural context in LLM input**

In the chat flow after filling `merged` content, add:

```python
            ast_facts = search_ast_structure(prompt, merged) if use_ast else []
```

When building `ctx_json`, include structure facts as synthetic context:

```python
            ctx_items = [{
                "repo": r["repo"], "path": r["path"], "line": r["line"],
                "language": r.get("language", ""), "content": r.get("content", ""),
            } for r in merged]
            if ast_facts:
                ctx_items.append({
                    "repo": "ast-service",
                    "path": "structure",
                    "line": "",
                    "language": "text",
                    "content": "\n".join(ast_facts),
                })
            ctx_json = json.dumps(ctx_items)
```

- [ ] **Step 4: Show AST facts in expander**

After the existing retrieval expander, add:

```python
        if ast_facts:
            with st.expander(f"结构上下文 {len(ast_facts)} 条", expanded=False):
                for fact in ast_facts:
                    st.caption(fact)
```

- [ ] **Step 5: Run syntax check**

Run:

```bash
python3 -m py_compile chat-ui/app.py
```

Expected: no output and exit code 0.

- [ ] **Step 6: Commit**

```bash
git add chat-ui/app.py
git commit -m "feat(chat-ui): enrich answers with AST structure"
```

## Task 12: End-to-End Verification

**Files:**
- No source changes expected unless verification exposes issues.

- [ ] **Step 1: Run ast-service tests**

Run:

```bash
cd ast-service && pytest -v
```

Expected: all tests pass.

- [ ] **Step 2: Build service**

Run:

```bash
docker compose build ast-service
```

Expected: build succeeds.

- [ ] **Step 3: Start services**

Run:

```bash
docker compose up -d ast-service qdrant sourcebot chat-ui
```

Expected: all services start.

- [ ] **Step 4: Check health**

Run:

```bash
curl -fsSL http://localhost:8502/health
```

Expected:

```json
{"status":"ok"}
```

- [ ] **Step 5: Run AST full index**

Run:

```bash
npm run index:ast
```

Expected: command completes and prints an `IndexResult`.

- [ ] **Step 6: Query symbols**

Run:

```bash
curl -fsSL "http://localhost:8502/symbols?repo=repo-bot&limit=10"
```

Expected: JSON response with a `symbols` array.

- [ ] **Step 7: Query calls**

Run:

```bash
curl -fsSL "http://localhost:8502/calls?repo=repo-bot&limit=10"
```

Expected: JSON response with a `calls` array.

- [ ] **Step 8: Verify SCIP debug export**

Run:

```bash
curl -fsSL "http://localhost:8502/scip/export.json?repo=repo-bot"
```

Expected: JSON response with `metadata` and `documents`.

- [ ] **Step 9: Verify SCIP protobuf export**

Run:

```bash
curl -fsSL "http://localhost:8502/scip/export?repo=repo-bot" -o /tmp/repo-bot.scip
test -s /tmp/repo-bot.scip
```

Expected: command exits 0 and `/tmp/repo-bot.scip` is non-empty.

- [ ] **Step 10: Verify Chat UI still loads**

Open:

```text
http://localhost:8501
```

Expected: Chat UI loads and the sidebar includes AST service information and the `AST 结构检索` checkbox.

- [ ] **Step 11: Commit any verification fixes**

If verification required fixes:

```bash
git add <changed-files>
git commit -m "fix(ast-service): address verification issues"
```

If no fixes were needed, do not create an empty commit.

## Rollback Notes

If the AST integration causes issues:

- Disable the Chat UI checkbox.
- Remove or unset `AST_SERVICE_URL`.
- Stop only `ast-service` with `docker compose stop ast-service`.
- Existing Sourcebot and Qdrant search should continue working.

## Done Criteria

- All ast-service tests pass.
- Docker build succeeds.
- `/health`, `/symbols`, `/calls`, and `/imports` return valid JSON.
- `/scip/documents`, `/scip/symbols`, and `/scip/occurrences` return valid JSON.
- `/scip/export.json` returns SCIP-shaped debug JSON.
- `/scip/export` returns a non-empty SCIP protobuf payload.
- `npm run index:ast` performs offline preprocessing into SQLite.
- Offline indexing writes SCIP documents, occurrences, symbols, and relationships where inferable.
- Chat UI can include structural facts when AST retrieval is enabled.
- Chat UI still answers with Qdrant + Sourcebot when `ast-service` is unavailable.
- No application code directly traverses tree-sitter ASTs.
- ast-grep extraction rules live in YAML files.
