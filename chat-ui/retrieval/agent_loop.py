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
    available_repos: list[str] | None = None


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
    candidate_repos: list[str] = field(default_factory=list)


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
GENERIC_PROBE_TERMS = {"config", "global", "node", "uci", "配置"}


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
        if plan.intent == "dependency_relation":
            if dependency_term and not _repo_has_term_hit(hits, repo, dependency_term):
                actions.append(GapAction("MissingImport", repo=repo, package_name=dependency_term, priority=20))
        else:
            raw_terms = plan.entities.get("raw_terms", [])
            if isinstance(raw_terms, list):
                for term in raw_terms[:3]:
                    if isinstance(term, str) and term and not _repo_has_term_hit(hits, repo, term):
                        actions.append(GapAction("MissingTerm", repo=repo, package_name=term, priority=30))
                        break

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


def _candidate_repo_terms(question: str, plan: RetrievalPlan) -> list[str]:
    terms: list[str] = [question]
    for key in ("raw_terms", "search_facets"):
        values = plan.entities.get(key, [])
        if isinstance(values, list):
            terms.extend([value for value in values if isinstance(value, str)])
    for values in plan.queries.values():
        terms.extend([value for value in values if isinstance(value, str)])
    return unique_keep_order(terms)


def _derive_candidate_repos(plan: RetrievalPlan, available_repos: list[str] | None, question: str, limit: int = 5) -> list[str]:
    if not available_repos:
        return []
    candidates: list[str] = []
    hinted = plan.entities.get("repo_candidates", [])
    if isinstance(hinted, list):
        for repo in hinted:
            if isinstance(repo, str) and repo in available_repos and repo not in candidates:
                candidates.append(repo)
                if len(candidates) >= limit:
                    return candidates
    entity_hints = plan.entities.get("entity_hints", {})
    likely_repo = entity_hints.get("likely_repo") if isinstance(entity_hints, dict) else None
    if isinstance(likely_repo, str) and likely_repo in available_repos and likely_repo not in candidates:
        candidates.append(likely_repo)
        if len(candidates) >= limit:
            return candidates

    terms = [term.lower() for term in _candidate_repo_terms(question, plan)]
    for repo in available_repos:
        repo_lower = repo.lower()
        for term in terms:
            if len(term) < 4:
                continue
            if term in repo_lower or repo_lower in term:
                if repo not in candidates:
                    candidates.append(repo)
                break
        if len(candidates) >= limit:
            break
    return candidates


def _probe_pattern(plan: RetrievalPlan, question: str) -> str:
    terms: list[str] = []
    for key in ("search_facets", "raw_terms"):
        values = plan.entities.get(key, [])
        if isinstance(values, list):
            terms.extend([value for value in values if isinstance(value, str)])
    terms.append(question)
    compact = [term for term in unique_keep_order(terms) if term and len(term) >= 2][:10]
    return "|".join(re.escape(term) for term in compact) or re.escape(question)


def _specific_probe_terms(plan: RetrievalPlan, question: str) -> list[str]:
    terms: list[str] = []
    for key in ("search_facets", "raw_terms"):
        values = plan.entities.get(key, [])
        if isinstance(values, list):
            terms.extend([value for value in values if isinstance(value, str)])
    terms.append(question)
    return [
        term
        for term in unique_keep_order(terms)
        if term and len(term) >= 2 and term.lower() not in GENERIC_PROBE_TERMS
    ][:10]


def _probe_hit_has_specific_term(hit: RetrievalHit, specific_terms: list[str]) -> bool:
    haystack = f"{hit.path}\n{hit.content}".lower()
    return any(term.lower() in haystack for term in specific_terms)


def _probe_candidate_repos(
    plan: RetrievalPlan,
    question: str,
    repos_root: str,
    backends: RetrievalBackends,
    candidate_repos: list[str],
    confirmed_repos: set[str],
    probed_repos: set[str],
    limit: int = 3,
) -> tuple[list[RetrievalHit], list[LocalAction]]:
    hits: list[RetrievalHit] = []
    actions: list[LocalAction] = []
    pattern = _probe_pattern(plan, question)
    specific_terms = _specific_probe_terms(plan, question)
    for repo in candidate_repos:
        if repo in confirmed_repos or repo in probed_repos:
            continue
        probed_repos.add(repo)
        action = LocalAction("repo_probe_grep", repo, {"pattern": pattern})
        actions.append(action)
        try:
            probe_hits = backends.local_tool_grep(repos_root, repo, pattern=pattern, max_matches=5)
        except Exception:
            probe_hits = []
        specific_hits = [hit for hit in probe_hits if _probe_hit_has_specific_term(hit, specific_terms)]
        if specific_hits:
            hits.extend(specific_hits)
            confirmed_repos.add(repo)
        if len(actions) >= limit:
            break
    return hits, actions


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


def _build_context_for_llm(merged: list[dict], confirmed_repos: set[str], available_repos: list[str] | None = None, max_items: int = 10) -> str:
    from .ranking import SYNTHETIC_REPOS

    lines: list[str] = []
    real_repos = sorted(confirmed_repos - SYNTHETIC_REPOS)
    if real_repos:
        lines.append(f"已确认仓库（来自检索命中）: {', '.join(real_repos)}")
    else:
        lines.append("(未确认到具体代码仓库)")

    if available_repos:
        other = sorted(set(available_repos) - SYNTHETIC_REPOS - set(real_repos))
        if other:
            lines.append(f"其他可用仓库（可参考试探）: {', '.join(other)}")

    shown = 0
    for item in merged:
        repo = item.get("repo", "")
        if repo in SYNTHETIC_REPOS:
            continue
        path = item.get("path", "")
        line = item.get("line", "")
        content = item.get("content", "")[:200]
        if not content:
            continue
        lines.append(f"- [{repo}] {path}:{line}")
        lines.append(f"  ```\n  {content}\n  ```")
        shown += 1
        if shown >= max_items:
            break

    return "\n".join(lines)


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
    from .ranking import rank_code_repositories, should_run_precision_search

    # 1. Initial Planning (rule-based, no LLM)
    plan = plan_query(question)

    # 2. Query Expansion (based on rule plan only)
    queries = expand_queries(question, plan)

    all_sourcebot_results: list[dict] = []
    all_qdrant_results: list[dict] = []
    all_hits: list[RetrievalHit] = []
    all_merged: list[dict] = []
    ast_facts: list[str] = []
    graph_facts: list[str] = []
    rounds: list[RetrievalRound] = []
    notes: list[str] = []
    seen_sourcebot_queries: set[str] = set()

    confirmed_repos: set[str] = set()
    candidate_repos = _derive_candidate_repos(plan, backends.available_repos, question)
    probed_repos: set[str] = set()
    ranked_repos: list[dict] = []

    prev_hit_count = 0

    for round_idx in range(1, max_rounds + 1):
        round_notes: list[str] = []
        round_record = RetrievalRound(index=round_idx)
        is_first_round = round_idx == 1

        if is_first_round:
            # Round 1: Global search
            sourcebot_queries = [q for q in queries["sourcebot"][:8] if q not in seen_sourcebot_queries]
            for q in sourcebot_queries:
                seen_sourcebot_queries.add(q)
            qdrant_queries = queries["qdrant"][:3]

            round_record.sourcebot_queries = list(sourcebot_queries)
            round_record.qdrant_queries = list(qdrant_queries)

            if use_sourcebot:
                for q in sourcebot_queries:
                    try:
                        results = backends.search_sourcebot(q, 5)
                        all_sourcebot_results.extend(results)
                    except Exception as e:
                        round_notes.append(f"sourcebot error ({q}): {e}")

            if use_qdrant:
                for q in qdrant_queries:
                    try:
                        results = backends.search_qdrant(q, 5)
                        all_qdrant_results.extend(results)
                    except Exception as e:
                        round_notes.append(f"qdrant error ({q}): {e}")

            # Convert results to hits
            src_hits = to_hits(all_sourcebot_results, "sourcebot")
            qdr_hits = to_hits(all_qdrant_results, "qdrant")
            all_hits = list(src_hits) + list(qdr_hits)
            all_merged = merge_results(all_sourcebot_results, all_qdrant_results)
            all_merged = _hydrate_merged_content(all_merged, backends)

            confirmed_repos = confirmed_repos_from_results(all_sourcebot_results + all_qdrant_results)
            ranked_repos = rank_code_repositories(all_hits)

            # AST
            if use_ast and all_merged:
                ast_queries = queries["ast"][:5]
                round_record.ast_queries = ast_queries
                for q in ast_queries:
                    try:
                        facts = backends.search_ast_structure(q, all_merged, limit=8)
                        ast_facts.extend(facts)
                    except Exception as e:
                        round_notes.append(f"ast error: {e}")

            # Graph
            if use_graph and all_merged:
                graph_queries = queries["graph"][:5]
                round_record.graph_queries = graph_queries
                for q in graph_queries:
                    try:
                        facts = backends.search_graph_relations(q, all_merged, limit=12)
                        graph_facts.extend(facts)
                    except Exception as e:
                        round_notes.append(f"graph error: {e}")

            # Convert AST/Graph facts to hits
            for fact in ast_facts:
                all_hits.append(RetrievalHit("ast", "ast-service", "structure", "", fact, "structure"))
            for fact in graph_facts:
                all_hits.append(RetrievalHit("neo4j", "ast-service", "graph", "", fact, "graph"))

            # ── Phase 2: LLM context-driven planning (after Round 1 discovery) ──
            if backends.llm_plan:
                context = _build_context_for_llm(all_merged, confirmed_repos, available_repos=backends.available_repos)
                plan.entities["round1_context"] = context
                try:
                    llm_result = backends.llm_plan(question, plan)
                    if isinstance(llm_result, dict) and llm_result:
                        plan = merge_llm_plan(plan, llm_result)
                        # Re-expand queries based on LLM-enriched plan
                        queries = expand_queries(question, plan)
                        candidate_repos = unique_keep_order(candidate_repos + _derive_candidate_repos(plan, backends.available_repos, question))
                        round_notes.append("llm_plan: context-driven re-plan applied")
                except Exception as e:
                    round_notes.append(f"llm_plan error: {e}")

        round_local_actions: list[LocalAction] = []
        if candidate_repos:
            probe_hits, probe_actions = _probe_candidate_repos(
                plan,
                question,
                repos_root,
                backends,
                candidate_repos,
                confirmed_repos,
                probed_repos,
            )
            round_local_actions.extend(probe_actions)
            if probe_hits:
                all_hits.extend(probe_hits)
                ranked_repos = rank_code_repositories(all_hits)

        # Precision search via gap observation (every round)
        if should_run_precision_search(plan, ranked_repos):
            gaps = observe_gaps(plan, all_hits, ranked_repos, confirmed_repos)
            local_actions: list[LocalAction] = []

            for gap in gaps:
                if gap.kind == "MissingManifest" and gap.repo:
                    local_actions.append(LocalAction("read_manifest", gap.repo))
                elif gap.kind in ("MissingImport", "MissingTerm") and gap.repo and gap.package_name:
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
                    round_notes.append(f"precision error ({action.repo}/{action.tool}): {e}")
                    continue
                if new_hits:
                    all_hits.extend(new_hits)

            round_local_actions.extend(local_actions)

        round_record.local_actions = round_local_actions

        round_record.new_hits = len(all_hits) - prev_hit_count
        round_record.notes = round_notes
        prev_hit_count = len(all_hits)
        rounds.append(round_record)

        # Stop if no new hits this round
        if round_record.new_hits == 0 and not is_first_round:
            break

        # After round 1, follow-up with LLM rewrites + discovered terms
        if is_first_round and round_idx < max_rounds:
            discovered = extract_discovered_terms(all_hits)
            followup_queries: list[str] = []
            for q in queries["sourcebot"]:
                if q not in seen_sourcebot_queries:
                    followup_queries.append(q)
            for t in discovered:
                if t not in seen_sourcebot_queries and t not in followup_queries:
                    followup_queries.append(t)
            if followup_queries and use_sourcebot:
                for q in followup_queries[:8]:
                    seen_sourcebot_queries.add(q)
                    try:
                        results = backends.search_sourcebot(q, 5)
                        all_sourcebot_results.extend(results)
                    except Exception as e:
                        round_notes.append(f"sourcebot follow-up error ({q}): {e}")
                # Rebuild hits and ranking for next round
                src_hits = to_hits(all_sourcebot_results, "sourcebot")
                qdr_hits = to_hits(all_qdrant_results, "qdrant")
                all_hits = list(src_hits) + list(qdr_hits)
                for fact in ast_facts:
                    all_hits.append(RetrievalHit("ast", "ast-service", "structure", "", fact, "structure"))
                for fact in graph_facts:
                    all_hits.append(RetrievalHit("neo4j", "ast-service", "graph", "", fact, "graph"))
                all_merged = merge_results(all_sourcebot_results, all_qdrant_results)
                all_merged = _hydrate_merged_content(all_merged, backends)
                confirmed_repos = confirmed_repos_from_results(all_sourcebot_results + all_qdrant_results)
                ranked_repos = rank_code_repositories(all_hits)

    return RetrievalLoopResult(
        plan=plan,
        hits=all_hits,
        merged=all_merged,
        ast_facts=ast_facts,
        graph_facts=graph_facts,
        ranked_repos=ranked_repos,
        confirmed_repos=confirmed_repos,
        candidate_repos=candidate_repos,
        rounds=rounds,
    )
