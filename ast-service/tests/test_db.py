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
        "symbols",
        "calls",
        "imports",
    }.issubset(table_names(conn))


def test_init_db_creates_scip_phase_two_tables(tmp_path):
    conn = connect_db(str(tmp_path / "ast.sqlite"))
    init_db(conn)

    assert {
        "scip_documents",
        "scip_symbols",
        "scip_occurrences",
        "scip_relationships",
    }.issubset(table_names(conn))


def test_foreign_keys_are_enabled(tmp_path):
    conn = connect_db(str(tmp_path / "ast.sqlite"))
    enabled = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert enabled == 1
