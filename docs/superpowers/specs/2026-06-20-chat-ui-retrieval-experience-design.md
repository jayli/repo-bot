# Chat UI Retrieval Experience Design

## Summary

Improve repo-bot's Chat UI from a one-pass mixed retrieval flow into a structured code search workflow:

```text
user query
  -> hybrid query planner
  -> Sourcebot / Qdrant / AST / Neo4j global retrieval
  -> repository and symbol ranking
  -> bounded precision search inside selected repositories
  -> evidence pack
  -> template-driven answer synthesis
```

The goal is to answer repository search questions with higher precision, stronger evidence, and consistent output. For example, a query such as `block-proxy 是怎样依赖 anyproxy 的` should identify the likely repository, prove the dependency through manifest/import/call evidence, inspect the most relevant files, and produce a concise answer with file and line citations.

## Goals

- Route common code search questions to appropriate answer templates.
- Keep answer output predictable while allowing different templates for different question types.
- Use Sourcebot, Qdrant, AST, Neo4j, and local precision search according to their strengths.
- Introduce a structured evidence pack so the LLM sees ranked, typed evidence instead of an unordered list of snippets.
- Add bounded repository-local precision search for complex questions.
- Use a hybrid planner: deterministic rules as the stable default, optional LLM planning as an enhancer for complex or ambiguous questions.
- Preserve graceful degradation when one retrieval backend is unavailable.

## Non-Goals

- Do not turn Chat UI into an unrestricted autonomous agent.
- Do not allow arbitrary shell execution from the LLM.
- Do not perform unbounded full-repository reads during chat requests.
- Do not replace Sourcebot, Qdrant, AST, or Neo4j; this design orchestrates them.
- Do not require perfect type-aware call graph resolution.
- Do not implement automatic code modification as part of search answering.

## Existing Context

The current `chat-ui/app.py` flow is:

```text
user prompt
  -> search_sourcebot()
  -> search_qdrant()
  -> merge_results()
  -> read_file_content()
  -> search_ast_structure()
  -> search_graph_relations()
  -> ask_llm()
```

This already integrates four retrieval sources:

- Sourcebot for exact code search.
- Qdrant for semantic code chunk retrieval.
- AST service for symbols, imports, and calls.
- Neo4j graph relations through `ast-service`.

The current weakness is not lack of retrieval systems. The weakness is orchestration: the LLM receives a flat context and has no explicit contract for intent, evidence quality, repository selection, precision search, or answer shape.

## Recommended Approach

Use a hybrid planner:

```text
Rule Planner
  -> optional LLM Planner enhancement
  -> retrieval execution
  -> evidence pack construction
  -> answer synthesis
```

The Rule Planner is the source of stable behavior. It classifies the query, extracts obvious entities, creates baseline search queries, chooses an answer template, and decides whether precision search is needed.

The LLM Planner may enrich complex plans with query rewrites, likely symbol names, and precision search hints. It must not answer the user. It must not directly execute tools. Its output is advisory and must be validated before execution.

## Query Classification

The first implementation should support these high-value intents:

| Intent | Typical Questions | Template |
|---|---|---|
| `dependency_relation` | `A 是怎样依赖 B 的`, `哪个仓库引入了 X`, `A 和 B 什么关系` | dependency relation |
| `call_chain` | `A 怎么调用到 B`, `参数怎么传进去`, `请求链路是什么` | call chain / data flow |
| `implementation_location` | `登录逻辑在哪里`, `哪个文件实现了 X` | implementation location |
| `troubleshooting` | `为什么没结果`, `这个报错怎么修` | troubleshooting |
| `generic_code_answer` | Other code understanding questions | concise evidence-backed answer |

Additional intents can be added later:

- `symbol_explanation`
- `impact_analysis`
- `proposal`
- `comparison`
- `architecture_overview`
- `insufficient_evidence`

## Rule Planner

The Rule Planner should be deterministic and testable. It should emit a plan like:

```json
{
  "intent": "dependency_relation",
  "template": "dependency_relation",
  "entities": {
    "raw_terms": ["block-proxy", "anyproxy"],
    "subject": "block-proxy",
    "object": "anyproxy",
    "symbols": ["anyproxy"]
  },
  "queries": {
    "sourcebot": ["anyproxy"],
    "qdrant": ["block-proxy 是怎样依赖 anyproxy 的"],
    "ast": ["anyproxy"],
    "graph": ["anyproxy"]
  },
  "precision": {
    "enabled": true,
    "patterns": ["anyproxy"],
    "read_manifests": true
  }
}
```

Classification can start with Chinese keyword rules:

- `怎样依赖`, `依赖`, `引入`, `使用了` -> `dependency_relation`
- `调用链`, `怎么调用`, `传到哪里`, `流程` -> `call_chain`
- `在哪里`, `哪个文件`, `实现位置` -> `implementation_location`
- `为什么`, `报错`, `没结果`, `怎么修` -> `troubleshooting`

Entity extraction can start with:

- English, numeric, underscore, dot, slash, colon, at-sign, and hyphen tokens.
- camelCase, PascalCase, and snake_case symbols.
- npm-style package names such as `@scope/pkg`.
- Repository-like names such as `block-proxy`.

## LLM Planner Enhancement

The LLM Planner is optional. It should be invoked for complex or ambiguous queries.

Trigger conditions:

- Intent is `dependency_relation`, `call_chain`, `troubleshooting`, or later `impact_analysis`.
- The query contains multiple entities and the rule planner cannot confidently assign roles.
- The first retrieval pass is weak: Sourcebot is empty, AST is empty, or no repository clearly wins.
- The user asks for a relationship, path, reason, or impact rather than a simple location.

The LLM Planner returns JSON only:

```json
{
  "query_rewrites": {
    "sourcebot": [
      "\"anyproxy\"",
      "ProxyServer",
      "require(\"anyproxy\")",
      "import anyproxy"
    ],
    "qdrant": [
      "proxy server startup anyproxy",
      "block proxy wrapper around anyproxy"
    ]
  },
  "entity_hints": {
    "likely_repo": "block-proxy",
    "likely_dependency": "anyproxy",
    "likely_api_symbols": ["ProxyServer"]
  },
  "precision_search": {
    "extra_patterns": ["ProxyServer", "proxyServer", "rule", "options"],
    "important_files": ["package.json", "src/index.*", "src/**/proxy*"]
  }
}
```

Merge rules:

- The Rule Planner's `intent` and `template` should not be replaced by the LLM Planner unless explicit validation is added later.
- LLM-generated queries and patterns are additive.
- Invalid JSON, timeout, or unsafe output falls back to the Rule Planner.
- The executor must bound query counts, pattern counts, and read windows.

## Global Retrieval

Retrieval execution should run the selected backends and normalize their outputs.

Normalized hit shape:

```json
{
  "source": "sourcebot",
  "repo": "block-proxy",
  "path": "src/proxy/server.js",
  "line_range": "L3-L12",
  "matched_query": "anyproxy",
  "content": "...",
  "strength": "exact_text"
}
```

Source-specific strengths:

- Sourcebot: `exact_text`
- Qdrant: `semantic`
- AST: `structure`
- Neo4j: `graph`
- Precision search: `file_confirmed`

## Repository And Symbol Ranking

After global retrieval, rank candidate repositories and symbols before precision search.

Initial repository scoring:

```text
+10 Sourcebot exact import/require/package hit
+8  AST import/call hit
+7  Sourcebot exact ordinary hit
+6  Neo4j graph relation hit
+4  Qdrant high-score semantic hit
+2  Qdrant lower-score semantic hit
-5  README/docs-only hit unless the question asks about docs
```

Precision search should run when:

- Intent is complex: dependency relation, call chain, troubleshooting, or impact analysis.
- A top repository is available and needs stronger proof.
- Sourcebot or AST found a strong clue but the chain is incomplete.
- The user asks "怎样", "为什么", "影响哪里", or "链路".

Precision search may be skipped when:

- The user only asks where something is implemented.
- There is already a clear location answer.
- No repository can be selected safely.

## Precision Search

Precision search is bounded repository-local search. It should expose a small internal API:

```text
read_manifest(repo)
grep_repo(repo, pattern, include_globs, exclude_globs)
read_file_window(repo, path, start_line, end_line)
```

First implementation should avoid arbitrary LLM-selected shell commands. All paths must be resolved under `REPOS_ROOT`.

For dependency questions:

```text
1. read package manifests and lockfiles when present.
2. grep selected repositories for the dependency entity and likely API symbols.
3. read bounded windows around import/require/call matches.
4. read likely entry files when manifest or retrieval results identify them.
```

Example precision plan:

```json
{
  "repo": "block-proxy",
  "actions": [
    {
      "type": "read_manifest",
      "files": ["package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"]
    },
    {
      "type": "grep_repo",
      "pattern": "anyproxy|ProxyServer",
      "include": ["*.js", "*.ts", "*.json"]
    },
    {
      "type": "read_file_window",
      "path": "src/proxy/server.js",
      "start_line": 1,
      "end_line": 100
    }
  ]
}
```

## Evidence Pack

The Evidence Pack is the only structured context the Answer Synthesizer should see.

Shape:

```json
{
  "query": "block-proxy 是怎样依赖 anyproxy 的",
  "intent": "dependency_relation",
  "answer_template": "dependency_relation",
  "entities": {
    "subject": "block-proxy",
    "object": "anyproxy"
  },
  "candidate_repos": [
    {
      "repo": "block-proxy",
      "score": 31,
      "selected": true,
      "reasons": ["exact dependency mention", "AST import", "semantic proxy startup"]
    }
  ],
  "evidence": [
    {
      "id": "E1",
      "tier": "strong",
      "source": "precision_search",
      "repo": "block-proxy",
      "path": "package.json",
      "line_range": "L18-L24",
      "claim": "declares anyproxy dependency",
      "content": "..."
    }
  ],
  "retrieval_coverage": {
    "sourcebot": {"used": true, "summary": "命中 anyproxy 和 ProxyServer"},
    "qdrant": {"used": true, "summary": "命中代理启动语义相关片段"},
    "ast": {"used": true, "summary": "命中 import/call 结构事实"},
    "neo4j": {"used": true, "summary": "命中入口到代理服务的调用链"},
    "precision_search": {"used": true, "summary": "读取 package.json 和代理启动文件确认依赖方式"}
  },
  "known_gaps": ["未读取 lockfile，无法确认实际安装版本"]
}
```

Evidence tiers:

| Tier | Sources |
|---|---|
| `strong` | precision file content, Sourcebot exact code hit, AST import/call/definition with location |
| `supporting` | Neo4j graph relation, AST fallback symbol, Sourcebot ordinary text hit |
| `weak` | Qdrant semantic recall, docs-only hit unless the question asks about docs |

Confidence rules:

- High: at least two strong evidence items from different evidence layers, such as manifest plus import/call.
- Medium: at least one strong evidence item plus supporting evidence.
- Low: only supporting or weak evidence.
- Unconfirmed: no strong evidence for a question that asks to prove dependency, call chain, or impact.

## Answer Templates

All complex answers should start with the answer, not retrieval logs.

Default complex shape:

~~~md
## 结论

...

## 链路 / 分析

...

## 关键证据

| 层级 | 位置 | 说明 |
|---|---|---|

## 检索覆盖

- Sourcebot: ...
- Qdrant: ...
- AST: ...
- Neo4j: ...
- 精搜: ...

## 不确定性

...
~~~

Simple answers can be compressed:

~~~md
## 结论

...

## 证据

- `repo/path:Lx-Ly`: ...
~~~

### Dependency Relation Template

~~~md
## 结论

`subject` 对 `object` 是直接运行时依赖 / 间接运行时依赖 / 开发或测试依赖 / 配置或文档引用 / 未确认依赖 / 无依赖证据。
置信度：高 / 中 / 低。
原因：...

## 依赖链路

```text
manifest
 -> import/require
 -> runtime call or instantiation
 -> entrypoint or config flow
```

## 关键证据

| 层级 | 位置 | 说明 |
|---|---|---|

## 代码行为说明

解释 subject 如何使用 object，以及 object 在系统中承担的角色。

## 检索覆盖

- Sourcebot: ...
- Qdrant: ...
- AST: ...
- Neo4j: ...
- 精搜: ...

## 不确定性

...

## 下一步

...
~~~

### Call Chain Template

~~~md
## 结论

调用链是：`A -> B -> C`。
置信度：...

## 调用链

```text
A repo/path:Lx
 -> B repo/path:Lx
 -> C repo/path:Lx
```

## 分段说明

1. 入口层：...
2. 编排层：...
3. 核心逻辑：...
4. 外部调用或副作用：...

## 关键证据

| 节点 | 位置 | 作用 |
|---|---|---|
~~~

### Implementation Location Template

~~~md
## 结论

相关实现主要在 `repo/path`。
核心入口是 `function/class/config`。

## 文件地图

| 文件 | 作用 | 关键位置 |
|---|---|---|

## 阅读顺序

1. 先看 `...`
2. 再看 `...`
3. 最后看 `...`
~~~

### Troubleshooting Template

~~~md
## 结论

最可能原因是：...
置信度：高 / 中 / 低。

## 证据

| 现象 | 代码位置 | 推断 |
|---|---|---|

## 排查路径

1. 检查配置：...
2. 检查请求：...
3. 检查数据：...
4. 检查降级逻辑：...

## 修复建议

- 最小修复：...
- 更稳妥修复：...
- 需要验证：...
~~~

## Prompt Layers

Prompt composition should be explicit:

```text
System Base Prompt
  -> Retrieval Source Policy
  -> Evidence Rules
  -> Template Prompt
  -> Evidence Pack
  -> User Question
```

System base:

```text
你是 repo-bot 的代码检索分析助手。你的任务是基于本地代码仓库检索结果回答问题。
你必须使用中文回答。
你必须优先依据提供的代码、结构索引、调用图和精搜结果。
不要编造不存在的文件、函数、调用链、依赖关系或版本号。
复杂问题先给结论，再给证据。
所有关键判断必须引用 repo/path:Lx 或 repo/path:Lx-Ly。
如果证据不足，明确说证据不足，并列出还需要检索什么。
```

Retrieval source policy:

```text
Sourcebot 代表精确关键词、正则、文件内容命中。
Qdrant 代表语义相关代码片段，不能单独证明直接依赖或调用关系。
AST 代表结构化符号、定义、调用、import/require 信息。
Neo4j 代表从 AST 派生出的图关系，需要尽量结合文件内容确认。
精搜代表在候选仓库内进一步 read_file/grep 得到的高置信证据。
```

Evidence rules:

```text
强证据优先级：精搜文件内容 > Sourcebot 精确命中 > AST 结构事实 > Neo4j 调用图 > Qdrant 语义召回。
不要把多个仓库的同名文件混为一谈。
如果 repo 名称、包名、符号名可能多义，必须指出。
引用格式统一为 `repo/path:Lx` 或 `repo/path:Lx-Ly`。
```

## Proposed Chat UI Layout

Implementation should eventually move retrieval logic out of the large Streamlit file:

```text
chat-ui/
  app.py
  retrieval/
    planner.py
    sourcebot.py
    qdrant.py
    ast_client.py
    graph_client.py
    ranking.py
    precision.py
    evidence.py
  prompts/
    base.py
    templates.py
    synthesizer.py
```

The first implementation can be incremental and does not need to complete this entire layout at once. However, new code should avoid growing `app.py` further where possible.

## Minimal Implementation Plan

The first shippable version should:

1. Add `classify_query()` and a Rule Planner.
2. Add optional LLM Planner enhancement behind a timeout and JSON validation.
3. Normalize current Sourcebot, Qdrant, AST, and Neo4j results into typed hits.
4. Rank candidate repositories and symbols.
5. Add bounded precision search for selected repositories:
   - `read_manifest`
   - `grep_repo`
   - `read_file_window`
6. Build an Evidence Pack.
7. Replace flat `ctx_json` answer input with Evidence Pack input.
8. Add dependency relation, call chain, implementation location, and troubleshooting templates.
9. Keep the old answer path as a fallback while the new flow stabilizes.

## Testing Strategy

Unit tests:

- Query classification for representative Chinese and mixed Chinese/English queries.
- Entity extraction for repo names, package names, and symbols.
- LLM Planner JSON validation and fallback behavior.
- Repository ranking scoring.
- Precision search path safety under `REPOS_ROOT`.
- Evidence tier assignment and confidence calculation.
- Prompt/template selection.

Integration tests:

- Sourcebot failure should not break Qdrant/AST/Neo4j-backed answers.
- AST service failure should degrade with clear retrieval coverage.
- Dependency query with manifest and import evidence should produce high confidence.
- Semantic-only query should not produce high confidence for direct dependency claims.

Manual checks:

- Ask `block-proxy 是怎样依赖 anyproxy 的`.
- Ask an implementation location question.
- Ask a call chain question.
- Ask a question with no strong evidence and confirm the answer says evidence is insufficient.

## Open Questions

- Whether LLM Planner should run before global retrieval, after weak first-pass retrieval, or both.
- Whether precision search results should be shown in the UI as a separate expander.
- How many files and line windows are acceptable per request before latency becomes too high.
- Whether call-chain answers should prefer Neo4j path output or source-file-confirmed paths when they disagree.
