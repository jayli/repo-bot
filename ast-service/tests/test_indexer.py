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


def test_full_index_writes_scip_data(tmp_path, monkeypatch):
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

    run_index(mode="full", repo=None)

    conn = connect_db(str(db_path))
    doc_count = conn.execute("SELECT COUNT(*) FROM scip_documents").fetchone()[0]
    sym_count = conn.execute("SELECT COUNT(*) FROM scip_symbols").fetchone()[0]
    occ_count = conn.execute("SELECT COUNT(*) FROM scip_occurrences").fetchone()[0]
    assert doc_count >= 1, f"scip_documents: {doc_count}"
    assert sym_count >= 3, f"scip_symbols: {sym_count}"
    assert occ_count >= 3, f"scip_occurrences: {occ_count}"


def test_full_index_links_call_graph(tmp_path, monkeypatch):
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

    run_index(mode="full", repo=None)

    conn = connect_db(str(db_path))
    linked_calls = conn.execute(
        "SELECT COUNT(*) FROM calls WHERE caller_symbol_id IS NOT NULL"
    ).fetchone()[0]
    assert linked_calls >= 1, f"calls with caller_symbol_id: {linked_calls}"

    linked_callees = conn.execute(
        "SELECT COUNT(*) FROM calls WHERE callee_symbol_id IS NOT NULL"
    ).fetchone()[0]
    assert linked_callees >= 1, f"calls with callee_symbol_id: {linked_callees}"


def test_incremental_index_marks_deleted_files(tmp_path, monkeypatch):
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

    run_index(mode="full", repo=None)

    # Delete the fixture file
    (repo_root / "sample-python" / "app.py").unlink()

    # Run incremental — should mark file as deleted
    run_index(mode="incremental", repo=None)

    conn = connect_db(str(db_path))
    deleted = conn.execute(
        "SELECT COUNT(*) FROM files WHERE deleted_at IS NOT NULL"
    ).fetchone()[0]
    assert deleted >= 1, f"files with deleted_at: {deleted}"


def test_run_index_succeeds_with_graph_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("NEO4J_ENABLED", "false")
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
    assert result.files_indexed == 1

    conn = connect_db(str(db_path))
    status_row = conn.execute(
        "SELECT status FROM index_runs WHERE id = ?", (result.run_id,)
    ).fetchone()
    assert status_row is not None
    assert status_row["status"] == "ok"


def test_run_index_calls_graph_refresh_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("NEO4J_ENABLED", "true")
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

    call_args = []

    class FakeNeo4jDriver:
        def close(self):
            pass

    def fake_create_driver(config):
        return FakeNeo4jDriver()

    def fake_verify(driver, retries=5, delay_seconds=1.0):
        pass

    def fake_ensure(driver, database):
        pass

    def fake_refresh(conn_arg, driver, database, repo_name):
        call_args.append(repo_name)

    monkeypatch.setattr("indexer.create_driver", fake_create_driver)
    monkeypatch.setattr("indexer.verify_connectivity", fake_verify)
    monkeypatch.setattr("indexer.ensure_constraints", fake_ensure)
    monkeypatch.setattr("indexer.refresh_repo_graph", fake_refresh)

    run_index(mode="full", repo=None)
    assert len(call_args) >= 1
    assert "sample-python" in call_args


def test_run_index_errors_when_graph_refresh_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("NEO4J_ENABLED", "true")
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

    class FakeNeo4jDriver:
        def close(self):
            pass

    def fake_create_driver(config):
        return FakeNeo4jDriver()

    def fake_verify(driver, retries=5, delay_seconds=1.0):
        pass

    def fake_ensure(driver, database):
        pass

    def fake_refresh(conn_arg, driver, database, repo_name):
        raise RuntimeError("Neo4j unavailable")

    monkeypatch.setattr("indexer.create_driver", fake_create_driver)
    monkeypatch.setattr("indexer.verify_connectivity", fake_verify)
    monkeypatch.setattr("indexer.ensure_constraints", fake_ensure)
    monkeypatch.setattr("indexer.refresh_repo_graph", fake_refresh)

    try:
        run_index(mode="full", repo=None)
    except RuntimeError:
        pass

    conn2 = connect_db(str(db_path))
    row = conn2.execute(
        "SELECT status, error FROM index_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["status"] == "error"
    assert "Neo4j unavailable" in (row["error"] or "")
