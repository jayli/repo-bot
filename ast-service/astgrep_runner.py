from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from ast_grep_py import SgRoot


@dataclass(frozen=True)
class AstGrepMatch:
    text: str
    start_line: int
    end_line: int
    start_character: int
    end_character: int
    captures: dict[str, str]
    rule_id: str
    entity_kind: str | None = None


LANGUAGE_MAP = {
    "Python": "python",
    "TypeScript": "typescript",
    "JavaScript": "javascript",
    "Java": "java",
    "Go": "go",
    "Rust": "rust",
    "Bash": "bash",
    "C": "c",
    "C++": "cpp",
    "Dart": "dart",
    "Swift": "swift",
    "python": "python",
    "typescript": "typescript",
    "javascript": "javascript",
    "java": "java",
    "go": "go",
    "rust": "rust",
    "bash": "bash",
    "c": "c",
    "cpp": "cpp",
    "dart": "dart",
    "swift": "swift",
}


def load_rule(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


SINGLE_CAPTURES = ("NAME", "CALLEE", "MODULE")
MULTI_CAPTURES = ("ARGS", "BODY", "NAMES", "IMPORTS", "PARAMS")

# field: extraction mapping — captures[KEY] = node.field(FIELD_NAME).text()
# Used when pattern-based $METAVAR extraction is unreliable (Java modifiers, C++ structs, etc.)
FIELD_CAP = {
    "NAME": "name",       # symbol name (method, class, struct, function)
    "CALLEE": "function", # call target (function/method expression)
    "MODULE": "path",     # import/include path
}


def _captures(node: Any, field_map: dict[str, str] | None = None) -> dict[str, str]:
    captures: dict[str, str] = {}
    get_match = getattr(node, "get_match", None)
    if get_match is not None:
        for name in SINGLE_CAPTURES:
            try:
                value = get_match(name)
            except Exception:
                value = None
            if value is not None:
                captures[name] = value.text()

    get_multiple_matches = getattr(node, "get_multiple_matches", None)
    if get_multiple_matches is not None:
        for name in MULTI_CAPTURES:
            try:
                values = get_multiple_matches(name)
            except Exception:
                values = []
            if values:
                captures[name] = ", ".join(v.text() for v in values)

    # field: extraction — node.field(FIELD_NAME).text() → captures[KEY]
    # Supports chained access via dot notation: "declarator.declarator"
    # Only fills in keys not already set by pattern metavariables
    if field_map:
        field_fn = getattr(node, "field", None)
        if field_fn is not None:
            for cap_key, field_name in field_map.items():
                if cap_key in captures:
                    continue  # pattern metavar takes precedence
                try:
                    if isinstance(field_name, list):
                        # Chained field access: ["declarator", "declarator"]
                        fnode = node
                        for fn in field_name:
                            fnode = fnode.field(fn)
                            if fnode is None:
                                break
                    else:
                        fnode = field_fn(field_name)
                except Exception:
                    fnode = None
                if fnode is not None:
                    try:
                        captures[cap_key] = fnode.text()
                    except Exception:
                        pass
    return captures


def _range(node: Any) -> tuple[int, int, int, int]:
    rng = node.range()
    start = getattr(rng, "start", None) or rng["start"]
    end = getattr(rng, "end", None) or rng["end"]
    start_line = getattr(start, "line", None) if not isinstance(start, dict) else start["line"]
    start_col = getattr(start, "column", None) if not isinstance(start, dict) else start["column"]
    end_line = getattr(end, "line", None) if not isinstance(end, dict) else end["line"]
    end_col = getattr(end, "column", None) if not isinstance(end, dict) else end["column"]
    return int(start_line) + 1, int(start_col), int(end_line) + 1, int(end_col)


def _find_all(root_node: Any, rule: dict[str, Any]) -> list[Any]:
    try:
        return list(root_node.find_all(config={"rule": rule}))
    except TypeError:
        return list(root_node.find_all(**rule))


def run_rule_file(source_path: Path, rule_path: Path) -> list[AstGrepMatch]:
    rule_doc = load_rule(rule_path)
    language = LANGUAGE_MAP[rule_doc["language"]]
    source = source_path.read_text(encoding="utf-8", errors="replace")
    root = SgRoot(source, language)
    metadata = rule_doc.get("metadata") or {}

    # YAML field: {field_name: cap_key} → internal field_map: {cap_key: field_name}
    # Dot-separated field names become lists for chained access: "declarator.declarator" → ["declarator", "declarator"]
    field_doc = rule_doc.get("field") or {}
    field_map = {}
    for field_key, cap_key in field_doc.items():
        field_map[cap_key] = field_key.split(".") if "." in field_key else field_key
    field_map = field_map if field_map else None

    nodes = _find_all(root.root(), rule_doc["rule"])
    matches: list[AstGrepMatch] = []
    for node in nodes:
        text = node.text()
        start_line, start_character, end_line, end_character = _range(node)
        matches.append(
            AstGrepMatch(
                text=text,
                start_line=start_line,
                end_line=end_line,
                start_character=start_character,
                end_character=end_character,
                captures=_captures(node, field_map),
                rule_id=rule_doc["id"],
                entity_kind=metadata.get("entity_kind"),
            )
        )
    return matches
