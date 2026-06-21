# Retrieval Repo Discovery Design

## Goal

Fix fuzzy Chinese configuration queries that currently lock onto the wrong semantic hit and never discover the correct repository, such as `科学上网的配置是怎样的` needing `passwall-any` instead of only `Xray-core`.

## Problem

The current retrieval loop starts with rule planning. Chinese queries often produce no raw terms because the token extractor is English-oriented, so Sourcebot receives only the full Chinese sentence and AST/Graph receive no useful symbols. The LLM planner runs only after Round 1, so it is biased by whatever Qdrant returned first. Local tools are correctly gated by `confirmed_repos`, but there is no intermediate state for likely repositories that need cheap confirmation.

## Design

The retrieval flow becomes:

```text
Pre-plan facets
→ Global discovery
→ Candidate repo probe / confirmation
→ Local precision research
```

`planner.py` adds deterministic domain facets for common Chinese repo-bot questions. For the PassWall class of query, `科学上网`, `passwall`, `openwrt`, `代理`, `节点`, and `订阅` expand to terms like `passwall`, `openwrt-passwall`, `luci-app-passwall`, `0_default_config`, `subscribe.lua`, `节点`, `订阅`, and `代理`.

Configuration terms are deliberately gated. Generic triggers such as `配置`, `config`, and `uci` do not create facets by themselves because they are too common across unrelated repositories. They only append auxiliary facets such as `config`, `uci`, `global`, and `node` after a PassWall/OpenWrt trigger has already matched. This prevents queries like `数据库连接池怎么配置` from receiving PassWall-specific search vocabulary.

This is query expansion and repo discovery, not a replacement for `classify_query()`. The intent classifier still chooses broad answer templates such as `generic_code_answer`; the facet layer adds domain-specific retrieval vocabulary before the first global search.

When no English tokens are extracted, `entities["subject"]` may become the first deterministic facet, such as `passwall`. This is intentional for fuzzy domain questions: it gives downstream diagnostics a concrete subject without changing the broad intent template. Non-dependency query expansion does not rely on `subject`, so the behavior impact is limited to metadata and evidence context.

`agent_loop.py` adds `candidate_repos` to `RetrievalLoopResult`. Candidate repos come from available repo names matched against the question and expanded facets, plus any future LLM `repo_candidates` output. Candidate repos are not treated as confirmed.

When confirmed evidence is weak or missing for a candidate, the loop runs a bounded probe against a small number of candidate repos. The current probe is a constrained `local_tool_grep(max_matches=5)` using a compact pattern list. If the probe returns content, that repo is promoted to `confirmed_repos` and normal precision search can run through the existing `observe_gaps()` path.

The final local precision rules remain conservative: full `local_tool_*` research still requires `confirmed_repos`. The only new exception is the bounded probe used to confirm a candidate repo.

## Extension Strategy

The first facet set is intentionally small and deterministic because it addresses a known production miss. New domains should be added only when there is a repeatable retrieval failure with a test case. Each addition should define:

- Trigger phrases that appear in user questions.
- Search facets that are specific enough for Sourcebot and AST/Graph.
- Optional qdrant rewrites for semantic recall.
- Optional repo candidates, limited to names that can appear in `available_repos`.

If the facet table grows beyond a few domains, move it out of code into a small configuration file or retrieval knowledge base. Until then, keeping it in `planner.py` makes the behavior testable and easy to review.

## Deterministic and LLM Re-plan Interaction

Deterministic facets run first and seed Round 1. The post-Round-1 LLM planner remains in the loop. It may add:

- `query_rewrites.sourcebot`
- `query_rewrites.qdrant`
- `search_facets`
- `repo_candidates`
- `entity_hints.likely_repo`

Merging is additive and order-preserving. Deterministic values stay first; LLM values are appended if unique. LLM suggestions cannot directly trigger full local precision search. A repo suggested by the LLM must exist in `available_repos`, become a candidate, and pass the bounded probe before it joins `confirmed_repos`.

## Probe Risk Controls

Probe promotion currently uses a low threshold: any probe hit promotes the candidate repo. This is acceptable for the initial PassWall regression because candidates are bounded by `available_repos`, explicit repo hints, and a maximum of three probes per loop.

The main risk is false promotion from generic terms such as `config` or `global`. The current mitigation is that probe patterns are built from ordered facets and capped to a small list; specific terms such as `passwall`, `luci-app-passwall`, `0_default_config`, and `subscribe.lua` appear before generic config terms. Promotion also requires at least one non-generic probe term to appear in the probe hit path or content, so a generic-only match such as `config global node` cannot confirm a repository by itself.

Future hardening can still add:

- A threshold of two independent facet hits before promotion.
- Per-facet weights so `config` cannot promote a repo by itself.
- File-path weighting for known configuration files.

Every probe pattern term is escaped with `re.escape()` before terms are joined with `|`, so facet values containing regex metacharacters cannot inject arbitrary regular expressions.

## Multiple Candidates

Candidate order is deterministic:

1. `RetrievalPlan.entities["repo_candidates"]`
2. `entity_hints.likely_repo`
3. `available_repos` name matches against query terms and facets

The loop probes only a bounded prefix of candidates. If several candidates are confirmed, normal `rank_code_repositories()` decides Evidence Pack order from the accumulated evidence. This keeps conflict resolution consistent with the existing ranking layer, but the ranking may need path/facet weighting if multiple repos produce similarly weak probe hits.

## Data Model

`RetrievalBackends` already exposes:

```python
available_repos: list[str] | None
```

This list is used for candidate derivation and is also passed into `_build_context_for_llm()` so the LLM planner can suggest real repo names.

`RetrievalLoopResult` gains:

```python
candidate_repos: list[str]
```

`RetrievalPlan.entities` may contain:

```python
{
    "search_facets": [...],
    "repo_candidates": [...],
    "entity_hints": {
        "likely_repo": "..."
    }
}
```

The LLM planner JSON contract also accepts top-level `search_facets` and `repo_candidates`, which `merge_llm_plan()` merges into `RetrievalPlan.entities`.

## Testing

Add tests for:

- Chinese PassWall queries produce expanded search facets.
- Available repo matching discovers `passwall-any`.
- A weak first-round semantic hit for `Xray-core` does not prevent probing `passwall-any`.
- Probe hits promote `passwall-any` into `confirmed_repos` and trigger local precision actions.
- No facet match preserves existing behavior.
- Empty probes do not promote candidates.
- `available_repos=None` skips candidate derivation.
- New candidates added by the LLM in Round 1 can be probed once.
- Generic-only probe hits do not promote candidates.
- Probe pattern construction preserves order, caps terms, and escapes regex metacharacters.

## Non-goals

This change does not redesign the final LLM answer tool loop. It fixes the upstream retrieval candidate discovery so the Evidence Pack starts with the right repository.
