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


def to_hits(results: list[dict], source: str) -> list[RetrievalHit]:
    hits = []
    for r in results:
        line = r.get("line") or f"L{r.get('start_line', 1)}"
        hits.append(RetrievalHit(
            source=source,
            repo=r.get("repo", ""),
            path=r.get("path", ""),
            line_range=line,
            content=r.get("content", ""),
            strength="exact_text" if source == "sourcebot" else "semantic",
            score=r.get("score"),
        ))
    return hits


def merge_results(src: list, qdr: list, top_k: int = 15) -> list[dict]:
    k = 60
    scores: dict[str, float] = {}
    all_r: dict[str, dict] = {}
    for rank, r in enumerate(src):
        key = f"{r['repo']}:{r['path']}:{r['line']}"
        scores[key] = scores.get(key, 0) + 1 / (k + rank + 1)
        all_r[key] = r
    for rank, r in enumerate(qdr):
        key = f"{r['repo']}:{r['path']}:{r['line']}"
        scores[key] = scores.get(key, 0) + 1 / (k + rank + 1)
        all_r[key] = r
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
    return [all_r[k] for k, _ in ranked]


def confirmed_repos_from_results(results: list[dict], synthetic_repos: set[str] | None = None) -> set[str]:
    from .ranking import SYNTHETIC_REPOS

    blocked = synthetic_repos or SYNTHETIC_REPOS
    return {item["repo"] for item in results if item.get("repo") and item.get("repo") not in blocked}


def _hydrate_merged_content(merged: list[dict], backends: RetrievalBackends) -> list[dict]:
    hydrated = []
    for r in merged:
        if r.get("content"):
            hydrated.append(r)
            continue
        repo = r.get("repo", "")
        path = r.get("path", "")
        start = r.get("start_line", 1)
        end = r.get("end_line", start + 5)
        try:
            content = backends.read_file_content(repo, path, start, end)
        except Exception:
            content = ""
        hydrated.append({**r, "content": content})
    return hydrated


def run_retrieval_loop(
    question: str,
    *,
    repos_root: str,
    backends: RetrievalBackends,
    use_sourcebot: bool = True,
    use_qdrant: bool = True,
    use_ast: bool = True,
    use_graph: bool = True,
    max_rounds: int = 2,
) -> RetrievalLoopResult:
    from .planner import merge_llm_plan, plan_query
    from .ranking import rank_code_repositories

    # 1. Initial Planning
    plan = plan_query(question)
    if backends.llm_plan:
        try:
            llm_result = backends.llm_plan(question, plan)
            if isinstance(llm_result, dict) and llm_result:
                plan = merge_llm_plan(plan, llm_result)
        except Exception:
            pass

    # 2. Query Expansion
    queries = expand_queries(question, plan)

    all_sourcebot_results: list[dict] = []
    all_qdrant_results: list[dict] = []
    rounds: list[RetrievalRound] = []
    notes: list[str] = []

    # Round 1: Global search
    sourcebot_queries = queries["sourcebot"][:8]
    qdrant_queries = queries["qdrant"][:3]

    if use_sourcebot:
        for q in sourcebot_queries:
            try:
                results = backends.search_sourcebot(q, 5)
                all_sourcebot_results.extend(results)
            except Exception as e:
                notes.append(f"sourcebot error ({q}): {e}")

    if use_qdrant:
        for q in qdrant_queries:
            try:
                results = backends.search_qdrant(q, 5)
                all_qdrant_results.extend(results)
            except Exception as e:
                notes.append(f"qdrant error ({q}): {e}")

    round1 = RetrievalRound(
        index=1,
        sourcebot_queries=list(sourcebot_queries),
        qdrant_queries=list(qdrant_queries),
        notes=list(notes),
    )

    # Dedupe and merge
    src_hits = to_hits(all_sourcebot_results, "sourcebot")
    qdr_hits = to_hits(all_qdrant_results, "qdrant")
    merged = merge_results(all_sourcebot_results, all_qdrant_results)
    merged = _hydrate_merged_content(merged, backends)

    all_hits: list[RetrievalHit] = list(src_hits) + list(qdr_hits)

    # Confirmed repos
    confirmed_repos = confirmed_repos_from_results(all_sourcebot_results + all_qdrant_results)

    # Rank
    ranked_repos = rank_code_repositories(all_hits)

    round1.new_hits = len(all_hits)
    rounds.append(round1)

    return RetrievalLoopResult(
        plan=plan,
        hits=all_hits,
        merged=merged,
        ast_facts=[],
        graph_facts=[],
        ranked_repos=ranked_repos,
        confirmed_repos=confirmed_repos,
        rounds=rounds,
    )
