from typing import Any, Literal

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


class SearchMatch(BaseModel):
    repo: str
    path: str
    start_line: int
    end_line: int
    text: str


class SearchResponse(BaseModel):
    matches: list[SearchMatch]


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


class SymbolDetailResponse(BaseModel):
    symbol: SymbolItem | None
    callers: list[dict[str, Any]]
    callees: list[dict[str, Any]]


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


class RunsResponse(BaseModel):
    runs: list[dict[str, Any]]


class ScipExportJsonResponse(BaseModel):
    metadata: dict[str, Any]
    documents: list[dict[str, Any]]


class ScipDocumentsResponse(BaseModel):
    documents: list[dict[str, Any]]


class ScipSymbolsResponse(BaseModel):
    symbols: list[dict[str, Any]]


class ScipOccurrencesResponse(BaseModel):
    occurrences: list[dict[str, Any]]
