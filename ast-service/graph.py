import os
import time
from dataclasses import dataclass

from neo4j import GraphDatabase


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


DELETE_REPO_GRAPH = """
MATCH (n)
WHERE n.repo = $repo
DETACH DELETE n
"""

DELETE_REPO_NODE = """
MATCH (r:Repository {name: $repo})
DETACH DELETE r
"""

MERGE_REPO = """
MERGE (:Repository {name: $repo})
"""

MERGE_FILES = """
UNWIND $rows AS row
MATCH (r:Repository {name: row.repo})
MERGE (f:File {repo: row.repo, path: row.path})
SET f.language = row.language, f.content_hash = row.content_hash
MERGE (r)-[:CONTAINS]->(f)
"""

MERGE_SYMBOLS = """
UNWIND $rows AS row
MATCH (f:File {repo: row.repo, path: row.path})
MERGE (s:Symbol {repo: row.repo, symbol_id: row.symbol_id})
SET s.path = row.path,
    s.name = row.name,
    s.qualified_name = row.qualified_name,
    s.kind = row.kind,
    s.start_line = row.start_line,
    s.end_line = row.end_line
MERGE (f)-[:DEFINES]->(s)
"""

MERGE_LINKED_CALLS = """
UNWIND $rows AS row
MATCH (cs:Symbol {repo: row.repo, symbol_id: row.caller_symbol_id})
MATCH (cd:Symbol {repo: row.repo, symbol_id: row.callee_symbol_id})
MERGE (cs)-[:CALLS {line: row.call_line, source: 'ast-grep', confidence: 1.0}]->(cd)
"""

MERGE_UNRESOLVED_CALLS = """
UNWIND $rows AS row
MATCH (cs:Symbol {repo: row.repo, symbol_id: row.caller_symbol_id})
MERGE (cd:ExternalSymbol {repo: row.repo, name: row.callee_name})
MERGE (cs)-[:CALLS {line: row.call_line, source: 'ast-grep', confidence: 0.5}]->(cd)
"""

MERGE_IMPORTS = """
UNWIND $rows AS row
MATCH (f:File {repo: row.repo, path: row.path})
MERGE (m:Module {repo: row.repo, module_path: row.module_path})
MERGE (f)-[rel:IMPORTS {line: row.import_line}]->(m)
SET rel.imported_names_json = row.imported_names_json
"""

BATCH_SIZE = 1000
MAX_NAME_LENGTH = 500


def _batch_run(session, statement: str, rows: list[dict], database: str) -> None:
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        session.run(statement, parameters={"rows": batch})


def refresh_repo_graph(conn, driver, database: str, repo: str) -> None:
    if driver is None:
        return

    def _do_refresh(tx):
        tx.run(DELETE_REPO_GRAPH, parameters={"repo": repo})
        tx.run(DELETE_REPO_NODE, parameters={"repo": repo})
        tx.run(MERGE_REPO, parameters={"repo": repo})

        file_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT repo, path, language, content_hash FROM files WHERE repo = ? AND deleted_at IS NULL",
                (repo,),
            ).fetchall()
        ]
        _batch_run(tx, MERGE_FILES, file_rows, database)

        symbol_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT s.repo, s.id AS symbol_id, f.path, s.name, s.qualified_name,
                       s.kind, s.start_line, s.end_line
                FROM symbols s
                JOIN files f ON f.id = s.file_id
                WHERE s.repo = ? AND f.deleted_at IS NULL
                """,
                (repo,),
            ).fetchall()
        ]
        _batch_run(tx, MERGE_SYMBOLS, symbol_rows, database)

        linked_call_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT c.repo, c.caller_symbol_id, c.callee_symbol_id, c.call_line
                FROM calls c
                JOIN files f ON f.id = c.file_id
                WHERE c.repo = ? AND c.callee_symbol_id IS NOT NULL AND f.deleted_at IS NULL
                """,
                (repo,),
            ).fetchall()
        ]
        _batch_run(tx, MERGE_LINKED_CALLS, linked_call_rows, database)

        unresolved_call_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT c.repo, c.caller_symbol_id, c.callee_name, c.call_line
                FROM calls c
                JOIN files f ON f.id = c.file_id
                WHERE c.repo = ? AND c.callee_symbol_id IS NULL AND c.caller_symbol_id IS NOT NULL AND f.deleted_at IS NULL
                """,
                (repo,),
            ).fetchall()
        ]
        for r in unresolved_call_rows:
            if len(r["callee_name"]) > MAX_NAME_LENGTH:
                r["callee_name"] = r["callee_name"][:MAX_NAME_LENGTH]
        _batch_run(tx, MERGE_UNRESOLVED_CALLS, unresolved_call_rows, database)

        import_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT i.repo, f.path, i.module_path, i.imported_names_json, i.import_line
                FROM imports i
                JOIN files f ON f.id = i.file_id
                WHERE i.repo = ? AND f.deleted_at IS NULL
                """,
                (repo,),
            ).fetchall()
        ]
        _batch_run(tx, MERGE_IMPORTS, import_rows, database)

    with driver.session(database=database) as session:
        session.execute_write(_do_refresh)


def graph_health(config: GraphConfig, driver) -> dict:
    if not config.enabled or driver is None:
        return {"enabled": False, "status": "disabled"}
    return {"enabled": True, "status": "ok"}


def sync_graph_from_sqlite(conn, driver, database: str, repo: str | None = None) -> list[str]:
    if driver is None:
        return []
    if repo:
        repos = [repo]
    else:
        rows = conn.execute("SELECT DISTINCT repo FROM files WHERE deleted_at IS NULL").fetchall()
        repos = [row["repo"] for row in rows]
    for r in repos:
        refresh_repo_graph(conn, driver, database, r)
    return repos


def query_impact(driver, database: str, repo: str, symbol: str, depth: int, limit: int) -> list[dict]:
    if driver is None:
        return []
    cypher = """
    MATCH (s:Symbol {repo: $repo, name: $symbol})
    CALL {
      WITH s
      MATCH path = (s)-[:CALLS*1..%d]->(target:Symbol {repo: $repo})
      RETURN target, length(path) AS dist
      UNION
      WITH s
      MATCH path = (s)<-[:CALLS*1..%d]-(caller:Symbol {repo: $repo})
      RETURN caller AS target, -length(path) AS dist
    }
    RETURN target, dist
    LIMIT $limit
    """ % (depth, depth)
    with driver.session(database=database) as session:
        result = session.run(cypher, parameters={"repo": repo, "symbol": symbol, "limit": limit})
        return [{"node": dict(record["target"]), "distance": record["dist"]} for record in result]


def query_call_paths(driver, database: str, repo: str, from_symbol: str, to_symbol: str, max_depth: int, limit: int) -> list[list[dict]]:
    if driver is None:
        return []
    cypher = """
    MATCH path = (s:Symbol {repo: $repo, name: $from})-[rels:CALLS*1..%d]->(t:Symbol {repo: $repo, name: $to})
    RETURN path
    LIMIT $limit
    """ % max_depth
    with driver.session(database=database) as session:
        result = session.run(cypher, parameters={"repo": repo, "from": from_symbol, "to": to_symbol, "limit": limit})
        paths: list[list[dict]] = []
        for record in result:
            p = record["path"]
            nodes = [dict(n) for n in p.nodes]
            paths.append(nodes)
        return paths
