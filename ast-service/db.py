import os
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timezone

from normalizer import CallRecord, ImportRecord, SymbolRecord
from scip import ScipOccurrenceRow, ScipSymbolRow


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

CREATE TABLE IF NOT EXISTS symbols (
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

CREATE TABLE IF NOT EXISTS calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  repo TEXT NOT NULL,
  caller_symbol_id INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
  callee_name TEXT NOT NULL,
  callee_symbol_id INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
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
CREATE INDEX IF NOT EXISTS idx_symbols_repo_name ON symbols(repo, name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(repo, qualified_name);
CREATE INDEX IF NOT EXISTS idx_calls_repo_callee ON calls(repo, callee_name);
CREATE INDEX IF NOT EXISTS idx_calls_repo_caller ON calls(repo, caller_symbol_id);
CREATE INDEX IF NOT EXISTS idx_imports_repo_module ON imports(repo, module_path);

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

CREATE INDEX IF NOT EXISTS idx_scip_documents_repo_path ON scip_documents(repo, relative_path);
CREATE INDEX IF NOT EXISTS idx_scip_symbols_repo_symbol ON scip_symbols(repo, scip_symbol);
CREATE INDEX IF NOT EXISTS idx_scip_occurrences_symbol ON scip_occurrences(repo, scip_symbol);
CREATE INDEX IF NOT EXISTS idx_scip_relationships_source ON scip_relationships(repo, source_symbol);
CREATE INDEX IF NOT EXISTS idx_scip_relationships_target ON scip_relationships(repo, target_symbol);
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


def link_calls_in_file(conn: sqlite3.Connection, file_id: int, repo: str) -> int:
    symbols = conn.execute(
        "SELECT id, start_line, end_line FROM symbols WHERE file_id = ?",
        (file_id,),
    ).fetchall()
    if not symbols:
        return 0
    symbols.sort(key=lambda s: (s["end_line"] or s["start_line"]) - s["start_line"])
    updated = 0
    for sym in symbols:
        end = sym["end_line"] if sym["end_line"] is not None else sym["start_line"]
        conn.execute(
            """
            UPDATE calls SET caller_symbol_id = ?
            WHERE file_id = ? AND call_line >= ? AND call_line <= ?
              AND caller_symbol_id IS NULL
            """,
            (sym["id"], file_id, sym["start_line"], end),
        )
        updated += conn.total_changes
    return updated


def link_callee_symbols(conn: sqlite3.Connection, repo: str) -> int:
    updated = 0
    rows = conn.execute(
        """
        SELECT calls.id, calls.callee_name
        FROM calls
        JOIN files ON files.id = calls.file_id
        WHERE calls.repo = ? AND calls.callee_symbol_id IS NULL AND files.deleted_at IS NULL
        """,
        (repo,),
    ).fetchall()
    for row in rows:
        symbol = conn.execute(
            "SELECT id FROM symbols WHERE repo = ? AND name = ? LIMIT 1",
            (repo, row["callee_name"]),
        ).fetchone()
        if symbol is not None:
            conn.execute(
                "UPDATE calls SET callee_symbol_id = ? WHERE id = ?",
                (symbol["id"], row["id"]),
            )
            updated += 1
    conn.commit()
    return updated


def mark_deleted_files(conn: sqlite3.Connection, repo: str, seen_file_ids: set[int]) -> int:
    now = utc_now()
    if not seen_file_ids:
        cur = conn.execute(
            "UPDATE files SET deleted_at = ? WHERE repo = ? AND deleted_at IS NULL",
            (now, repo),
        )
    else:
        placeholders = ",".join("?" * len(seen_file_ids))
        cur = conn.execute(
            f"UPDATE files SET deleted_at = ? WHERE repo = ? AND deleted_at IS NULL AND id NOT IN ({placeholders})",
            [now, repo] + list(seen_file_ids),
        )
    conn.commit()
    return cur.rowcount


def scip_index_one_file(
    conn: sqlite3.Connection,
    file_id: int,
    repo: str,
    rel_path: str,
    language: str,
    source_text: str,
    symbol_records: Iterable[SymbolRecord],
) -> tuple[int, int]:
    from scip import ScipOccurrenceRow, ScipSymbolRow, symbol_to_scip_rows

    doc_id = upsert_scip_document(conn, file_id, repo, rel_path, language)
    scip_symbols: list[ScipSymbolRow] = []
    scip_occurrences: list[ScipOccurrenceRow] = []
    for sym in symbol_records:
        s_row, o_row = symbol_to_scip_rows(sym, doc_id, source_text)
        scip_symbols.append(s_row)
        scip_occurrences.append(o_row)
    return replace_scip_records(conn, doc_id, repo, scip_symbols, scip_occurrences)


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


def get_symbol_detail(conn: sqlite3.Connection, symbol_id: int) -> tuple[sqlite3.Row | None, list[sqlite3.Row], list[sqlite3.Row]]:
    symbol = conn.execute(
        """
        SELECT symbols.*, files.path
        FROM symbols
        JOIN files ON files.id = symbols.file_id
        WHERE symbols.id = ? AND files.deleted_at IS NULL
        """,
        (symbol_id,),
    ).fetchone()
    if symbol is None:
        return None, [], []
    callers = conn.execute(
        """
        SELECT calls.*, files.path
        FROM calls
        JOIN files ON files.id = calls.file_id
        WHERE calls.callee_symbol_id = ? AND files.deleted_at IS NULL
        ORDER BY calls.repo, files.path, calls.call_line
        LIMIT 50
        """,
        (symbol_id,),
    ).fetchall()
    callees = conn.execute(
        """
        SELECT calls.*, files.path
        FROM calls
        JOIN files ON files.id = calls.file_id
        WHERE calls.caller_symbol_id = ? AND files.deleted_at IS NULL
        ORDER BY calls.repo, files.path, calls.call_line
        LIMIT 50
        """,
        (symbol_id,),
    ).fetchall()
    return symbol, callers, callees


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


def query_runs(conn: sqlite3.Connection, repo: str | None, limit: int) -> list[sqlite3.Row]:
    sql = "SELECT * FROM index_runs WHERE 1 = 1"
    params: list[object] = []
    if repo:
        sql += " AND (repo = ? OR repo IS NULL)"
        params.append(repo)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def latest_runs(conn: sqlite3.Connection, limit: int = 5) -> list[sqlite3.Row]:
    return query_runs(conn, None, limit)


def query_scip_documents(conn: sqlite3.Connection, repo: str | None, limit: int) -> list[sqlite3.Row]:
    sql = """
    SELECT scip_documents.*
    FROM scip_documents
    JOIN files ON files.id = scip_documents.file_id
    WHERE files.deleted_at IS NULL
    """
    params: list[object] = []
    if repo:
        sql += " AND scip_documents.repo = ?"
        params.append(repo)
    sql += " ORDER BY scip_documents.repo, scip_documents.relative_path LIMIT ?"
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
    sql = """
    SELECT scip_occurrences.*
    FROM scip_occurrences
    JOIN scip_documents ON scip_documents.id = scip_occurrences.document_id
    JOIN files ON files.id = scip_documents.file_id
    WHERE files.deleted_at IS NULL
    """
    params: list[object] = []
    if repo:
        sql += " AND scip_occurrences.repo = ?"
        params.append(repo)
    if symbol:
        sql += " AND scip_occurrences.scip_symbol = ?"
        params.append(symbol)
    sql += " ORDER BY scip_occurrences.repo, scip_occurrences.document_id, scip_occurrences.range_start_line, scip_occurrences.range_start_character LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()
