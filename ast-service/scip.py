from dataclasses import dataclass

from normalizer import SymbolRecord


# SCIP SymbolRole bit values.
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


def build_scip_export_json(conn, repo: str) -> dict:
    documents = conn.execute(
        """
        SELECT scip_documents.*
        FROM scip_documents
        JOIN files ON files.id = scip_documents.file_id
        WHERE scip_documents.repo = ? AND files.deleted_at IS NULL
        ORDER BY scip_documents.relative_path
        """,
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
    from scip_proto import scip_pb2

    debug_payload = build_scip_export_json(conn, repo)
    index = scip_pb2.Index()
    index.metadata.version = scip_pb2.ProtocolVersion.UnspecifiedProtocolVersion
    index.metadata.tool_info.name = "repo-bot ast-service"
    index.metadata.project_root = repo

    for document_payload in debug_payload["documents"]:
        document = scip_pb2.Document()
        document.relative_path = document_payload["relative_path"]
        document.language = document_payload["language"] or ""
        document.position_encoding = scip_pb2.PositionEncoding.UTF8CodeUnitOffsetFromLineStart
        for item in document_payload["occurrences"]:
            occurrence = scip_pb2.Occurrence()
            occurrence.symbol = item["scip_symbol"]
            occurrence.symbol_roles = item["symbol_roles"]
            sl = item["range_start_line"]
            sc = item["range_start_character"]
            el = item["range_end_line"]
            ec = item["range_end_character"]
            if sl == el:
                rng = scip_pb2.SingleLineRange()
                rng.line = sl
                rng.start_character = sc
                rng.end_character = ec
                occurrence.single_line_range.CopyFrom(rng)
            else:
                rng = scip_pb2.MultiLineRange()
                rng.start_line = sl
                rng.start_character = sc
                rng.end_line = el
                rng.end_character = ec
                occurrence.multi_line_range.CopyFrom(rng)
            document.occurrences.append(occurrence)
        index.documents.append(document)

    for row in conn.execute(
        """
        SELECT DISTINCT scip_symbols.*
        FROM scip_symbols
        JOIN scip_occurrences ON scip_occurrences.scip_symbol = scip_symbols.scip_symbol
          AND scip_occurrences.repo = scip_symbols.repo
        JOIN scip_documents ON scip_documents.id = scip_occurrences.document_id
        JOIN files ON files.id = scip_documents.file_id
        WHERE scip_symbols.repo = ? AND files.deleted_at IS NULL
        ORDER BY scip_symbols.scip_symbol
        """,
        (repo,),
    ).fetchall():
        info = scip_pb2.SymbolInformation()
        info.symbol = row["scip_symbol"]
        info.display_name = row["display_name"]
        if row["documentation"]:
            info.documentation.append(row["documentation"])
        index.external_symbols.append(info)

    return index.SerializeToString()
