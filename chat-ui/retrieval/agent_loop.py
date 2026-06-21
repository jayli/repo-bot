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


def _repo_has_manifest_hit(hits: list[RetrievalHit], repo: str) -> bool:
    manifest_names = {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "pyproject.toml", "requirements.txt"}
    for hit in hits:
        if hit.source not in {"precision_search", "local_tool"}:
            continue
        if hit.repo == repo and hit.path.split("/")[-1] in manifest_names:
            return True
    return False


def _repo_has_term_hit(hits: list[RetrievalHit], repo: str, term: str) -> bool:
    if not term:
        return False
    term_lower = term.lower()
    for hit in hits:
        if hit.source not in {"precision_search", "local_tool"}:
            continue
        if hit.repo != repo:
            continue
        if not hit.content:
            continue
        if term_lower in hit.content.lower():
            return True
    return False


def observe_gaps(
    plan: RetrievalPlan,
    hits: list[RetrievalHit],
    ranked_repos: list[dict],
    confirmed_repos: set[str],
) -> list[GapAction]:
    actions: list[GapAction] = []

    if plan.intent != "dependency_relation":
        return actions

    candidate_repos: list[str] = []
    for item in ranked_repos:
        repo = item.get("repo", "")
        if repo and repo in confirmed_repos and repo not in candidate_repos:
            candidate_repos.append(repo)
    hinted = plan.entities.get("entity_hints", {}).get("likely_repo") if isinstance(plan.entities.get("entity_hints"), dict) else None
    if hinted and isinstance(hinted, str) and hinted in confirmed_repos and hinted not in candidate_repos:
        candidate_repos.append(hinted)

    entity_hints = plan.entities.get("entity_hints") if isinstance(plan.entities.get("entity_hints"), dict) else {}
    dependency_term = entity_hints.get("likely_dependency") or plan.entities.get("object")
    if not isinstance(dependency_term, str):
        dependency_term = ""

    for repo in candidate_repos:
        if not _repo_has_manifest_hit(hits, repo):
            actions.append(GapAction("MissingManifest", repo=repo, priority=10))
        if dependency_term and not _repo_has_term_hit(hits, repo, dependency_term):
            actions.append(GapAction("MissingImport", repo=repo, package_name=dependency_term, priority=20))

    seen: set[tuple] = set()
    deduped: list[GapAction] = []
    for action in sorted(actions, key=lambda a: a.priority):
        key = (action.kind, action.repo, action.package_name, action.symbol)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(action)
    return deduped


def select_precision_repos(plan: RetrievalPlan, ranked_repos: list[dict], confirmed_repos: set[str], limit: int = 3) -> list[str]:
    repos: list[str] = []
    hinted = plan.entities.get("entity_hints", {}).get("likely_repo") if isinstance(plan.entities.get("entity_hints"), dict) else None
    if hinted and isinstance(hinted, str) and hinted in confirmed_repos:
        repos.append(hinted)
    for item in ranked_repos:
        repo = item.get("repo", "")
        if repo and repo in confirmed_repos and repo not in repos:
            repos.append(repo)
        if len(repos) >= limit:
            break
    return repos


def _execute_gap_action(action: GapAction, repos_root: str, backends: RetrievalBackends) -> list[RetrievalHit]:
    if not action.repo:
        return []
    try:
        if action.kind == "MissingManifest":
            return backends.read_manifest(repos_root, action.repo)
        elif action.kind == "MissingImport" and action.package_name:
            return backends.local_tool_grep(repos_root, action.repo, pattern=re.escape(action.package_name), max_matches=20)
    except Exception:
        pass
    return []


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

    # Precision search via gap observation
    from .ranking import should_run_precision_search

    if should_run_precision_search(plan, ranked_repos):
        gaps = observe_gaps(plan, all_hits, ranked_repos, confirmed_repos)
        local_actions: list[LocalAction] = []
        precision_hits: list[RetrievalHit] = []

        for gap in gaps:
            if gap.kind == "MissingManifest" and gap.repo:
                local_actions.append(LocalAction("read_manifest", gap.repo))
            elif gap.kind == "MissingImport" and gap.repo and gap.package_name:
                local_actions.append(LocalAction("local_tool_grep", gap.repo, {"pattern": re.escape(gap.package_name)}))
            elif gap.kind == "MissingApiUsage" and gap.repo and gap.symbol:
                local_actions.append(LocalAction("local_tool_grep", gap.repo, {"pattern": re.escape(gap.symbol)}))

        for action in local_actions:
            if action.repo not in confirmed_repos:
                continue
            try:
                if action.tool == "read_manifest":
                    new_hits = backends.read_manifest(repos_root, action.repo)
                elif action.tool == "local_tool_grep":
                    pattern = action.params.get("pattern", "")
                    new_hits = backends.local_tool_grep(repos_root, action.repo, pattern=pattern, max_matches=20)
                else:
                    continue
            except Exception as e:
                notes.append(f"precision error ({action.repo}/{action.tool}): {e}")
                continue
            if new_hits:
                precision_hits.extend(new_hits)

        round1.local_actions = local_actions
        if precision_hits:
            all_hits.extend(precision_hits)
            round1.new_hits = len(all_hits)

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
