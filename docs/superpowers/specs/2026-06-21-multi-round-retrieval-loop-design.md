# Multi-Round Retrieval Loop Design

## Goal

Improve repo-bot's retrieval behavior from a mostly one-shot RAG flow into a controlled multi-round retrieval loop. The loop should let the system decompose natural-language questions into backend-specific queries, execute the right tools in the right order, observe evidence gaps, and perform targeted follow-up retrieval before asking the LLM to synthesize the final answer.

The immediate motivation is dependency and relationship questions such as:

```text
block-proxy 是怎样依赖 anyproxy 的
```

The current implementation can produce a useful answer, but it still has structural problems:

- Sourcebot and Qdrant mostly search the original user prompt instead of planned backend-specific queries.
- The optional LLM planner can generate query rewrites, but those queries are not consistently executed by the main search path.
- Synthetic service repos such as `ast-service` can dominate repository ranking and become precision-search targets.
- Precision search runs against only one top repo, which misses subject/object pairs such as `block-proxy` and `anyproxy`.
- Local tools are available to the final prompt, but there is no real tool execution loop that observes gaps and fetches more evidence.

## Non-Goals

This design does not introduce a full Anthropic tool-calling agent yet. The first version should remain deterministic, bounded, and testable. LLM planning may be used as an optional query expansion input, but code controls which tools run, how often they run, and which repos are eligible for local access.

This design also does not change the final answer synthesizer beyond feeding it better evidence.

## Recommended Approach

Create a shared retrieval orchestrator:

```python
run_retrieval_loop(
    question: str,
    *,
    use_sourcebot: bool = True,
    use_qdrant: bool = True,
    use_ast: bool = True,
    use_graph: bool = True,
    max_rounds: int = 2,
) -> RetrievalLoopResult
```

Both `chat-ui/app.py` and `chat-ui/test_chat.py` should call this function. The UI remains responsible for Streamlit rendering. The CLI remains responsible for debug printing. Retrieval logic should live in `chat-ui/retrieval/agent_loop.py` or a similarly focused module.

The orchestrator should not import Streamlit. It should also avoid creating Anthropic/OpenAI clients directly. Backend functions should be injectable so tests can run without network services:

```python
@dataclass
class RetrievalBackends:
    search_sourcebot: Callable[[str, int], list[dict]]
    search_qdrant: Callable[[str, int], list[dict]]
    search_ast_structure: Callable[[str, list[dict], int], list[str]]
    search_graph_relations: Callable[[str, list[dict], int], list[str]]
    read_file_content: Callable[[str, str, int, int], str]
    llm_plan: Callable[[str, RetrievalPlan], dict[str, Any]] | None = None
```

`app.py` and `test_chat.py` can pass their existing functions into the loop. Unit tests can pass fake backends and assert exact calls.

## Data Model

Add lightweight dataclasses in the retrieval layer:

```python
@dataclass
class RetrievalRound:
    index: int
    sourcebot_queries: list[str]
    qdrant_queries: list[str]
    ast_queries: list[str]
    graph_queries: list[str]
    local_actions: list[dict[str, Any]]
    new_hits: int
    notes: list[str]

@dataclass
class RetrievalLoopResult:
    plan: RetrievalPlan
    hits: list[RetrievalHit]
    merged: list[dict]
    ast_facts: list[str]
    graph_facts: list[str]
    ranked_repos: list[dict]
    rounds: list[RetrievalRound]
```

The final Evidence Pack remains built by `build_evidence_pack(question, plan, hits, ranked_repos)`.

## Loop Flow

The loop should run in bounded phases.

### 1. Initial Planning

Use the existing `plan_query(question)` as the required baseline. If `LLM_PLANNER_ENABLED=true`, call the existing LLM planner and merge valid JSON into the plan.

The plan's query fields must drive actual retrieval. For example, if `plan.queries["sourcebot"]` contains `ProxyServer`, Sourcebot should search `ProxyServer`.

### 2. Query Expansion

Add deterministic query expansion for common code-retrieval patterns.

For dependency questions with subject/object terms:

- Sourcebot queries should include subject, object, package-name variants, `require(...)`, `import ... from`, dependency manifest terms, and known API symbols discovered from earlier evidence.
- Qdrant queries should keep natural-language variants.
- AST and graph queries should focus on symbols and API names, not full prose.

Queries must be deduplicated globally across rounds.

### 3. Global Search Round

Run Sourcebot and Qdrant across the planned query sets. Each backend should cap query count and per-query result count to avoid runaway retrieval.

Suggested first-version limits:

- Sourcebot: up to 8 queries per round, 5 results per query.
- Qdrant: up to 3 queries per round, 5 results per query.
- Max rounds: 2 by default, 3 only if explicitly configured.

Sourcebot errors should be collected as notes, not fatal unless every backend fails.

Results must be deduplicated before ranking and evidence building. The primary identity should be `(source, repo, path, line_range, content[:200])` for typed hits and `(repo, path, line)` for merged UI results. Repeated queries should not inflate repository ranking.

### 4. Snippet Hydration

For merged global results without content, read file snippets with the existing `read_file_content` equivalent. This helper should move into the shared retrieval module or be injected from app/CLI to avoid duplication.

### 5. Repository Ranking

Rank only real code repositories for precision-search targeting. Synthetic evidence sources such as `ast-service` must not become precision targets.

Implementation options:

- Update `rank_repositories()` to skip known synthetic repos.
- Or add `rank_code_repositories()` for local tool eligibility.

The first version should prefer an explicit skip set:

```python
SYNTHETIC_REPOS = {"ast-service"}
```

Ranking should still keep AST/Graph facts in evidence; it should only exclude synthetic repos from code-repo selection.

### 6. AST and Graph Search

Run AST and Graph queries after there are candidate code repos. They should use symbols from:

- Original plan terms.
- Sourcebot snippets.
- Qdrant snippets.
- LLM planner `likely_api_symbols`, if present.

AST/Graph facts should be stored as evidence, but they should not override real repo ranking.

### 7. Evidence Gap Observation

Add a small rule-based observer that inspects `plan`, `hits`, and ranked real repos.

For `dependency_relation`, the observer should ask:

- Do we have a manifest or lockfile hit in the subject repo?
- Do we have an import/require hit in the subject repo?
- Do we have a manifest hit in the object repo if an object repo is known?
- Do we have runtime API usage, such as `ProxyServer` or `certMgr`, when symbols are discovered?
- Are subject and object repos both represented when both terms look like repo names?

The observer outputs targeted local actions and follow-up exact queries. It should not output prose for the final user.

### 8. Precision Search

Precision search should run against multiple eligible code repos, not only the top repo.

For dependency questions, eligible repos should include:

- The subject repo if found or inferred from a ranked repo name.
- The object repo if found or inferred from a ranked repo name.
- The top 1-2 real ranked repos.

Local actions should be constrained:

- `read_manifest(repo)` for dependency questions.
- `local_tool_list(repo, "")` for repo structure when manifest exists or repo confidence is high.
- `local_tool_grep(repo, pattern)` for package names, imports, API symbols.
- `local_tool_read(repo, path)` only for files already found by Sourcebot/local grep/manifest/list.

No local tool should run for a repo that is not present in ranked real repos, candidate repos, or confirmed global-search results.

### 9. Second Round

If the observer finds gaps and new queries/actions are available, run one additional round. The second round should prioritize:

- Missing manifest reads.
- Missing import/require grep.
- Missing package-name definition.
- API-symbol grep/read from files discovered in round 1.

If a round produces no new hits or no new queries/actions, stop early.

### 10. Final Evidence Pack

Build the Evidence Pack from all hits. The final LLM should receive:

- Strong local/Sourcebot evidence first.
- Supporting AST/Graph facts.
- Qdrant results only as weak locator evidence unless corroborated.
- `repo_roots` from real candidate repos.
- Round metadata only for debug UI/CLI, not as final answer content.

If needed, add a small evidence ordering helper so `precision_search` and `local_tool` results appear before weak Qdrant locators. This keeps the final LLM focused on verifiable file content.

## Error Handling

The loop should degrade gracefully:

- If LLM planner fails, continue with rule planning.
- If Sourcebot fails, continue with Qdrant/local actions only if repos are already known.
- If Qdrant fails, continue with Sourcebot and precision search.
- If AST/Graph fail, continue with text and local evidence.
- If local tools fail for a repo, record a note and continue other repos.

Failures should be visible in `RetrievalRound.notes` for CLI/UI diagnostics.

## Testing Strategy

Add unit tests before implementation.

Required tests:

- LLM planner query rewrites are executed by the retrieval loop.
- Sourcebot receives expanded exact queries, not only the original prompt.
- Multi-query results are deduplicated before repository ranking.
- Synthetic repos such as `ast-service` are excluded from precision-search targets.
- Dependency questions read manifests for subject/object candidate repos.
- Local tools do not run unless the repo has been confirmed by global results or ranking.
- The retrieval loop accepts fake backend functions and does not require Streamlit or live network services in unit tests.
- The loop deduplicates queries across rounds.
- The loop stops after `max_rounds`.
- If no new queries/actions are produced, the loop stops early.

Existing tests for prompt synthesis, evidence pack generation, precision tools, and ranking should remain passing.

## Implementation Plan Preview

Implementation should be staged:

1. Add tests for ranking and query expansion.
2. Add query expansion helpers.
3. Add real-code repo ranking or precision-target selection.
4. Add `RetrievalLoopResult` and `run_retrieval_loop`.
5. Update `test_chat.py` to use the loop and print round diagnostics.
6. Update `app.py` to use the loop while preserving existing UI controls.
7. Run `python3 -m pytest chat-ui/tests -q`.
8. Run a manual CLI check for the dependency example.
