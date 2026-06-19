from normalizer import SymbolRecord
from scip import (
    DEFINITION_ROLE,
    Position,
    make_occurrence_range,
    make_scip_symbol,
    symbol_to_scip_rows,
)


def test_make_scip_symbol_is_stable_and_repo_local():
    symbol = make_scip_symbol(
        repo="repo-bot",
        path="repo-bot/chat-ui/app.py",
        descriptor_chain=["search_qdrant()"],
    )

    assert symbol == "local repo-bot repo-bot/chat-ui/app.py / search_qdrant()."


def test_make_occurrence_range_is_zero_based():
    source = "def foo():\n    return 1\n"
    start = source.index("foo")
    end = start + len("foo")

    assert make_occurrence_range(source, start, end) == Position(0, 4, 0, 7)


def test_symbol_to_scip_rows_maps_definition_role():
    symbol = SymbolRecord(
        repo="repo-bot",
        path="repo-bot/app.py",
        name="foo",
        qualified_name="foo",
        kind="function",
        start_line=1,
        end_line=2,
    )

    scip_symbol, occurrence = symbol_to_scip_rows(
        symbol=symbol,
        document_id=1,
        source_text="def foo():\n    pass\n",
    )

    assert scip_symbol.scip_symbol.endswith("foo().")
    assert occurrence.symbol_roles & DEFINITION_ROLE
