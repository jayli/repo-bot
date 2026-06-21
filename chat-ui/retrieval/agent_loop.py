from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .models import RetrievalHit, RetrievalPlan


@dataclass
class RetrievalBackends:
    search_sourcebot: Callable[[str, int], list[dict]]
    search_qdrant: Callable[[str, int], list[dict]]
    search_ast_structure: Callable[[str, list[dict], int], list[str]]
    search_graph_relations: Callable[[str, list[dict], int], list[str]]
    read_file_content: Callable[[str, str, int, int], str]
    read_manifest: Callable[[str, str], list[RetrievalHit]]
    local_tool_list: Callable[..., list[RetrievalHit]]
    local_tool_grep: Callable[..., list[RetrievalHit]]
    local_tool_read: Callable[..., RetrievalHit | None]
    llm_plan: Callable[[str, RetrievalPlan], dict[str, Any]] | None = None


@dataclass
class LocalAction:
    tool: str
    repo: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class GapAction:
    kind: str
    repo: str | None = None
    package_name: str | None = None
    symbol: str | None = None
    priority: int = 100


@dataclass
class RetrievalRound:
    index: int
    sourcebot_queries: list[str] = field(default_factory=list)
    qdrant_queries: list[str] = field(default_factory=list)
    ast_queries: list[str] = field(default_factory=list)
    graph_queries: list[str] = field(default_factory=list)
    local_actions: list[LocalAction] = field(default_factory=list)
    new_hits: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class RetrievalLoopResult:
    plan: RetrievalPlan
    hits: list[RetrievalHit]
    merged: list[dict]
    ast_facts: list[str]
    graph_facts: list[str]
    ranked_repos: list[dict]
    confirmed_repos: set[str]
    rounds: list[RetrievalRound]


def unique_keep_order(items: list[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


_REQUIRE_SINGLE = re.compile(r"""require\((["'])([^"']+)\1\)""")
_IMPORT_FROM = re.compile(r"""(?:from|import)\s+(["'])([^"']+)\1""")
_DEPENDENCY_KEY = re.compile(r'"(dependencies|devDependencies|peerDependencies)"')


def expand_queries(question: str, plan: RetrievalPlan, discovered_terms: list[str] | None = None) -> dict[str, list[str]]:
    queries: dict[str, list[str]] = {
        "sourcebot": list(plan.queries.get("sourcebot", [])),
        "qdrant": list(plan.queries.get("qdrant", [question])),
        "ast": list(plan.queries.get("ast", [])),
        "graph": list(plan.queries.get("graph", [])),
    }

    if plan.intent != "dependency_relation":
        return queries

    obj = plan.entities.get("object")
    if not obj or not isinstance(obj, str):
        return queries

    extra_sourcebot = [
        f"require('{obj}')",
        f'require("{obj}")',
        f"from '{obj}'",
        f'from "{obj}"',
        f"import {obj} from",
        "dependencies",
    ]
    if discovered_terms:
        extra_sourcebot.extend(discovered_terms)

    queries["sourcebot"] = unique_keep_order(queries["sourcebot"] + extra_sourcebot)

    return queries


def extract_discovered_terms(hits: list[RetrievalHit]) -> list[str]:
    terms: list[str] = []
    for hit in hits:
        if hit.source not in {"sourcebot", "precision_search", "local_tool"}:
            continue
        if not hit.content:
            continue
        for match in _REQUIRE_SINGLE.findall(hit.content):
            pkg = match[1]
            if pkg and pkg not in terms:
                terms.append(pkg)
        for match in _IMPORT_FROM.findall(hit.content):
            pkg = match[1]
            if pkg and pkg not in terms and "/" not in pkg:
                terms.append(pkg)
    return terms
