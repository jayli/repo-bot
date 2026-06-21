# Retrieval Repo Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe repo-discovery stage so fuzzy Chinese configuration questions can discover and confirm repositories like `passwall-any` before local precision search.

**Architecture:** Extend the existing retrieval loop without replacing it. `planner.py` generates deterministic facets and possible repo hints; `agent_loop.py` tracks candidate repos, probes them with bounded local actions, and promotes only repos with probe evidence into `confirmed_repos`.

**Tech Stack:** Python, pytest, existing chat-ui retrieval dataclasses and fake backends.

**Current Status:** Implemented in the working tree. The checkboxes below are marked complete to reflect the current state after implementation and verification.

---

### Task 1: Planner Facets

**Files:**
- Modify: `chat-ui/retrieval/planner.py`
- Test: `chat-ui/tests/test_planner.py`

- [x] **Step 1: Write failing tests**

Add tests asserting `plan_query("科学上网的配置是怎样的")` includes `passwall`, `luci-app-passwall`, `0_default_config`, `subscribe.lua`, `节点`, and `订阅` in sourcebot queries or entities.

- [x] **Step 2: Verify red**

Run:

```bash
python3 -m pytest chat-ui/tests/test_planner.py -q
```

Expected: new test fails because facets are missing.

- [x] **Step 3: Implement facets**

Add a small synonym/facet mapper in `planner.py` and merge facets into `raw_terms`, `sourcebot`, `qdrant`, `precision.patterns`, `search_facets`, and `repo_candidates` where appropriate.

- [x] **Step 4: Verify green**

Run the same planner tests and expect pass.

### Task 2: LLM Planner Merge Contract

**Files:**
- Modify: `chat-ui/retrieval/planner.py`
- Modify: `chat-ui/app.py`
- Modify: `chat-ui/test_chat.py`
- Test: `chat-ui/tests/test_planner.py`

- [x] **Step 1: Add LLM fields to merge logic**

Make `merge_llm_plan()` accept top-level `repo_candidates` and `search_facets`, merging them additively into `RetrievalPlan.entities` without replacing deterministic values.

- [x] **Step 2: Expose fields in LLM planner prompt**

Update the JSON output shape in both planner adapters so the LLM knows it may return `search_facets` and `repo_candidates`:

- `chat-ui/app.py`, inside the `st.chat_input` branch where `client.messages.create(... messages=[...])` builds the planner prompt.
- `chat-ui/test_chat.py`, inside `llm_plan_query()` where the CLI planner prompt is built.

- [x] **Step 3: Verify planner tests**

```bash
python3 -m pytest chat-ui/tests/test_planner.py -q
```

Expected: planner tests pass.

### Task 3: Candidate Repo Derivation

**Files:**
- Modify: `chat-ui/retrieval/agent_loop.py`
- Test: `chat-ui/tests/test_agent_loop.py`

- [x] **Step 1: Write failing tests**

Add a fake backend scenario where Qdrant returns `Xray-core`, `available_repos` includes `passwall-any`, and `local_tool_grep` returns a probe hit for `passwall-any`. Assert the result includes `passwall-any` in `candidate_repos` and `confirmed_repos`.

- [x] **Step 2: Verify red**

Run:

```bash
python3 -m pytest chat-ui/tests/test_agent_loop.py -q
```

Expected: new test fails because `candidate_repos` and probe promotion do not exist.

- [x] **Step 3: Implement candidate terms**

Add `_candidate_repo_terms()` to collect the question, `raw_terms`, `search_facets`, and planned queries as matching input.

- [x] **Step 4: Implement candidate derivation**

Add `_derive_candidate_repos()` to prioritize explicit `repo_candidates`, then `entity_hints.likely_repo`, then name matches from `available_repos`.

- [x] **Step 5: Implement probe pattern**

Add `_probe_pattern()` to build an escaped, capped regex from facets and raw terms.

- [x] **Step 6: Implement candidate probe**

Add `_probe_candidate_repos()` to run bounded probe grep, append probe hits, and promote only repos with probe evidence.

- [x] **Step 7: Wire candidate/probe logic into the loop**

Add `candidate_repos` to `RetrievalLoopResult`, derive candidates from available repo names and plan hints, run bounded probe actions, append probe hits, and recompute ranking/confirmation before normal gap observation.

- [x] **Step 8: Verify green**

Run the agent loop tests and expect pass.

### Task 4: LLM Context Dependency

**Files:**
- Existing dependency: `chat-ui/retrieval/agent_loop.py`

- [x] **Step 1: Confirm available repo context exists**

Verify `_build_context_for_llm()` accepts `available_repos` and includes other available repositories in the LLM planner context.

- [x] **Step 2: Confirm loop passes available repos**

Verify `run_retrieval_loop()` calls `_build_context_for_llm(..., available_repos=backends.available_repos)`.

### Task 5: Regression Verification

**Files:**
- Test: `chat-ui/tests`

- [x] **Step 1: Run full chat-ui test suite**

```bash
python3 -m pytest chat-ui/tests -q
```

Expected: all tests pass.

- [x] **Step 2: Inspect diff**

```bash
git diff -- chat-ui/retrieval/planner.py chat-ui/retrieval/agent_loop.py chat-ui/tests/test_planner.py chat-ui/tests/test_agent_loop.py docs/superpowers
```

Expected: changes are scoped to retrieval discovery and docs.

### Task 6: Additional Test Coverage To Add Next

**Files:**
- Modify: `chat-ui/tests/test_planner.py`
- Modify: `chat-ui/tests/test_agent_loop.py`

- [x] **Step 1: Add no-facet regression**

Assert an unrelated Chinese query that does not match `DOMAIN_FACETS` keeps existing fallback behavior and does not create `repo_candidates`.

- [x] **Step 2: Add empty-probe regression**

Assert a candidate repo with no probe hits is not promoted to `confirmed_repos`.

- [x] **Step 3: Add no-available-repos regression**

Assert `available_repos=None` skips candidate derivation and probe execution.

- [x] **Step 4: Add multi-candidate regression**

Assert multiple probe-hit repos can be confirmed, and ranking still controls final repo ordering.

- [x] **Step 5: Add generic-term false-positive regression**

Add a test asserting a generic-only match such as `config` does not promote a repo unless accompanied by a specific facet.

- [x] **Step 6: Add Round 2 candidate regression**

Assert a new repo candidate returned by post-Round-1 LLM planning is probed once and not re-probed in later rounds because `probed_repos` is shared across the loop.

- [x] **Step 7: Add `_probe_pattern()` unit coverage**

Assert `_probe_pattern()` preserves facet order, filters terms shorter than two characters, caps to 10 terms, and escapes regex metacharacters before joining with `|`.

### Task 7: Probe Hardening

**Files:**
- Modify: `chat-ui/retrieval/agent_loop.py`
- Test: `chat-ui/tests/test_agent_loop.py`

- [x] **Step 1: Define facet specificity**

Classify probe terms as specific or generic. For the current PassWall domain, specific terms include `passwall`, `openwrt-passwall`, `luci-app-passwall`, `0_default_config`, and `subscribe.lua`; generic terms include `config`, `global`, and `node`.

- [x] **Step 2: Add failing generic-only promotion test**

Assert a candidate repo hit containing only generic terms is not promoted to `confirmed_repos`.

- [x] **Step 3: Implement minimal hardening**

Require at least one specific facet hit before promoting a candidate repo. Keep the existing bounded probe behavior and avoid adding a scoring subsystem until multiple domains need it.

- [x] **Step 4: Verify targeted and full tests**

```bash
python3 -m pytest chat-ui/tests/test_agent_loop.py -q
python3 -m pytest chat-ui/tests -q
```
