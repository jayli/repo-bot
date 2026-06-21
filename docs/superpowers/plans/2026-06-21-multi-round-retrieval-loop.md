# Multi-Round Retrieval Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a bounded multi-round retrieval loop that turns natural-language code questions into backend-specific searches, follows evidence gaps with targeted local tools, and feeds stronger Evidence Packs to the final LLM answer.

**Architecture:** Add a shared retrieval orchestrator under `chat-ui/retrieval/agent_loop.py` with injectable backend functions so Streamlit, CLI, and tests use one retrieval path. Keep planning deterministic and bounded: rule planner first, optional LLM query hints, deduped global searches, real-repo ranking, gap observation, local precision search, and a final evidence result.

**Tech Stack:** Python 3, dataclasses, pytest, existing `retrieval.*` modules, Streamlit app integration, existing Sourcebot/Qdrant/AST/Neo4j helper functions.

---

## Current Context

Relevant spec:

- `docs/superpowers/specs/2026-06-21-multi-round-retrieval-loop-design.md`

Important existing files:

- `chat-ui/retrieval/planner.py`: rule planner, LLM plan validation/merge helpers.
- `chat-ui/retrieval/ranking.py`: repo ranking and precision-search gate.
- `chat-ui/retrieval/precision.py`: local tools and manifest readers.
- `chat-ui/retrieval/evidence.py`: Evidence Pack construction.
- `chat-ui/app.py`: Streamlit UI and current inline retrieval path.
- `chat-ui/test_chat.py`: CLI test path and duplicated retrieval logic.
- `chat-ui/tests/*`: pytest suite.

Workspace caution:

- There may be unrelated or previous-turn edits in `chat-ui/examples/question1.md`, `chat-ui/prompts/templates.py`, `chat-ui/retrieval/evidence.py`, `chat-ui/tests/test_evidence.py`, and `chat-ui/tests/test_synthesizer.py`.
- Do not revert those changes. Work with them if they affect tests.

## File Structure

Create:

- `chat-ui/retrieval/agent_loop.py`
  - Owns `RetrievalBackends`, `LocalAction`, `GapAction`, `RetrievalRound`, `RetrievalLoopResult`.
  - Owns query expansion, dedupe helpers, confirmed repo tracking, precision target selection, gap observation, discovered-term extraction, and `run_retrieval_loop`.
  - Must not import Streamlit.
  - Must not construct Anthropic/OpenAI clients.
  - Must not read `REPOS_ROOT` directly; callers pass `repos_root`.

- `chat-ui/tests/test_agent_loop.py`
  - Unit tests with fake backend functions.
  - Verifies planner rewrites, query expansion, dedupe, real-repo targeting, local gating, max rounds, and early stop.

Modify:

- `chat-ui/retrieval/ranking.py`
  - Define the shared `SYNTHETIC_REPOS` set.
  - Add a way to rank/select real code repos without letting synthetic repos such as `ast-service` become precision targets.

- `chat-ui/retrieval/planner.py`
  - Preserve LLM planner `entity_hints` in `RetrievalPlan.entities` so the loop can use `likely_repo` only after confirmation.

- `chat-ui/app.py`
  - Replace inline retrieval orchestration with `run_retrieval_loop`.
  - Preserve existing sidebar toggles and display expanders.

- `chat-ui/test_chat.py`
  - Replace duplicated retrieval orchestration with `run_retrieval_loop`.
  - Print round diagnostics from `RetrievalLoopResult.rounds`.

Possibly modify:

- `chat-ui/retrieval/evidence.py`
  - Import shared `SYNTHETIC_REPOS` instead of hardcoding synthetic repo filtering.
  - Only add evidence ordering if final evidence quality still needs it after loop integration.

## Task 1: Add Real Repo Ranking Guard

**Files:**

- Modify: `chat-ui/retrieval/ranking.py`
- Modify: `chat-ui/retrieval/evidence.py`
- Test: `chat-ui/tests/test_ranking.py`
- Test: `chat-ui/tests/test_evidence.py`

- [ ] **Step 1: Write failing tests for synthetic repo exclusion**

Add tests similar to:

```python
def test_rank_code_repositories_excludes_synthetic_repos():
    models = load_module("retrieval.models")
    ranking = load_ranking()
    hits = [
        models.RetrievalHit("ast", "ast-service", "structure", "", "block-proxy/proxy/proxy.js:L1", "structure"),
        models.RetrievalHit("sourcebot", "block-proxy", "proxy/proxy.js", "L1", "require('@bachi/anyproxy')", "exact_text"),
    ]

    ranked = ranking.rank_code_repositories(hits)

    assert [item["repo"] for item in ranked] == ["block-proxy"]
```

Also add:

```python
def test_rank_repositories_keeps_existing_behavior_for_all_evidence():
    ...
    ranked = ranking.rank_repositories(hits)
    assert ranked[0]["repo"] == "ast-service"
```

This preserves existing all-evidence ranking while adding a code-repo ranking path.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m pytest chat-ui/tests/test_ranking.py -q
```

Expected: fails because `rank_code_repositories` does not exist.

- [ ] **Step 3: Implement minimal ranking guard**

In `chat-ui/retrieval/ranking.py`, add:

```python
SYNTHETIC_REPOS = {"ast-service"}

def rank_code_repositories(hits: list[RetrievalHit], synthetic_repos: set[str] | None = None) -> list[dict]:
    blocked = synthetic_repos or SYNTHETIC_REPOS
    return rank_repositories([hit for hit in hits if hit.repo not in blocked])
```

Keep `rank_repositories()` unchanged unless tests require a tiny shared helper.

- [ ] **Step 4: Reuse `SYNTHETIC_REPOS` in Evidence Pack roots**

In `chat-ui/retrieval/evidence.py`, replace any hardcoded synthetic repo skip such as `{"ast-service"}` with:

```python
from .ranking import SYNTHETIC_REPOS
```

Keep behavior the same: synthetic repos should not appear in `repo_roots`.

No new evidence test is required unless behavior changes; this step primarily verifies the existing `test_evidence.py` still passes with the shared constant.

- [ ] **Step 5: Run ranking and evidence tests**

Run:

```bash
python3 -m pytest chat-ui/tests/test_ranking.py chat-ui/tests/test_evidence.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add chat-ui/retrieval/ranking.py chat-ui/retrieval/evidence.py chat-ui/tests/test_ranking.py chat-ui/tests/test_evidence.py
git commit -m "fix(retrieval): exclude synthetic repos from precision ranking"
```

## Task 2: Add Query Expansion And Dedupe Helpers

**Files:**

- Create: `chat-ui/retrieval/agent_loop.py`
- Modify: `chat-ui/retrieval/planner.py`
- Test: `chat-ui/tests/test_agent_loop.py`
- Test: `chat-ui/tests/test_planner.py`

- [ ] **Step 1: Write failing test that LLM entity hints are preserved**

In `chat-ui/tests/test_planner.py`, add:

```python
def test_merge_llm_plan_preserves_entity_hints():
    planner = load_planner()
    base = planner.plan_query("koa 是怎样依赖 koa-router 的")

    merged = planner.merge_llm_plan(base, {"entity_hints": {"likely_repo": "koa", "likely_dependency": "koa-router"}})

    assert merged.entities["entity_hints"]["likely_repo"] == "koa"
    assert merged.entities["entity_hints"]["likely_dependency"] == "koa-router"
```

- [ ] **Step 2: Run planner test and verify failure**

Run:

```bash
python3 -m pytest chat-ui/tests/test_planner.py::test_merge_llm_plan_preserves_entity_hints -q
```

Expected: fails because `merge_llm_plan()` currently drops `entity_hints`.

- [ ] **Step 3: Implement entity hint preservation**

In `chat-ui/retrieval/planner.py`, update `merge_llm_plan()`:

```python
entities = dict(base.entities)
entity_hints = llm_plan.get("entity_hints")
if isinstance(entity_hints, dict):
    entities["entity_hints"] = entity_hints
...
return replace(base, queries=queries, precision=precision, entities=entities)
```

- [ ] **Step 4: Write failing tests for dependency query expansion**

Create `chat-ui/tests/test_agent_loop.py` with the same dynamic import style used by other tests. Add:

```python
def test_expand_queries_for_dependency_relation_adds_backend_specific_sourcebot_terms():
    models = load_module("retrieval.models")
    agent_loop = load_module("retrieval.agent_loop")
    plan = models.RetrievalPlan(
        "dependency_relation",
        "dependency_relation",
        entities={"subject": "block-proxy", "object": "anyproxy", "raw_terms": ["block-proxy", "anyproxy"]},
        queries={"sourcebot": ["block-proxy", "anyproxy"], "qdrant": ["block-proxy 是怎样依赖 anyproxy 的"], "ast": [], "graph": []},
        precision={"enabled": True, "patterns": ["block-proxy", "anyproxy"], "read_manifests": True},
    )

    queries = agent_loop.expand_queries("block-proxy 是怎样依赖 anyproxy 的", plan)

    assert "require('anyproxy')" in queries["sourcebot"]
    assert "require(\"anyproxy\")" in queries["sourcebot"]
    assert "from 'anyproxy'" in queries["sourcebot"]
    assert "dependencies" in queries["sourcebot"]
    assert "block-proxy 是怎样依赖 anyproxy 的" in queries["qdrant"]
```

- [ ] **Step 5: Write failing tests for ordered dedupe**

Add:

```python
def test_unique_keep_order_dedupes_without_reordering():
    agent_loop = load_module("retrieval.agent_loop")
    assert agent_loop.unique_keep_order(["a", "b", "a", "", "c"]) == ["a", "b", "c"]
```

- [ ] **Step 6: Run new tests and verify failure**

Run:

```bash
python3 -m pytest chat-ui/tests/test_planner.py chat-ui/tests/test_agent_loop.py -q
```

Expected: fails because `retrieval/agent_loop.py` does not exist.

- [ ] **Step 7: Implement minimal helpers and dataclasses**

In `chat-ui/retrieval/agent_loop.py`, add:

```python
from __future__ import annotations

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
```

Then implement `expand_queries(question, plan, discovered_terms=None)` with minimal dependency-specific exact patterns.

Note: `read_file_content(repo, path, start_line, end_line)` intentionally differs from the precision local tools because the current UI/CLI helper closes over `REPOS_ROOT`. The orchestrator passes `repos_root` explicitly only to precision local tools such as `read_manifest`, `local_tool_grep`, `local_tool_list`, and `local_tool_read`.

- [ ] **Step 8: Run new tests**

Run:

```bash
python3 -m pytest chat-ui/tests/test_planner.py chat-ui/tests/test_agent_loop.py -q
```

Expected: pass.

- [ ] **Step 9: Commit**

```bash
git add chat-ui/retrieval/planner.py chat-ui/retrieval/agent_loop.py chat-ui/tests/test_planner.py chat-ui/tests/test_agent_loop.py
git commit -m "feat(retrieval): add retrieval loop query expansion helpers"
```

## Task 3: Add Backend-Driven Global Search Loop

**Files:**

- Modify: `chat-ui/retrieval/agent_loop.py`
- Test: `chat-ui/tests/test_agent_loop.py`

- [ ] **Step 1: Write failing test that LLM planner rewrites are executed**

Add fake backends:

```python
class FakeBackends:
    def __init__(self):
        self.sourcebot_queries = []
        self.qdrant_queries = []

    def search_sourcebot(self, query, top_k):
        self.sourcebot_queries.append(query)
        return []

    def search_qdrant(self, query, top_k):
        self.qdrant_queries.append(query)
        return []

    def search_ast_structure(self, query, results, limit):
        return []

    def search_graph_relations(self, query, results, limit):
        return []

    def read_file_content(self, repo, path, start_line, end_line):
        return ""

    def read_manifest(self, repos_root, repo):
        return []

    def local_tool_list(self, *args, **kwargs):
        return []

    def local_tool_grep(self, *args, **kwargs):
        return []

    def local_tool_read(self, *args, **kwargs):
        return None

    def llm_plan(self, question, plan):
        return getattr(self, "llm_plan_result", {"query_rewrites": {"sourcebot": ["ProxyServer"], "qdrant": ["MITM proxy engine"]}})
```

Test:

```python
def test_run_retrieval_loop_executes_llm_plan_rewrites():
    agent_loop = load_module("retrieval.agent_loop")
    fake = FakeBackends()
    backends = agent_loop.RetrievalBackends(
        fake.search_sourcebot,
        fake.search_qdrant,
        fake.search_ast_structure,
        fake.search_graph_relations,
        fake.read_file_content,
        fake.read_manifest,
        fake.local_tool_list,
        fake.local_tool_grep,
        fake.local_tool_read,
        fake.llm_plan,
    )

    agent_loop.run_retrieval_loop("block-proxy 是怎样依赖 anyproxy 的", repos_root="/tmp/repos", backends=backends, max_rounds=1)

    assert "ProxyServer" in fake.sourcebot_queries
    assert "MITM proxy engine" in fake.qdrant_queries
```

- [ ] **Step 2: Write failing test for not only original prompt**

Assert Sourcebot receives more than the original prompt for dependency questions:

```python
def test_run_retrieval_loop_searches_expanded_sourcebot_queries():
    ...
    agent_loop.run_retrieval_loop("block-proxy 是怎样依赖 anyproxy 的", repos_root="/tmp/repos", backends=backends, max_rounds=1)
    assert "block-proxy 是怎样依赖 anyproxy 的" not in fake.sourcebot_queries
    assert "anyproxy" in fake.sourcebot_queries
    assert "require('anyproxy')" in fake.sourcebot_queries
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```bash
python3 -m pytest chat-ui/tests/test_agent_loop.py -q
```

Expected: fails because `run_retrieval_loop` is not implemented.

- [ ] **Step 4: Implement minimal global search loop**

In `agent_loop.py`:

- Import `plan_query`, `merge_llm_plan`, `rank_code_repositories`, and app-equivalent `to_hits` / `merge_results` logic.
- Implement local helper `to_hits(results, source)`.
- Implement local helper `merge_results(src, qdr, top_k=15)`.
- In `run_retrieval_loop`, do:
  - `plan = plan_query(question)`
  - if `backends.llm_plan`, merge its dict into plan
  - expand queries
  - execute up to 8 Sourcebot queries and 3 Qdrant queries
  - append `RetrievalRound`
  - dedupe hits and merged results
  - hydrate missing merged content through `backends.read_file_content`
  - derive `confirmed_repos` from real Sourcebot/Qdrant result repo names after excluding `SYNTHETIC_REPOS`
  - rank with `rank_code_repositories`
  - return `RetrievalLoopResult`

- [ ] **Step 5: Run tests**

Run:

```bash
python3 -m pytest chat-ui/tests/test_agent_loop.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add chat-ui/retrieval/agent_loop.py chat-ui/tests/test_agent_loop.py
git commit -m "feat(retrieval): execute planned global search queries"
```

## Task 4: Add Gap Observation, Precision Target Selection, And Local Tool Gating

**Files:**

- Modify: `chat-ui/retrieval/agent_loop.py`
- Test: `chat-ui/tests/test_agent_loop.py`

- [ ] **Step 1: Write failing test that raw subject/object tokens do not trigger local tools**

Use a fake LLM plan hinting at a repo that is not confirmed:

```python
def test_likely_repo_hint_does_not_trigger_local_tools_until_confirmed():
    agent_loop = load_module("retrieval.agent_loop")
    fake = FakeBackends()
    fake.llm_plan_result = {"entity_hints": {"likely_repo": "koa"}}
    backends = make_backends(agent_loop, fake)

    result = agent_loop.run_retrieval_loop("koa 是怎样依赖 koa-router 的", repos_root="/tmp/repos", backends=backends, max_rounds=1)

    actions = [action for r in result.rounds for action in r.local_actions]
    assert result.confirmed_repos == set()
    assert actions == []
```

- [ ] **Step 2: Write failing test for confirmed likely repo precision target**

Add a fake backend returning Sourcebot hits in both repos:

```python
def search_sourcebot(self, query, top_k):
    self.sourcebot_queries.append(query)
    if query == "anyproxy":
        return [{"repo": "anyproxy", "path": "package.json", "line": "L1", "start_line": 1, "end_line": 5, "content": "{\"name\":\"@bachi/anyproxy\"}"}]
    if query == "block-proxy":
        return [{"repo": "block-proxy", "path": "proxy/proxy.js", "line": "L1", "start_line": 1, "end_line": 3, "content": "const AnyProxy = require('@bachi/anyproxy')"}]
    return []
```

Test:

```python
def test_precision_targets_include_confirmed_likely_repos():
    ...
    result = agent_loop.run_retrieval_loop("block-proxy 是怎样依赖 anyproxy 的", repos_root="/tmp/repos", backends=backends, max_rounds=1)
    actions = [action for r in result.rounds for action in r.local_actions]
    assert agent_loop.LocalAction("read_manifest", "block-proxy") in actions
    assert agent_loop.LocalAction("read_manifest", "anyproxy") in actions
```

- [ ] **Step 3: Write failing test that synthetic repo is not a precision target**

Fake Sourcebot returns no real repo, AST returns `ast-service` facts. Assert no local actions target `ast-service`.

- [ ] **Step 4: Write failing tests for gap observer actions**

Add pure-function tests:

```python
def test_observe_gaps_emits_missing_manifest_for_confirmed_repo():
    ...
    actions = agent_loop.observe_gaps(plan, hits=[], ranked_repos=[{"repo": "block-proxy", "score": 10}], confirmed_repos={"block-proxy"})
    assert agent_loop.GapAction("MissingManifest", repo="block-proxy", priority=10) in actions

def test_observe_gaps_stops_emitting_manifest_after_hit_exists():
    ...
    hits = [models.RetrievalHit("precision_search", "block-proxy", "package.json", "L1-L10", "{}", "file_confirmed")]
    actions = agent_loop.observe_gaps(plan, hits=hits, ranked_repos=[{"repo": "block-proxy", "score": 10}], confirmed_repos={"block-proxy"})
    assert all(action.kind != "MissingManifest" for action in actions)
```

- [ ] **Step 5: Run tests and verify failure**

Run:

```bash
python3 -m pytest chat-ui/tests/test_agent_loop.py -q
```

Expected: fails because gap observation and local precision actions are not implemented.

- [ ] **Step 6: Implement confirmed repo extraction**

In `agent_loop.py`, add:

```python
def confirmed_repos_from_results(results: list[dict], synthetic_repos: set[str] | None = None) -> set[str]:
    blocked = synthetic_repos or SYNTHETIC_REPOS
    return {item["repo"] for item in results if item.get("repo") and item.get("repo") not in blocked}
```

`confirmed_repos` means real repo names observed in global Sourcebot/Qdrant results. It does not include raw `subject`, raw `object`, or unconfirmed LLM hints.

- [ ] **Step 7: Implement gap observer**

In `agent_loop.py`, add:

```python
def observe_gaps(plan: RetrievalPlan, hits: list[RetrievalHit], ranked_repos: list[dict], confirmed_repos: set[str]) -> list[GapAction]:
    ...
```

Rules:

- Candidate repos are confirmed ranked repos plus confirmed `entity_hints.likely_repo`.
- For `dependency_relation`, resolve `dependency_term = plan.entities["entity_hints"]["likely_dependency"]` when present, otherwise `plan.entities["object"]`.
- For dependency queries, emit `GapAction("MissingManifest", repo=repo, priority=10)` when no `package.json`, lockfile, `pyproject.toml`, or `requirements.txt` hit exists for that repo.
- Emit `GapAction("MissingImport", repo=repo, package_name=dependency_term, priority=20)` when no strong hit in that repo contains the dependency/package term.
- Sort by priority and dedupe `(kind, repo, package_name, symbol)`.

- [ ] **Step 8: Implement precision target selection**

In `agent_loop.py`, add:

```python
def select_precision_repos(plan: RetrievalPlan, ranked_repos: list[dict], confirmed_repos: set[str], limit: int = 3) -> list[str]:
    ...
```

Rules:

- Include `plan.entities["entity_hints"]["likely_repo"]` only if it is in `confirmed_repos`.
- Add top ranked real repos until limit.
- Deduplicate.

- [ ] **Step 9: Implement local precision execution**

In `run_retrieval_loop`, after global search/ranking:

- If `should_run_precision_search(plan, ranked_repos)`, run `observe_gaps(...)`.
- Convert gap actions into `LocalAction` records:
  - `MissingManifest` -> `LocalAction("read_manifest", repo)`
  - `MissingImport` -> `LocalAction("local_tool_grep", repo, {"pattern": package_name, ...})`
  - `MissingApiUsage` -> `LocalAction("local_tool_grep", repo, {"pattern": symbol, ...})`
- Execute only actions whose repo is in `confirmed_repos`.
- For dependency questions, optionally call `local_tool_list(repos_root, repo, dir_path="", exclude=[...], max_entries=100)` after a manifest read succeeds or the repo has high rank.
- For up to 3 files per repo from Sourcebot/local hits, call `local_tool_read(...)`.
- Append `LocalAction` records to the current `RetrievalRound.local_actions`.
- Catch exceptions and append notes.

Use the explicit `repos_root` parameter passed to `run_retrieval_loop()`.

- [ ] **Step 10: Run tests**

Run:

```bash
python3 -m pytest chat-ui/tests/test_agent_loop.py -q
```

Expected: pass.

- [ ] **Step 11: Commit**

```bash
git add chat-ui/retrieval/agent_loop.py chat-ui/tests/test_agent_loop.py
git commit -m "feat(retrieval): target local precision search by confirmed repos"
```

## Task 5: Add AST/Graph Integration And Loop Stop Rules

**Files:**

- Modify: `chat-ui/retrieval/agent_loop.py`
- Test: `chat-ui/tests/test_agent_loop.py`

- [ ] **Step 1: Write failing tests for discovered term extraction**

Add:

```python
def test_extract_discovered_terms_from_strong_hits():
    models = load_module("retrieval.models")
    agent_loop = load_module("retrieval.agent_loop")
    hits = [
        models.RetrievalHit("sourcebot", "block-proxy", "proxy/proxy.js", "L1", "const AnyProxy = require('@bachi/anyproxy'); new AnyProxy.ProxyServer(options);", "exact_text"),
        models.RetrievalHit("qdrant", "block-proxy", "README.md", "L1", "semantic mention of ignoredTerm", "semantic"),
    ]

    terms = agent_loop.extract_discovered_terms(hits)

    assert "@bachi/anyproxy" in terms
    assert "ProxyServer" in terms
    assert "ignoredTerm" not in terms
```

- [ ] **Step 2: Write failing test for AST/Graph backend calls**

Test that AST/Graph receive symbol queries derived from terms and only run when enabled:

```python
def test_run_retrieval_loop_calls_ast_and_graph_with_candidate_symbols():
    ...
    result = agent_loop.run_retrieval_loop("block-proxy 是怎样依赖 anyproxy 的", backends=backends, max_rounds=1, use_ast=True, use_graph=True)
    assert fake.ast_queries
    assert fake.graph_queries
    assert any(hit.source == "ast" for hit in result.hits)
    assert any(hit.source == "neo4j" for hit in result.hits)
```

- [ ] **Step 3: Write failing tests for max rounds and early stop**

Add:

```python
def test_run_retrieval_loop_stops_at_max_rounds():
    ...
    result = agent_loop.run_retrieval_loop("block-proxy 是怎样依赖 anyproxy 的", backends=backends, max_rounds=2)
    assert len(result.rounds) <= 2

def test_run_retrieval_loop_stops_early_when_no_new_work():
    ...
    result = agent_loop.run_retrieval_loop("登录逻辑在哪里", backends=backends, max_rounds=3)
    assert len(result.rounds) == 1
```

- [ ] **Step 4: Run tests and verify failure**

Run:

```bash
python3 -m pytest chat-ui/tests/test_agent_loop.py -q
```

Expected: fails because discovered-term extraction, AST/Graph, and loop-stop behavior are incomplete.

- [ ] **Step 5: Implement discovered-term extraction**

In `agent_loop.py`, add:

```python
def extract_discovered_terms(hits: list[RetrievalHit]) -> list[str]:
    ...
```

Rules:

- Inspect only strong text hits: `source in {"sourcebot", "precision_search", "local_tool"}` and non-empty content.
- Extract package names from `require("...")`, `require('...')`, `from "..."`, `from '...'`, and `import ... from "..."`.
- Extract dependency keys from manifest snippets when the key looks like a package name.
- Extract API-looking identifiers used with imported package aliases, such as `ProxyServer` and `certMgr`.
- Deduplicate with `unique_keep_order`.

- [ ] **Step 6: Implement AST/Graph calls**

In `run_retrieval_loop`:

- After merged results are hydrated, call `backends.search_ast_structure(query, merged, limit=8)` for relevant AST queries if `use_ast`.
- Call `backends.search_graph_relations(query, merged, limit=12)` for relevant graph queries if `use_graph`.
- Convert returned facts into `RetrievalHit("ast", "ast-service", "structure", "", fact, "structure")` and `RetrievalHit("neo4j", "ast-service", "graph", "", fact, "graph")`.
- Store facts in result fields.

- [ ] **Step 7: Implement bounded second round**

Keep the first implementation simple:

- Round 1 runs expanded plan queries.
- After precision search, call `extract_discovered_terms(hits)`.
- In each follow-up round, execute local gap actions first, then run unseen Sourcebot queries produced from discovered terms. This favors precise local evidence once repos are confirmed while still allowing global search to discover related repos/files.
- If `max_rounds > 1` and new discovered terms produce unseen Sourcebot queries, run one more bounded global batch after local actions.
- Stop if no unseen queries or no new hits.

- [ ] **Step 8: Run tests**

Run:

```bash
python3 -m pytest chat-ui/tests/test_agent_loop.py -q
```

Expected: pass.

- [ ] **Step 9: Commit**

```bash
git add chat-ui/retrieval/agent_loop.py chat-ui/tests/test_agent_loop.py
git commit -m "feat(retrieval): add bounded follow-up retrieval rounds"
```

## Task 6: Migrate CLI To Shared Retrieval Loop

**Files:**

- Modify: `chat-ui/test_chat.py`
- Test: no dedicated unit test; verify with existing test suite and manual CLI when services are available.

- [ ] **Step 1: Refactor imports**

Import from `retrieval.agent_loop`:

```python
from retrieval.agent_loop import RetrievalBackends, run_retrieval_loop
```

Keep existing backend helper functions such as `search_sourcebot`, `search_qdrant`, `search_ast_structure`, `search_graph_relations`, and `read_file_content`.

- [ ] **Step 2: Add CLI LLM planner adapter**

Extract the current optional LLM planner block into:

```python
def llm_plan_query(question: str, plan) -> dict:
    ...
```

Return `{}` on failure. Reuse current prompt and `validate_llm_plan`.

- [ ] **Step 3: Replace inline retrieval path**

In `main()`, replace steps 1-8 retrieval orchestration with:

```python
backends = RetrievalBackends(
    search_sourcebot=search_sourcebot,
    search_qdrant=search_qdrant,
    search_ast_structure=search_ast_structure,
    search_graph_relations=search_graph_relations,
    read_file_content=read_file_content,
    read_manifest=read_manifest,
    local_tool_list=local_tool_list,
    local_tool_grep=local_tool_grep,
    local_tool_read=local_tool_read,
    llm_plan=llm_plan_query if USE_LLM_PLANNER else None,
)
result = run_retrieval_loop(question, repos_root=REPOS_ROOT, backends=backends)
```

Then:

```python
plan = result.plan
hits = result.hits
merged = result.merged
ast_facts = result.ast_facts
graph_facts = result.graph_facts
ranked_repos = result.ranked_repos
confirmed_repos = result.confirmed_repos
```

Build evidence with existing `build_evidence_pack`.

- [ ] **Step 4: Remove duplicated retrieval helpers that moved into `agent_loop.py`**

After CLI uses `run_retrieval_loop`, delete local copies of generic orchestration helpers that are no longer used, such as duplicated `to_hits()` and `merge_results()`. Keep backend-specific functions that the CLI still passes into `RetrievalBackends`.

- [ ] **Step 5: Print round diagnostics**

Add concise output:

```python
for round_info in result.rounds:
    print(f"  Round {round_info.index}: Sourcebot={len(round_info.sourcebot_queries)} Qdrant={len(round_info.qdrant_queries)} Local={len(round_info.local_actions)} NewHits={round_info.new_hits}")
```

Print notes if present.

- [ ] **Step 6: Run tests**

Run:

```bash
python3 -m pytest chat-ui/tests -q
```

Expected: pass.

- [ ] **Step 7: Optional manual CLI check**

Run only if local services and env are available:

```bash
cd chat-ui
python3 test_chat.py "block-proxy 是怎样依赖 anyproxy 的"
```

Expected:

- Sourcebot query count reflects expanded queries.
- Top repos do not show `ast-service` as precision target.
- Precision count is greater than 0 when local repos exist.

- [ ] **Step 8: Commit**

```bash
git add chat-ui/test_chat.py
git commit -m "refactor(chat-ui): use shared retrieval loop in CLI"
```

## Task 7: Migrate Streamlit App To Shared Retrieval Loop

**Files:**

- Modify: `chat-ui/app.py`
- Test: existing pytest suite; manual app run if feasible.

- [ ] **Step 1: Refactor imports**

Import:

```python
from retrieval.agent_loop import RetrievalBackends, run_retrieval_loop
```

Remove retrieval orchestration imports that become unused, but keep backend helper functions used by adapters.

- [ ] **Step 2: Add Streamlit LLM planner adapter**

Extract current optional LLM planner block into:

```python
def llm_plan_query(question: str, plan) -> dict:
    ...
```

Return `{}` on failure. Do not call Streamlit inside the orchestrator.

- [ ] **Step 3: Replace inline retrieval orchestration**

Inside the chat handler, replace the block from `plan = plan_query(prompt)` through precision search with:

```python
backends = RetrievalBackends(
    search_sourcebot=search_sourcebot,
    search_qdrant=search_qdrant,
    search_ast_structure=search_ast_structure,
    search_graph_relations=search_graph_relations,
    read_file_content=read_file_content,
    read_manifest=read_manifest,
    local_tool_list=local_tool_list,
    local_tool_grep=local_tool_grep,
    local_tool_read=local_tool_read,
    llm_plan=llm_plan_query if USE_LLM_PLANNER else None,
)
result = run_retrieval_loop(
    prompt,
    repos_root=os.environ.get("REPOS_ROOT", "/repos"),
    backends=backends,
    use_sourcebot=use_sourcebot,
    use_qdrant=use_qdrant,
    use_ast=use_ast,
    use_graph=use_graph,
)
```

Then set local variables from `result`.

- [ ] **Step 4: Remove duplicated retrieval helpers that moved into `agent_loop.py`**

After Streamlit uses `run_retrieval_loop`, delete app-local copies of generic orchestration helpers that are no longer used, such as duplicated `to_hits()` and `merge_results()`. Keep backend-specific wrappers needed by `RetrievalBackends`.

- [ ] **Step 5: Preserve existing expanders**

Keep the current UI sections:

- merged result expander
- AST structure expander
- Neo4j graph expander
- Evidence Pack expander

Add optional compact round diagnostics expander:

```python
with st.expander(f"检索轮次 {len(result.rounds)}", expanded=False):
    ...
```

- [ ] **Step 6: Run tests**

Run:

```bash
python3 -m pytest chat-ui/tests -q
```

Expected: pass.

- [ ] **Step 7: Optional Streamlit smoke check**

If services are running:

```bash
npm run dev
```

Open the displayed local URL and ask:

```text
block-proxy 是怎样依赖 anyproxy 的
```

Expected:

- Response uses evidence from `block-proxy` and `anyproxy`.
- Evidence Pack contains `repo_roots`.
- Precision search does not target `ast-service`.

- [ ] **Step 8: Commit**

```bash
git add chat-ui/app.py
git commit -m "refactor(chat-ui): use shared retrieval loop in Streamlit"
```

## Task 8: Final Verification And Cleanup

**Files:**

- Review: all modified files.
- Possibly modify: `chat-ui/retrieval/evidence.py` only if evidence ordering is still poor.

- [ ] **Step 1: Run full chat-ui pytest suite**

Run:

```bash
python3 -m pytest chat-ui/tests -q
```

Expected: all tests pass.

- [ ] **Step 2: Inspect git diff**

Run:

```bash
git diff --stat
git diff -- chat-ui/retrieval/agent_loop.py chat-ui/retrieval/ranking.py chat-ui/test_chat.py chat-ui/app.py
```

Expected:

- No accidental changes to `chat-ui/examples/question1.md`.
- No Streamlit import in `chat-ui/retrieval/agent_loop.py`.
- No direct Anthropic/OpenAI client construction in `agent_loop.py`.

- [ ] **Step 3: Run manual CLI check if services are available**

Run:

```bash
cd chat-ui
python3 test_chat.py "block-proxy 是怎样依赖 anyproxy 的"
```

Expected:

- Retrieval round diagnostics appear.
- Expanded Sourcebot queries are visible in counts or debug output.
- Top precision repos are real code repos.
- Final answer is at least as good as the current example and cites concrete files/lines.

- [ ] **Step 4: Commit any final cleanup**

If there are final cleanup changes:

```bash
git add <files>
git commit -m "test(retrieval): verify multi-round retrieval loop"
```

- [ ] **Step 5: Final status report**

Report:

- commits created
- tests run and results
- manual CLI/app checks run or skipped with reason
- remaining known limitations
