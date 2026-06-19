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
    link_callee_symbols,
    link_calls_in_file,
    mark_deleted_files,
    replace_file_records,
    scip_index_one_file,
    start_index_run,
    transaction,
    upsert_file,
)
from normalizer import normalize_calls, normalize_imports, normalize_symbols
from repository_scanner import SourceFile, discover_source_files


_RULES = Path(__file__).resolve().parent / "rules"

RULES_BY_LANGUAGE = {
    "python": {
        "symbols": [_RULES / "python-functions.yml", _RULES / "python-classes.yml"],
        "calls": _RULES / "python-calls.yml",
        "imports": _RULES / "python-imports.yml",
    },
    "typescript": {
        "symbols": [_RULES / "ts-functions.yml", _RULES / "ts-classes.yml"],
        "calls": _RULES / "ts-calls.yml",
        "imports": _RULES / "ts-imports.yml",
    },
    "javascript": {
        "symbols": [_RULES / "ts-functions.yml", _RULES / "ts-classes.yml"],
        "calls": _RULES / "ts-calls.yml",
        "imports": _RULES / "ts-imports.yml",
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


def index_one_file(conn, source: SourceFile) -> tuple[int, int, int, int]:
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
    symbol_matches = []
    for symbol_rule in rules["symbols"]:
        symbol_matches.extend(run_rule_file(source.abs_path, symbol_rule))
    call_matches = run_rule_file(source.abs_path, rules["calls"])
    import_matches = run_rule_file(source.abs_path, rules["imports"])
    symbols = normalize_symbols(source.repo, source.rel_path, source.language, symbol_matches)
    calls = normalize_calls(source.repo, source.rel_path, call_matches)
    imports = normalize_imports(source.repo, source.rel_path, source.language, import_matches)
    s_count, c_count, i_count = replace_file_records(conn, file_id, source.repo, symbols, calls, imports)

    link_calls_in_file(conn, file_id, source.repo)

    source_text = source.abs_path.read_text(encoding="utf-8", errors="replace")
    scip_index_one_file(conn, file_id, source.repo, source.rel_path, source.language, source_text, symbols)

    return s_count, c_count, i_count, file_id


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
        seen_repos: set[str] = set()
        seen_file_ids: dict[str, set[int]] = {}
        for source in files:
            if repo and source.repo != repo:
                continue
            files_seen += 1
            digest = content_hash(source.abs_path)
            if not should_index(conn, source, mode, digest):
                seen_repos.add(source.repo)
                row = conn.execute(
                    "SELECT id FROM files WHERE repo = ? AND path = ?",
                    (source.repo, source.rel_path),
                ).fetchone()
                if row is not None:
                    seen_file_ids.setdefault(source.repo, set()).add(int(row["id"]))
                continue
            with transaction(conn):
                s_count, c_count, i_count, file_id = index_one_file(conn, source)
            files_indexed += 1
            symbols_count += s_count
            calls_count += c_count
            imports_count += i_count
            seen_repos.add(source.repo)
            seen_file_ids.setdefault(source.repo, set()).add(file_id)

        if mode == "incremental":
            sql = "SELECT DISTINCT repo FROM files WHERE deleted_at IS NULL"
            if repo:
                sql += " AND repo = ?"
                db_repos = {row["repo"] for row in conn.execute(sql, (repo,)).fetchall()}
            else:
                db_repos = {row["repo"] for row in conn.execute(sql).fetchall()}
            seen_repos |= db_repos

        for indexed_repo in sorted(seen_repos):
            link_callee_symbols(conn, indexed_repo)
            if mode == "incremental":
                mark_deleted_files(conn, indexed_repo, seen_file_ids.get(indexed_repo, set()))

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
