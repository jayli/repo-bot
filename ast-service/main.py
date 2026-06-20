from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

from db import (
    connect_db,
    get_symbol_detail,
    init_db,
    latest_runs,
    query_calls,
    query_imports,
    query_runs,
    query_scip_documents,
    query_scip_occurrences,
    query_scip_symbols,
    query_symbols,
)
from graph import (
    GraphConfig,
    create_driver,
    ensure_constraints,
    graph_health,
    query_call_paths,
    query_impact,
    sync_graph_from_sqlite,
    verify_connectivity,
)
from indexer import run_index
from models import (
    CallsResponse,
    GraphCallPathsResponse,
    GraphHealthResponse,
    GraphImpactResponse,
    GraphSyncResponse,
    HealthResponse,
    IndexRequest,
    IndexResponse,
    ImportsResponse,
    RunsResponse,
    ScipDocumentsResponse,
    ScipExportJsonResponse,
    ScipOccurrencesResponse,
    ScipSymbolsResponse,
    SearchRequest,
    SearchResponse,
    StatusResponse,
    SymbolDetailResponse,
    SymbolsResponse,
)
from scip import build_scip_export_json, build_scip_export_protobuf


@asynccontextmanager
async def lifespan(_app: FastAPI):
    conn = connect_db()
    init_db(conn)
    conn.close()

    graph_config = GraphConfig.from_env()
    driver = create_driver(graph_config)
    if driver is not None:
        verify_connectivity(driver)
        ensure_constraints(driver, graph_config.database)
    _app.state.graph_config = graph_config
    _app.state.neo4j_driver = driver
    try:
        yield
    finally:
        if driver is not None:
            driver.close()


app = FastAPI(title="repo-bot ast-service", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


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


@app.get("/symbols/{symbol_id}", response_model=SymbolDetailResponse)
def symbol_detail(symbol_id: int) -> SymbolDetailResponse:
    conn = connect_db()
    try:
        symbol, callers, callees = get_symbol_detail(conn, symbol_id)
        return SymbolDetailResponse(
            symbol=dict(symbol) if symbol else None,
            callers=[dict(row) for row in callers],
            callees=[dict(row) for row in callees],
        )
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


@app.get("/status", response_model=StatusResponse)
def status() -> StatusResponse:
    conn = connect_db()
    try:
        return StatusResponse(latest_runs=[dict(row) for row in latest_runs(conn)])
    finally:
        conn.close()


@app.get("/runs", response_model=RunsResponse)
def runs(
    repo: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> RunsResponse:
    conn = connect_db()
    try:
        rows = query_runs(conn, repo, limit)
        return RunsResponse(runs=[dict(row) for row in rows])
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
    raise HTTPException(status_code=501, detail="Realtime ast-grep search is deferred")


@app.get("/graph/health", response_model=GraphHealthResponse)
def graph_health_endpoint() -> GraphHealthResponse:
    gc = app.state.graph_config
    driver = app.state.neo4j_driver
    result = graph_health(gc, driver)
    return GraphHealthResponse(**result)


@app.post("/graph/sync", response_model=GraphSyncResponse)
def graph_sync(repo: str | None = None) -> GraphSyncResponse:
    driver = app.state.neo4j_driver
    if driver is None:
        raise HTTPException(status_code=400, detail="Neo4j is disabled")
    config = app.state.graph_config
    conn = connect_db()
    try:
        repos_synced = sync_graph_from_sqlite(conn, driver, config.database, repo=repo)
        return GraphSyncResponse(status="ok", repos_synced=repos_synced)
    finally:
        conn.close()


@app.get("/graph/impact", response_model=GraphImpactResponse)
def graph_impact(
    repo: str,
    symbol: str,
    depth: int = Query(default=2, ge=1, le=4),
    limit: int = Query(default=50, ge=1, le=200),
) -> GraphImpactResponse:
    driver = app.state.neo4j_driver
    if driver is None:
        raise HTTPException(status_code=400, detail="Neo4j is disabled")
    config = app.state.graph_config
    facts = query_impact(driver, config.database, repo, symbol, depth, limit)
    return GraphImpactResponse(facts=facts)


@app.get("/graph/call-paths", response_model=GraphCallPathsResponse)
def graph_call_paths(
    repo: str,
    from_symbol: str,
    to_symbol: str,
    max_depth: int = Query(default=4, ge=1, le=6),
    limit: int = Query(default=50, ge=1, le=200),
) -> GraphCallPathsResponse:
    driver = app.state.neo4j_driver
    if driver is None:
        raise HTTPException(status_code=400, detail="Neo4j is disabled")
    config = app.state.graph_config
    paths = query_call_paths(driver, config.database, repo, from_symbol, to_symbol, max_depth, limit)
    return GraphCallPathsResponse(paths=paths)
