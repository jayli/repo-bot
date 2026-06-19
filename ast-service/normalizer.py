import json
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


def normalize_symbols(
    repo: str,
    path: str,
    language: str,
    matches: list[AstGrepMatch],
) -> list[SymbolRecord]:
    records: list[SymbolRecord] = []
    for match in matches:
        name = match.captures.get("NAME")
        kind = match.entity_kind
        if not name or not kind:
            continue
        signature = f"({match.captures['PARAMS']})" if "PARAMS" in match.captures else None
        records.append(
            SymbolRecord(repo, path, name, name, kind, match.start_line, match.end_line, signature)
        )
    return records


def normalize_calls(repo: str, path: str, matches: list[AstGrepMatch]) -> list[CallRecord]:
    records: list[CallRecord] = []
    for match in matches:
        callee = match.captures.get("CALLEE")
        if callee:
            records.append(
                CallRecord(repo=repo, path=path, callee_name=callee, call_line=match.start_line)
            )
    return records


def normalize_imports(
    repo: str,
    path: str,
    language: str,
    matches: list[AstGrepMatch],
) -> list[ImportRecord]:
    records: list[ImportRecord] = []
    for match in matches:
        module = match.captures.get("MODULE")
        if not module:
            continue
        imported = [
            item.strip()
            for item in match.captures.get("NAMES", match.captures.get("IMPORTS", "")).split(",")
            if item.strip()
        ]
        records.append(
            ImportRecord(
                repo=repo,
                path=path,
                module_path=module.strip("\"'"),
                imported_names_json=json.dumps(imported) if imported else None,
                import_line=match.start_line,
            )
        )
    return records
