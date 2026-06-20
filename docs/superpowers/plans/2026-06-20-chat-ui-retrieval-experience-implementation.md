# Chat UI Retrieval Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Chat UI retrieval from flat mixed snippets to a hybrid-planned, evidence-packed, template-driven code search workflow.

**Architecture:** Add focused `chat-ui/retrieval/` and `chat-ui/prompts/` modules while keeping Streamlit UI behavior in `app.py`. Start with a deterministic Rule Planner and bounded precision search, then add optional LLM Planner enhancement and Evidence Pack synthesis. Keep the old flat answer path as a fallback until the new path is verified.

**Tech Stack:** Python 3, Streamlit, requests, qdrant-client, OpenAI embeddings client, Anthropic client, pytest, existing Sourcebot and ast-service APIs.

---

## File Structure

Create focused modules instead of growing `chat-ui/app.py`:

```text
chat-ui/
  retrieval/
    __init__.py
    models.py          # dataclasses and typed dict-like objects for plans, hits, evidence
    planner.py         # deterministic query classification and optional LLM plan validation helpers
    ranking.py         # repository/symbol ranking and precision-search decision
    precision.py       # safe manifest, grep, and file-window reads under REPOS_ROOT
    evidence.py        # normalized hits -> Evidence Pack, tiers, confidence
  prompts/
    __init__.py
    templates.py       # output templates and prompt fragments
    synthesizer.py     # Evidence Pack -> Anthropic messages/system prompt
  tests/
    test_planner.py
    test_ranking.py
    test_precision.py
    test_evidence.py
    test_synthesizer.py
```

Modify existing files:

- `chat-ui/app.py`: call the new retrieval pipeline, render Evidence Pack coverage, and keep fallback path.
- `chat-ui/sourcebot_client.py`: leave behavior unchanged unless a small adapter helper is needed.
- `chat-ui/Dockerfile`: update the final `COPY` line so `retrieval/` and `prompts/` are included in the image.

Do not modify `ast-service` for this phase.

## Task 1: Add Retrieval Data Models

**Files:**
- Create: `chat-ui/retrieval/__init__.py`
- Create: `chat-ui/retrieval/models.py`
- Test: `chat-ui/tests/test_evidence.py`

- [ ] **Step 1: Write failing model serialization test**

Create `chat-ui/tests/test_evidence.py` with the local import helper pattern used by `test_sourcebot_client.py`:

```python
import importlib.util
from pathlib import Path
import sys


def load_module(name):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(name, root / (name.replace(".", "/") + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_retrieval_hit_to_dict_keeps_location():
    models = load_module("retrieval.models")
    hit = models.RetrievalHit(
        source="sourcebot",
        repo="block-proxy",
        path="src/proxy/server.js",
        line_range="L3-L12",
        content="const anyproxy = require('anyproxy')",
        strength="exact_text",
    )

    assert hit.to_dict()["repo"] == "block-proxy"
    assert hit.to_dict()["line_range"] == "L3-L12"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd chat-ui && python -m pytest tests/test_evidence.py::test_retrieval_hit_to_dict_keeps_location -v`

Expected: FAIL because `retrieval/models.py` does not exist.

- [ ] **Step 3: Implement minimal models**

Create `chat-ui/retrieval/__init__.py` empty.

Create `chat-ui/retrieval/models.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RetrievalPlan:
    intent: str
    template: str
    entities: dict[str, Any] = field(default_factory=dict)
    queries: dict[str, list[Any]] = field(default_factory=dict)
    precision: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalHit:
    source: str
    repo: str
    path: str
    line_range: str
    content: str = ""
    strength: str = ""
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceItem:
    id: str
    tier: str
    source: str
    repo: str
    path: str
    line_range: str
    claim: str
    content: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

- [ ] **Step 4: Run model test**

Run: `cd chat-ui && python -m pytest tests/test_evidence.py::test_retrieval_hit_to_dict_keeps_location -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add chat-ui/retrieval/__init__.py chat-ui/retrieval/models.py chat-ui/tests/test_evidence.py
git commit -m "feat(chat-ui): add retrieval data models"
```

## Task 2: Implement Rule Planner

**Files:**
- Create: `chat-ui/retrieval/planner.py`
- Test: `chat-ui/tests/test_planner.py`

- [ ] **Step 1: Write failing planner tests**

Create `chat-ui/tests/test_planner.py`:

```python
import importlib.util
from pathlib import Path
import sys


def load_planner():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("retrieval.planner", root / "retrieval/planner.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_dependency_query_extracts_subject_and_object():
    planner = load_planner()

    plan = planner.plan_query("block-proxy 是怎样依赖 anyproxy 的")

    assert plan.intent == "dependency_relation"
    assert plan.template == "dependency_relation"
    assert plan.entities["subject"] == "block-proxy"
    assert plan.entities["object"] == "anyproxy"
    assert "anyproxy" in plan.queries["sourcebot"]
    assert plan.precision["enabled"] is True


def test_location_query_skips_precision_by_default():
    planner = load_planner()

    plan = planner.plan_query("登录逻辑在哪里")

    assert plan.intent == "implementation_location"
    assert plan.precision["enabled"] is False
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd chat-ui && python -m pytest tests/test_planner.py -v`

Expected: FAIL because `planner.py` does not exist.

- [ ] **Step 3: Implement rule planner**

Create `chat-ui/retrieval/planner.py`:

```python
from __future__ import annotations

import re

from .models import RetrievalPlan


TOKEN_RE = re.compile(r"@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|[A-Za-z0-9_./:-]*[A-Za-z][A-Za-z0-9_./:-]*")


def extract_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in TOKEN_RE.findall(query):
        if len(token) < 2:
            continue
        if token not in terms:
            terms.append(token)
    return terms


def classify_query(query: str) -> str:
    if any(word in query for word in ["怎样依赖", "依赖", "引入", "使用了", "什么关系"]):
        return "dependency_relation"
    if any(word in query for word in ["调用链", "怎么调用", "传到哪里", "流程"]):
        return "call_chain"
    if any(word in query for word in ["在哪里", "哪个文件", "实现位置"]):
        return "implementation_location"
    if any(word in query for word in ["为什么", "报错", "没结果", "怎么修"]):
        return "troubleshooting"
    return "generic_code_answer"


def plan_query(query: str) -> RetrievalPlan:
    intent = classify_query(query)
    terms = extract_terms(query)
    entities: dict[str, object] = {"raw_terms": terms, "symbols": terms}
    if intent == "dependency_relation" and len(terms) >= 2:
        entities["subject"] = terms[0]
        entities["object"] = terms[1]
    elif terms:
        entities["subject"] = terms[0]

    sourcebot_queries = terms[:5] or [query]
    qdrant_queries = [query]
    ast_queries = terms[:5]
    graph_queries = terms[:5]
    precision_enabled = intent in {"dependency_relation", "call_chain", "troubleshooting"}

    return RetrievalPlan(
        intent=intent,
        template=intent,
        entities=entities,
        queries={
            "sourcebot": sourcebot_queries,
            "qdrant": qdrant_queries,
            "ast": ast_queries,
            "graph": graph_queries,
        },
        precision={
            "enabled": precision_enabled,
            "patterns": terms[:5],
            "read_manifests": intent == "dependency_relation",
        },
    )
```

- [ ] **Step 4: Run planner tests**

Run: `cd chat-ui && python -m pytest tests/test_planner.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add chat-ui/retrieval/planner.py chat-ui/tests/test_planner.py
git commit -m "feat(chat-ui): add rule-based retrieval planner"
```

## Task 3: Add Ranking And Precision Decision

**Files:**
- Create: `chat-ui/retrieval/ranking.py`
- Test: `chat-ui/tests/test_ranking.py`

- [ ] **Step 1: Write failing ranking tests**

Create `chat-ui/tests/test_ranking.py`:

```python
import importlib.util
from pathlib import Path
import sys


def load_module(name):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(name, root / (name.replace(".", "/") + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_rank_repositories_prefers_exact_import_hits():
    models = load_module("retrieval.models")
    ranking = load_module("retrieval.ranking")
    hits = [
        models.RetrievalHit("qdrant", "other", "README.md", "L1-L5", strength="semantic", score=0.9),
        models.RetrievalHit("sourcebot", "block-proxy", "src/server.js", "L3", "require('anyproxy')", "exact_text"),
    ]

    ranked = ranking.rank_repositories(hits)

    assert ranked[0]["repo"] == "block-proxy"
    assert ranked[0]["score"] > ranked[1]["score"]


def test_should_precision_search_requires_selected_repo():
    models = load_module("retrieval.models")
    ranking = load_module("retrieval.ranking")
    plan = models.RetrievalPlan("dependency_relation", "dependency_relation", precision={"enabled": True})

    assert ranking.should_run_precision_search(plan, []) is False
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd chat-ui && python -m pytest tests/test_ranking.py -v`

Expected: FAIL because `ranking.py` does not exist.

- [ ] **Step 3: Implement ranking**

Create `chat-ui/retrieval/ranking.py`:

```python
from __future__ import annotations

from .models import RetrievalHit, RetrievalPlan


def _hit_score(hit: RetrievalHit) -> int:
    text = f"{hit.path}\n{hit.content}".lower()
    if hit.source == "sourcebot" and any(token in text for token in ["require(", "import ", "dependencies", "devdependencies"]):
        return 10
    if hit.source == "ast" and hit.strength == "structure":
        return 8
    if hit.source == "sourcebot":
        return 7
    if hit.source == "neo4j":
        return 6
    if hit.source == "qdrant" and (hit.score or 0) >= 0.75:
        return 4
    if hit.source == "qdrant":
        return 2
    return 1


def rank_repositories(hits: list[RetrievalHit]) -> list[dict]:
    scores: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}
    for hit in hits:
        if not hit.repo:
            continue
        score = _hit_score(hit)
        if "/readme" in hit.path.lower() or hit.path.lower().endswith("readme.md"):
            score -= 5
        scores[hit.repo] = scores.get(hit.repo, 0) + score
        reasons.setdefault(hit.repo, []).append(f"{hit.source}:{hit.path}:{hit.line_range}")
    return [
        {"repo": repo, "score": score, "reasons": reasons.get(repo, [])}
        for repo, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
    ]


def should_run_precision_search(plan: RetrievalPlan, ranked_repos: list[dict]) -> bool:
    return bool(plan.precision.get("enabled") and ranked_repos)
```

- [ ] **Step 4: Run ranking tests**

Run: `cd chat-ui && python -m pytest tests/test_ranking.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add chat-ui/retrieval/ranking.py chat-ui/tests/test_ranking.py
git commit -m "feat(chat-ui): rank retrieval repositories"
```

## Task 4: Add Safe Precision Search

**Files:**
- Create: `chat-ui/retrieval/precision.py`
- Test: `chat-ui/tests/test_precision.py`

- [ ] **Step 1: Write failing precision tests**

Create `chat-ui/tests/test_precision.py`:

```python
import importlib.util
from pathlib import Path
import sys


def load_precision():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("retrieval.precision", root / "retrieval/precision.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_read_manifest_finds_package_json(tmp_path):
    precision = load_precision()
    repo = tmp_path / "block-proxy"
    repo.mkdir()
    (repo / "package.json").write_text('{"dependencies":{"anyproxy":"1.0.0"}}\n', encoding="utf-8")

    hits = precision.read_manifest(str(tmp_path), "block-proxy")

    assert hits[0].path == "package.json"
    assert "anyproxy" in hits[0].content


def test_grep_repo_rejects_path_escape(tmp_path):
    precision = load_precision()
    try:
        precision.grep_repo(str(tmp_path), "../outside", "anyproxy")
    except ValueError as exc:
        assert "outside REPOS_ROOT" in str(exc)
    else:
        raise AssertionError("expected path escape to fail")
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd chat-ui && python -m pytest tests/test_precision.py -v`

Expected: FAIL because `precision.py` does not exist.

- [ ] **Step 3: Implement precision helpers**

Create `chat-ui/retrieval/precision.py`:

```python
from __future__ import annotations

from pathlib import Path
import re

from .models import RetrievalHit


MANIFESTS = ["package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "pyproject.toml", "requirements.txt"]


def _repo_root(repos_root: str, repo: str) -> Path:
    root = Path(repos_root).resolve()
    path = (root / repo).resolve()
    if root != path and root not in path.parents:
        raise ValueError("repo path is outside REPOS_ROOT")
    return path


def _line_range(start: int, end: int | None = None) -> str:
    return f"L{start}" if end is None or end == start else f"L{start}-L{end}"


def read_manifest(repos_root: str, repo: str) -> list[RetrievalHit]:
    repo_path = _repo_root(repos_root, repo)
    hits: list[RetrievalHit] = []
    for name in MANIFESTS:
        path = repo_path / name
        if not path.exists() or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        line_count = max(1, len(content.splitlines()))
        hits.append(RetrievalHit("precision_search", repo, name, _line_range(1, line_count), content, "file_confirmed"))
    return hits


def grep_repo(repos_root: str, repo: str, pattern: str, max_matches: int = 20) -> list[RetrievalHit]:
    repo_path = _repo_root(repos_root, repo)
    regex = re.compile(pattern)
    hits: list[RetrievalHit] = []
    for path in repo_path.rglob("*"):
        if len(hits) >= max_matches:
            break
        if not path.is_file() or path.suffix.lower() not in {".js", ".ts", ".tsx", ".jsx", ".json", ".py", ".toml", ".yaml", ".yml", ".md"}:
            continue
        rel = path.relative_to(repo_path).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for idx, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                hits.append(RetrievalHit("precision_search", repo, rel, _line_range(idx), line, "file_confirmed"))
                break
    return hits


def read_file_window(repos_root: str, repo: str, path: str, start_line: int, end_line: int) -> RetrievalHit | None:
    repo_path = _repo_root(repos_root, repo)
    file_path = (repo_path / path).resolve()
    if repo_path != file_path and repo_path not in file_path.parents:
        raise ValueError("file path is outside REPOS_ROOT")
    if not file_path.exists() or not file_path.is_file():
        return None
    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, start_line)
    end = min(len(lines), end_line)
    content = "\n".join(lines[start - 1:end])
    return RetrievalHit("precision_search", repo, path, _line_range(start, end), content, "file_confirmed")
```

- [ ] **Step 4: Run precision tests**

Run: `cd chat-ui && python -m pytest tests/test_precision.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add chat-ui/retrieval/precision.py chat-ui/tests/test_precision.py
git commit -m "feat(chat-ui): add bounded precision search"
```

## Task 5: Build Evidence Packs

**Files:**
- Create: `chat-ui/retrieval/evidence.py`
- Modify: `chat-ui/tests/test_evidence.py`

- [ ] **Step 1: Extend failing evidence tests**

Append to `chat-ui/tests/test_evidence.py`:

```python
def test_build_evidence_pack_assigns_high_confidence_for_two_strong_layers():
    models = load_module("retrieval.models")
    evidence = load_module("retrieval.evidence")
    plan = models.RetrievalPlan(
        "dependency_relation",
        "dependency_relation",
        entities={"subject": "block-proxy", "object": "anyproxy"},
    )
    hits = [
        models.RetrievalHit("precision_search", "block-proxy", "package.json", "L1-L10", "anyproxy", "file_confirmed"),
        models.RetrievalHit("sourcebot", "block-proxy", "src/server.js", "L3", "require('anyproxy')", "exact_text"),
    ]

    pack = evidence.build_evidence_pack("block-proxy 是怎样依赖 anyproxy 的", plan, hits, [{"repo": "block-proxy", "score": 20}])

    assert pack["confidence"] == "high"
    assert pack["evidence"][0]["tier"] == "strong"
    assert pack["retrieval_coverage"]["sourcebot"]["used"] is True
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd chat-ui && python -m pytest tests/test_evidence.py -v`

Expected: FAIL because `retrieval.evidence` does not exist.

- [ ] **Step 3: Implement Evidence Pack builder**

Create `chat-ui/retrieval/evidence.py`:

```python
from __future__ import annotations

from .models import EvidenceItem, RetrievalHit, RetrievalPlan


def evidence_tier(hit: RetrievalHit) -> str:
    if hit.source == "precision_search" and hit.content:
        return "strong"
    if hit.source == "sourcebot" and hit.strength == "exact_text" and hit.content:
        return "strong"
    if hit.source == "ast" and hit.line_range:
        return "strong"
    if hit.source in {"neo4j", "sourcebot", "ast"}:
        return "supporting"
    return "weak"


def _claim_for(hit: RetrievalHit) -> str:
    if hit.source == "precision_search":
        return "confirmed by repository-local file read/search"
    if hit.source == "sourcebot":
        return "exact code search match"
    if hit.source == "qdrant":
        return "semantic code search match"
    if hit.source == "ast":
        return "structural symbol/import/call fact"
    if hit.source == "neo4j":
        return "graph relation fact"
    return "retrieval match"


def _confidence(items: list[EvidenceItem]) -> str:
    strong_sources = {item.source for item in items if item.tier == "strong"}
    if len(strong_sources) >= 2:
        return "high"
    if strong_sources and any(item.tier == "supporting" for item in items):
        return "medium"
    if strong_sources:
        return "medium"
    if items:
        return "low"
    return "unconfirmed"


def build_evidence_pack(query: str, plan: RetrievalPlan, hits: list[RetrievalHit], ranked_repos: list[dict]) -> dict:
    evidence: list[EvidenceItem] = []
    for idx, hit in enumerate(hits[:30], start=1):
        evidence.append(
            EvidenceItem(
                id=f"E{idx}",
                tier=evidence_tier(hit),
                source=hit.source,
                repo=hit.repo,
                path=hit.path,
                line_range=hit.line_range,
                claim=_claim_for(hit),
                content=hit.content[:4000],
            )
        )
    coverage = {}
    for source in ["sourcebot", "qdrant", "ast", "neo4j", "precision_search"]:
        used = any(hit.source == source for hit in hits)
        coverage[source] = {"used": used, "summary": "provided evidence" if used else "未提供有效证据"}
    return {
        "query": query,
        "intent": plan.intent,
        "answer_template": plan.template,
        "entities": plan.entities,
        "candidate_repos": ranked_repos,
        "evidence": [item.to_dict() for item in evidence],
        "retrieval_coverage": coverage,
        "confidence": _confidence(evidence),
        "known_gaps": [],
    }
```

- [ ] **Step 4: Run evidence tests**

Run: `cd chat-ui && python -m pytest tests/test_evidence.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add chat-ui/retrieval/evidence.py chat-ui/tests/test_evidence.py
git commit -m "feat(chat-ui): build retrieval evidence packs"
```

## Task 6: Add Prompt Templates And Synthesizer Input

**Files:**
- Create: `chat-ui/prompts/__init__.py`
- Create: `chat-ui/prompts/templates.py`
- Create: `chat-ui/prompts/synthesizer.py`
- Test: `chat-ui/tests/test_synthesizer.py`

- [ ] **Step 1: Write failing synthesizer test**

Create `chat-ui/tests/test_synthesizer.py`:

```python
import importlib.util
from pathlib import Path
import sys


def load_module(name):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(name, root / (name.replace(".", "/") + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_synthesizer_system_prompt_contains_evidence_rules():
    synthesizer = load_module("prompts.synthesizer")

    system = synthesizer.build_system_prompt("dependency_relation")

    assert "Qdrant" in system
    assert "不能单独证明直接依赖" in system
    assert "repo/path:Lx" in system
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd chat-ui && python -m pytest tests/test_synthesizer.py -v`

Expected: FAIL because prompt modules do not exist.

- [ ] **Step 3: Implement prompt modules**

Create `chat-ui/prompts/__init__.py` empty.

Create `chat-ui/prompts/templates.py`:

```python
BASE_SYSTEM = """你是 repo-bot 的代码检索分析助手。你的任务是基于本地代码仓库检索结果回答问题。
你必须使用中文回答。
你必须优先依据提供的代码、结构索引、调用图和精搜结果。
不要编造不存在的文件、函数、调用链、依赖关系或版本号。
复杂问题先给结论，再给证据。
所有关键判断必须引用 repo/path:Lx 或 repo/path:Lx-Ly。
如果证据不足，明确说证据不足，并列出还需要检索什么。"""

SOURCE_POLICY = """Sourcebot 代表精确关键词、正则、文件内容命中。
Qdrant 代表语义相关代码片段，不能单独证明直接依赖或调用关系。
AST 代表结构化符号、定义、调用、import/require 信息。
Neo4j 代表从 AST 派生出的图关系，需要尽量结合文件内容确认。
精搜代表在候选仓库内进一步 read_file/grep 得到的高置信证据。"""

EVIDENCE_RULES = """强证据优先级：精搜文件内容 > Sourcebot 精确命中 > AST 结构事实 > Neo4j 调用图 > Qdrant 语义召回。
不要把多个仓库的同名文件混为一谈。
如果 repo 名称、包名、符号名可能多义，必须指出。
引用格式统一为 `repo/path:Lx` 或 `repo/path:Lx-Ly`。"""

DEPENDENCY_TEMPLATE = """依赖关系类输出：
## 结论
说明 subject 是否依赖 object、依赖类型、主要仓库、置信度。
## 依赖链路
展示声明、引入、调用、入口或配置流入链路。
## 关键证据
表格：层级 | 位置 | 说明
## 代码行为说明
解释 subject 如何使用 object。
## 检索覆盖
按 Sourcebot / Qdrant / AST / Neo4j / 精搜说明贡献。
## 不确定性
说明缺口。"""

GENERIC_TEMPLATE = """输出：
## 结论
直接回答问题。
## 证据
列出支持结论的文件和行号。"""


def template_for(name: str) -> str:
    if name == "dependency_relation":
        return DEPENDENCY_TEMPLATE
    return GENERIC_TEMPLATE
```

Create `chat-ui/prompts/synthesizer.py`:

```python
from __future__ import annotations

import json

from .templates import BASE_SYSTEM, EVIDENCE_RULES, SOURCE_POLICY, template_for


def build_system_prompt(template: str) -> str:
    return "\n\n".join([BASE_SYSTEM, SOURCE_POLICY, EVIDENCE_RULES, template_for(template)])


def build_user_message(question: str, evidence_pack: dict) -> str:
    return "Evidence Pack:\n" + json.dumps(evidence_pack, ensure_ascii=False, indent=2) + "\n\n问题: " + question
```

- [ ] **Step 4: Run synthesizer tests**

Run: `cd chat-ui && python -m pytest tests/test_synthesizer.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add chat-ui/prompts chat-ui/tests/test_synthesizer.py
git commit -m "feat(chat-ui): add evidence-based answer prompts"
```

## Task 7: Integrate Pipeline Into Streamlit App

**Files:**
- Modify: `chat-ui/app.py`
- Modify: `chat-ui/Dockerfile`

- [ ] **Step 1: Refactor imports and add new pipeline helper**

In `chat-ui/app.py`, import:

```python
from retrieval.planner import plan_query
from retrieval.ranking import rank_repositories, should_run_precision_search
from retrieval.precision import grep_repo, read_file_window, read_manifest
from retrieval.evidence import build_evidence_pack
from retrieval.models import RetrievalHit
from prompts.synthesizer import build_system_prompt, build_user_message
```

Add conversion helpers near existing search backend functions:

```python
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
```

- [ ] **Step 2: Add Evidence Pack aware LLM function**

Keep `ask_llm()` as fallback. Add:

```python
def ask_llm_with_evidence(question: str, evidence_pack: dict, history: list[dict] | None = None) -> str:
    import anthropic, httpx
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    if not api_key:
        return "❌ 未配置 ANTHROPIC_API_KEY"
    if not evidence_pack.get("evidence"):
        return "未找到足够相关代码证据，请尝试更精确的搜索词。"

    messages: list[dict] = []
    if history:
        messages.extend(history[-10:])
    messages.append({"role": "user", "content": build_user_message(question, evidence_pack)})

    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client_kwargs["http_client"] = httpx.Client(verify=False)
    client = anthropic.Anthropic(**client_kwargs)
    resp = client.messages.create(
        model=os.environ.get("LLM_MODEL", "claude-sonnet-4-6"),
        max_tokens=3000,
        system=build_system_prompt(evidence_pack.get("answer_template", "generic_code_answer")),
        messages=messages,
    )
    for block in resp.content:
        if hasattr(block, "text") and block.text:
            return block.text
    return "(模型未生成文本回答，可能只返回了 thinking block)"
```

- [ ] **Step 3: Execute planner and build hits in chat flow**

In the `if prompt := st.chat_input(...)` block, before retrieval:

```python
plan = plan_query(prompt)
```

After `src`, `qdr`, `merged`, `ast_facts`, and `graph_facts` are available, build hits:

```python
hits = to_hits(src, "sourcebot") + to_hits(qdr, "qdrant")
hits.extend(RetrievalHit("ast", "ast-service", "structure", "", fact, "structure") for fact in ast_facts)
hits.extend(RetrievalHit("neo4j", "ast-service", "graph", "", fact, "graph") for fact in graph_facts)
ranked_repos = rank_repositories(hits)
```

- [ ] **Step 4: Add bounded precision search**

If `should_run_precision_search(plan, ranked_repos)`:

```python
repos_root = os.environ.get("REPOS_ROOT", "/repos")
top_repo = ranked_repos[0]["repo"]
precision_hits = []
if plan.precision.get("read_manifests"):
    precision_hits.extend(read_manifest(repos_root, top_repo))
for pattern in plan.precision.get("patterns", [])[:5]:
    precision_hits.extend(grep_repo(repos_root, top_repo, pattern, max_matches=10))
hits.extend(precision_hits[:30])
```

Guard this with `try/except Exception as exc` and display a Streamlit warning rather than failing the whole answer.

- [ ] **Step 5: Build Evidence Pack and call new synthesizer**

Replace the final `ctx_json` answer call with:

```python
evidence_pack = build_evidence_pack(prompt, plan, hits, ranked_repos)
answer = ask_llm_with_evidence(prompt, evidence_pack, history)
```

Keep old `ask_llm(prompt, ctx_json, history)` fallback if `ask_llm_with_evidence` raises.

- [ ] **Step 6: Render Evidence Pack summary**

Add an expander after existing retrieval expanders:

```python
with st.expander(f"Evidence Pack ({evidence_pack.get('confidence', '-')})", expanded=False):
    st.json({
        "intent": evidence_pack.get("intent"),
        "candidate_repos": evidence_pack.get("candidate_repos", [])[:5],
        "retrieval_coverage": evidence_pack.get("retrieval_coverage"),
        "evidence_count": len(evidence_pack.get("evidence", [])),
    })
```

- [ ] **Step 7: Update Dockerfile copy rules**

Update `chat-ui/Dockerfile`:

```dockerfile
COPY app.py sourcebot_client.py ./
COPY retrieval ./retrieval
COPY prompts ./prompts
```

- [ ] **Step 8: Run focused tests**

Run: `cd chat-ui && python -m pytest tests -v`

Expected: all chat-ui tests pass.

- [ ] **Step 9: Run syntax/import check**

Run: `cd chat-ui && python -m py_compile app.py retrieval/*.py prompts/*.py sourcebot_client.py`

Expected: no output and exit code 0.

- [ ] **Step 10: Commit**

```bash
git add chat-ui/app.py chat-ui/Dockerfile
git commit -m "feat(chat-ui): answer from structured evidence packs"
```

## Task 8: Optional LLM Planner Enhancement

**Files:**
- Modify: `chat-ui/retrieval/planner.py`
- Test: `chat-ui/tests/test_planner.py`
- Modify: `chat-ui/app.py`

- [ ] **Step 1: Write LLM plan validation tests**

Append to `chat-ui/tests/test_planner.py`:

```python
def test_validate_llm_planner_rejects_non_json():
    planner = load_planner()

    assert planner.validate_llm_plan("not json") == {}


def test_merge_llm_plan_adds_queries_without_replacing_intent():
    planner = load_planner()
    base = planner.plan_query("block-proxy 是怎样依赖 anyproxy 的")
    merged = planner.merge_llm_plan(base, {"query_rewrites": {"sourcebot": ["ProxyServer"]}})

    assert merged.intent == "dependency_relation"
    assert "ProxyServer" in merged.queries["sourcebot"]
```

- [ ] **Step 2: Run planner tests to verify failure**

Run: `cd chat-ui && python -m pytest tests/test_planner.py -v`

Expected: FAIL because validation helpers do not exist.

- [ ] **Step 3: Implement validation and merge helpers**

Add to `chat-ui/retrieval/planner.py`:

```python
import json
from dataclasses import replace
from typing import Any


def validate_llm_plan(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _extend_unique(current: list[Any], extra: list[Any], limit: int = 8) -> list[Any]:
    result = list(current)
    for item in extra:
        if item not in result:
            result.append(item)
        if len(result) >= limit:
            break
    return result


def merge_llm_plan(base: RetrievalPlan, llm_plan: dict[str, Any]) -> RetrievalPlan:
    queries = {key: list(value) for key, value in base.queries.items()}
    rewrites = llm_plan.get("query_rewrites", {})
    if isinstance(rewrites, dict):
        for key in ["sourcebot", "qdrant"]:
            extra = rewrites.get(key)
            if isinstance(extra, list):
                queries[key] = _extend_unique(queries.get(key, []), extra)
    precision = dict(base.precision)
    extra_precision = llm_plan.get("precision_search", {})
    if isinstance(extra_precision, dict) and isinstance(extra_precision.get("extra_patterns"), list):
        precision["patterns"] = _extend_unique(list(precision.get("patterns", [])), extra_precision["extra_patterns"])
    return replace(base, queries=queries, precision=precision)
```

- [ ] **Step 4: Run planner tests**

Run: `cd chat-ui && python -m pytest tests/test_planner.py -v`

Expected: PASS.

- [ ] **Step 5: Wire optional planner in app**

Add an environment-gated helper in `app.py`, default off or conservative:

```python
USE_LLM_PLANNER = os.environ.get("LLM_PLANNER_ENABLED", "false").lower() == "true"
```

Only invoke it for complex intents and short timeout. If anything fails, continue with Rule Planner.

- [ ] **Step 6: Run tests and compile**

Run:

```bash
cd chat-ui && python -m pytest tests -v
cd chat-ui && python -m py_compile app.py retrieval/*.py prompts/*.py sourcebot_client.py
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add chat-ui/retrieval/planner.py chat-ui/tests/test_planner.py chat-ui/app.py
git commit -m "feat(chat-ui): add optional llm retrieval planner"
```

## Task 9: Full Verification

**Files:**
- No planned source changes unless verification finds issues.

- [ ] **Step 1: Run all chat-ui tests**

Run: `cd chat-ui && python -m pytest tests -v`

Expected: PASS.

- [ ] **Step 2: Run ast-service tests to ensure no regression**

Run: `cd ast-service && python -m pytest -v`

Expected: PASS.

- [ ] **Step 3: Build chat-ui container**

Run: `docker compose build chat-ui`

Expected: build completes successfully.

- [ ] **Step 4: Start services**

Run: `docker compose up -d chat-ui ast-service sourcebot qdrant neo4j`

Expected: containers start. If local data is not indexed, UI should still load and degrade clearly.

- [ ] **Step 5: Open Chat UI**

Run: `npm run open`

Expected: browser opens `http://localhost:8501`.

- [ ] **Step 6: Manual query checks**

Ask:

- `block-proxy 是怎样依赖 anyproxy 的`
- `登录逻辑在哪里`
- `search_graph_relations 是怎么工作的`
- A deliberately weak query with no obvious evidence.

Expected:

- Complex dependency answer includes `结论`, `关键证据`, `检索覆盖`, and `不确定性`.
- Simple location answer is concise.
- Weak evidence answer does not claim high confidence.

- [ ] **Step 7: Commit verification fixes if needed**

If verification found implementation issues:

```bash
git add chat-ui
git commit -m "fix(chat-ui): address retrieval workflow verification issues"
```

## Execution Notes

- Do not remove the existing Sourcebot/Qdrant/AST/Neo4j sidebars or expanders in the first implementation.
- Preserve current authentication/session behavior.
- Keep `ask_llm()` until the evidence path has passed manual verification.
- Keep precision search bounded by repository, patterns, matches, and line windows.
- Never let LLM Planner produce executable shell commands.
