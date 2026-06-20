import sqlite3

from db import init_db
from graph import (
    CONSTRAINTS,
    DELETE_REPO_GRAPH,
    MERGE_FILES,
    MERGE_IMPORTS,
    MERGE_LINKED_CALLS,
    MERGE_SYMBOLS,
    MERGE_UNRESOLVED_CALLS,
    GraphConfig,
    create_driver,
    ensure_constraints,
    refresh_repo_graph,
    verify_connectivity,
)


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


def _setup_sqlite() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)

    conn.execute("INSERT INTO files(repo, path, language, content_hash) VALUES (?, ?, ?, ?)",
                 ("test-repo", "a.py", "python", "abc123"))
    file_id = conn.execute("SELECT id FROM files WHERE path = 'a.py'").fetchone()["id"]

    conn.execute(
        "INSERT INTO symbols(file_id, repo, name, qualified_name, kind, start_line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (file_id, "test-repo", "foo", "foo", "function", 1, 5),
    )
    conn.execute(
        "INSERT INTO symbols(file_id, repo, name, qualified_name, kind, start_line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (file_id, "test-repo", "bar", "bar", "function", 10, 15),
    )
    foo_id = conn.execute("SELECT id FROM symbols WHERE name = 'foo'").fetchone()["id"]
    bar_id = conn.execute("SELECT id FROM symbols WHERE name = 'bar'").fetchone()["id"]

    conn.execute(
        "INSERT INTO calls(file_id, repo, caller_symbol_id, callee_name, callee_symbol_id, call_line) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (file_id, "test-repo", foo_id, "bar", bar_id, 3),
    )
    conn.execute(
        "INSERT INTO calls(file_id, repo, caller_symbol_id, callee_name, callee_symbol_id, call_line) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (file_id, "test-repo", bar_id, "external_func", None, 12),
    )

    conn.execute(
        "INSERT INTO imports(file_id, repo, module_path, imported_names_json, import_line) "
        "VALUES (?, ?, ?, ?, ?)",
        (file_id, "test-repo", "os", '["path"]', 1),
    )
    conn.commit()
    return conn


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


def test_create_driver_returns_none_when_disabled():
    config = GraphConfig(enabled=False, uri="", user="", password="", database="")
    assert create_driver(config) is None


def test_verify_connectivity_does_nothing_when_disabled():
    verify_connectivity(None)


def test_ensure_constraints_runs_all_statements():
    driver = FakeDriver()
    ensure_constraints(driver, "neo4j")
    statements = [s for s, _ in driver.session_obj.runs]
    assert len(statements) == len(CONSTRAINTS)
    for constraint in CONSTRAINTS:
        assert constraint in statements


def test_ensure_constraints_skips_when_disabled():
    ensure_constraints(None, "neo4j")


def test_refresh_repo_graph_skips_when_disabled():
    conn = _setup_sqlite()
    refresh_repo_graph(conn, None, "neo4j", "test-repo")


def test_refresh_repo_graph_deletes_before_recreating():
    conn = _setup_sqlite()
    driver = FakeDriver()

    refresh_repo_graph(conn, driver, "neo4j", "test-repo")

    statements = [s for s, _ in driver.session_obj.runs]
    assert DELETE_REPO_GRAPH in statements
    delete_idx = statements.index(DELETE_REPO_GRAPH)
    merge_files_idx = statements.index(MERGE_FILES) if MERGE_FILES in statements else 999
    assert delete_idx < merge_files_idx, "DELETE must happen before MERGE"


def test_refresh_repo_graph_uses_execute_write():
    conn = _setup_sqlite()
    driver = FakeDriver()

    refresh_repo_graph(conn, driver, "neo4j", "test-repo")

    assert len(driver.session_obj.write_calls) == 1


def test_refresh_repo_graph_writes_files():
    conn = _setup_sqlite()
    driver = FakeDriver()

    refresh_repo_graph(conn, driver, "neo4j", "test-repo")

    statements = [s for s, _ in driver.session_obj.runs]
    assert MERGE_FILES in statements


def test_refresh_repo_graph_writes_symbols():
    conn = _setup_sqlite()
    driver = FakeDriver()

    refresh_repo_graph(conn, driver, "neo4j", "test-repo")

    statements = [s for s, _ in driver.session_obj.runs]
    assert MERGE_SYMBOLS in statements


def test_refresh_repo_graph_writes_linked_calls():
    conn = _setup_sqlite()
    driver = FakeDriver()

    refresh_repo_graph(conn, driver, "neo4j", "test-repo")

    statements = [s for s, _ in driver.session_obj.runs]
    assert MERGE_LINKED_CALLS in statements


def test_refresh_repo_graph_writes_unresolved_calls():
    conn = _setup_sqlite()
    driver = FakeDriver()

    refresh_repo_graph(conn, driver, "neo4j", "test-repo")

    statements = [s for s, _ in driver.session_obj.runs]
    assert MERGE_UNRESOLVED_CALLS in statements


def test_refresh_repo_graph_writes_imports():
    conn = _setup_sqlite()
    driver = FakeDriver()

    refresh_repo_graph(conn, driver, "neo4j", "test-repo")

    statements = [s for s, _ in driver.session_obj.runs]
    assert MERGE_IMPORTS in statements


def test_refresh_repo_graph_no_enclosed_by():
    conn = _setup_sqlite()
    driver = FakeDriver()

    refresh_repo_graph(conn, driver, "neo4j", "test-repo")

    for statement, _ in driver.session_obj.runs:
        assert "ENCLOSED_BY" not in statement, "ENCLOSED_BY should not appear in first release"
