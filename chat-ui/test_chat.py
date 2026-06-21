#!/usr/bin/env python3
"""本地测试脚本：复刻 Chat UI 完整检索管线，无需 Docker 部署。

用法：
    python test_chat.py "block-proxy 是怎样依赖 anyproxy 的"
    python test_chat.py "index_code.py 在哪里定义的 embed_query"
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

# 加载 .env（优先当前目录，再上级目录）
for env_dir in [Path.cwd(), Path(__file__).resolve().parent, Path.cwd().parent]:
    env_file = env_dir / ".env"
    if env_file.exists():
        load_dotenv(env_file)
        break

import anthropic
import httpx
import requests
from openai import OpenAI
from qdrant_client import QdrantClient

from retrieval.planner import plan_query, validate_llm_plan, merge_llm_plan
from retrieval.ranking import rank_repositories, should_run_precision_search
from retrieval.precision import grep_repo, read_file_window, read_manifest, local_tool_grep, local_tool_read
from retrieval.evidence import build_evidence_pack
from retrieval.models import RetrievalHit
from prompts.synthesizer import build_system_prompt, build_user_message
from sourcebot_client import search_sourcebot as sourcebot_search

USE_LLM_PLANNER = os.environ.get("LLM_PLANNER_ENABLED", "false").lower() == "true"
REPOS_ROOT = os.path.expanduser(os.environ.get("REPOS_ROOT", "/repos"))

# ── Embedding ────────────────────────────────────────────────────────────────

_openai_client: OpenAI | None = None

def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(
            api_key=os.environ.get("EMBEDDING_API_KEY", os.environ.get("DASHSCOPE_API_KEY", "")),
            base_url=os.environ.get("EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        )
    return _openai_client

def embed_query(text: str) -> list[float]:
    client = _get_openai_client()
    model = os.environ.get("EMBEDDING_MODEL", "text-embedding-v4")
    dim = int(os.environ.get("EMBEDDING_DIM", "1024"))
    resp = client.embeddings.create(model=model, input=text, dimensions=dim, encoding_format="float")
    return resp.data[0].embedding

# ── Qdrant ───────────────────────────────────────────────────────────────────

_qdrant_client: QdrantClient | None = None

def _get_qdrant_client():
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(url=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    return _qdrant_client

def search_qdrant(query: str, top_k: int = 10) -> list[dict]:
    client = _get_qdrant_client()
    vector = embed_query(query)
    collection = os.environ.get("QDRANT_COLLECTION", "codebase")
    hits = client.query_points(collection, query=vector, limit=top_k)
    results = []
    for h in hits.points:
        p = h.payload
        results.append({
            "source": "qdrant",
            "repo": p["repo"],
            "path": p["path"],
            "line": f"L{p['start_line']}",
            "start_line": p["start_line"],
            "end_line": p["end_line"],
            "language": p.get("language", ""),
            "score": round(h.score, 3),
        })
    return results

# ── Sourcebot ────────────────────────────────────────────────────────────────

def search_sourcebot(query: str, top_k: int = 10) -> list[dict]:
    result = sourcebot_search(query, top_k=top_k)
    if result.error:
        print(f"  ⚠️  Sourcebot: {result.error}", file=sys.stderr)
    return result.items

# ── Helpers ──────────────────────────────────────────────────────────────────

def read_file_content(repo: str, path: str, start_line: int, end_line: int) -> str:
    fp = os.path.join(REPOS_ROOT, path)
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[max(0, start_line - 1):end_line])
    except Exception:
        return ""

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
    scores, all_r = {}, {}
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

def candidate_symbols(query: str, results: list[dict], limit: int = 8) -> list[str]:
    names: list[str] = []
    pattern = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?\b")
    for text in [query] + [r.get("content", "") for r in results]:
        for match in pattern.findall(text or ""):
            if len(match) < 3:
                continue
            if match in {"the", "and", "for", "return", "class", "function", "def"}:
                continue
            if match not in names:
                names.append(match)
            if len(names) >= limit:
                return names
    return names

# ── AST ──────────────────────────────────────────────────────────────────────

def search_ast_structure(query: str, results: list[dict], limit: int = 8) -> list[str]:
    url = os.environ.get("AST_SERVICE_URL", "http://localhost:8502").rstrip("/")
    symbols = candidate_symbols(query, results, limit=limit)
    facts: list[str] = []
    seen: set[str] = set()
    for name in symbols:
        repos = [r.get("repo") for r in results if r.get("repo")]
        repos = list(dict.fromkeys(repos))[:3] or [None]
        for repo in repos:
            params = {"callee_name": name, "limit": 5}
            if repo:
                params["repo"] = repo
            try:
                resp = requests.get(f"{url}/calls", params=params, timeout=3)
                resp.raise_for_status()
                for call in resp.json().get("calls", []):
                    fact = (
                        f"[structure] {name} called at "
                        f"{call.get('repo')}/{call.get('path')}:L{call.get('call_line')}"
                    )
                    if fact not in seen:
                        seen.add(fact)
                        facts.append(fact)
                    if len(facts) >= limit:
                        return facts
            except Exception:
                continue

    if not facts:
        repos = [r.get("repo") for r in results if r.get("repo")]
        repos = list(dict.fromkeys(repos))[:3]
        for repo in repos:
            try:
                resp = requests.get(f"{url}/symbols", params={"repo": repo, "limit": limit}, timeout=3)
                resp.raise_for_status()
                for sym in resp.json().get("symbols", []):
                    fact = (
                        f"[structure] {sym.get('name')} ({sym.get('kind', '')}) defined at "
                        f"{sym.get('repo')}/{sym.get('path')}:L{sym.get('start_line')}"
                    )
                    if fact not in seen:
                        seen.add(fact)
                        facts.append(fact)
                    if len(facts) >= limit:
                        return facts
            except Exception:
                continue
    return facts

# ── Neo4j ───────────────────────────────────────────────────────────────────

def search_graph_relations(query: str, results: list[dict], limit: int = 12) -> list[str]:
    url = os.environ.get("AST_SERVICE_URL", "http://localhost:8502").rstrip("/")
    symbols = candidate_symbols(query, results, limit=6)
    facts: list[str] = []
    seen: set[str] = set()

    repos = [r.get("repo") for r in results if r.get("repo")]
    repos = list(dict.fromkeys(repos))[:3]

    for name in symbols:
        for repo in repos:
            if not repo:
                continue
            try:
                resp = requests.get(
                    f"{url}/graph/impact",
                    params={"repo": repo, "symbol": name, "depth": 2, "limit": 8},
                    timeout=5,
                )
                resp.raise_for_status()
                for fact_item in resp.json().get("facts", []):
                    node = fact_item.get("node", {})
                    dist = fact_item.get("distance", 0)
                    node_name = node.get("name", "?")
                    node_kind = node.get("kind", "")
                    node_path = node.get("path", "")
                    loc = f"{repo}/{node_path}" if node_path else repo
                    if dist > 0:
                        desc = (
                            f"[graph] {name} calls {node_name}"
                            + (f"({node_kind})" if node_kind else "")
                            + f" (depth {dist}) in {loc}"
                        )
                    else:
                        desc = (
                            f"[graph] {node_name}"
                            + (f"({node_kind})" if node_kind else "")
                            + f" calls {name} (depth {-dist}) in {loc}"
                        )
                    if desc not in seen:
                        seen.add(desc)
                        facts.append(desc)
                    if len(facts) >= limit:
                        return facts
            except Exception:
                continue

    if not facts:
        for repo in repos:
            if not repo:
                continue
            try:
                sym_resp = requests.get(
                    f"{url}/symbols",
                    params={"repo": repo, "limit": 5},
                    timeout=3,
                )
                sym_resp.raise_for_status()
                top_names = [s["name"] for s in sym_resp.json().get("symbols", [])]
            except Exception:
                continue
            for name in top_names:
                try:
                    resp = requests.get(
                        f"{url}/graph/impact",
                        params={"repo": repo, "symbol": name, "depth": 2, "limit": 5},
                        timeout=5,
                    )
                    resp.raise_for_status()
                    for fact_item in resp.json().get("facts", []):
                        node = fact_item.get("node", {})
                        dist = fact_item.get("distance", 0)
                        node_name = node.get("name", "?")
                        node_kind = node.get("kind", "")
                        node_path = node.get("path", "")
                        loc = f"{repo}/{node_path}" if node_path else repo
                        if dist > 0:
                            desc = (
                                f"[graph] {name} calls {node_name}"
                                + (f"({node_kind})" if node_kind else "")
                                + f" (depth {dist}) in {loc}"
                            )
                        else:
                            desc = (
                                f"[graph] {node_name}"
                                + (f"({node_kind})" if node_kind else "")
                                + f" calls {name} (depth {-dist}) in {loc}"
                            )
                        if desc not in seen:
                            seen.add(desc)
                            facts.append(desc)
                        if len(facts) >= limit:
                            return facts
                except Exception:
                    continue
    return facts

# ── LLM ──────────────────────────────────────────────────────────────────────

def ask_llm_with_evidence(question: str, evidence_pack: dict) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    if not api_key:
        return "❌ 未配置 ANTHROPIC_API_KEY"
    if not evidence_pack.get("evidence"):
        return "未找到足够相关代码证据，请尝试更精确的搜索词。"

    messages = [{"role": "user", "content": build_user_message(question, evidence_pack)}]

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

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("用法: python test_chat.py \"你的问题\"", file=sys.stderr)
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    print(f"🔍 问题: {question}\n")

    # 1. Plan
    plan = plan_query(question)
    print(f"📋 意图: {plan.intent}  |  Entities: {plan.entities.get('raw_terms', [])}")

    # 2. Optional LLM planner
    if USE_LLM_PLANNER and plan.intent in {"dependency_relation", "call_chain", "troubleshooting"}:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
        if api_key:
            try:
                client_kwargs = {"api_key": api_key}
                if base_url:
                    client_kwargs["base_url"] = base_url
                client_kwargs["http_client"] = httpx.Client(verify=False)
                client = anthropic.Anthropic(**client_kwargs)
                resp = client.messages.create(
                    model=os.environ.get("LLM_MODEL", "claude-sonnet-4-6"),
                    max_tokens=500,
                    system="你是一个代码检索规划器。根据用户问题返回 JSON 格式的检索增强建议。只输出 JSON，不要有其他内容。",
                    messages=[{"role": "user", "content": f"问题：{question}\n\n输出格式：\n{{\n  \"query_rewrites\": {{\"sourcebot\": [\"...\"], \"qdrant\": [\"...\"]}},\n  \"entity_hints\": {{\"likely_repo\": \"...\", \"likely_dependency\": \"...\", \"likely_api_symbols\": [\"...\"]}},\n  \"precision_search\": {{\"extra_patterns\": [\"...\"], \"important_files\": [\"...\"]}}\n}}"}],
                )
                llm_text = next((b.text for b in resp.content if hasattr(b, "text") and b.text), "")
                llm_plan = validate_llm_plan(llm_text)
                if llm_plan:
                    plan = merge_llm_plan(plan, llm_plan)
                    print(f"🤖 LLM Planner 已合并")
            except Exception as e:
                print(f"  ⚠️  LLM Planner 失败: {e}", file=sys.stderr)

    # 3. Search
    print("\n⏳ 检索中...")
    src = search_sourcebot(question)
    qdr = search_qdrant(question)
    merged = merge_results(src, qdr)
    print(f"  Sourcebot: {len(src)} 条  |  Qdrant: {len(qdr)} 条  |  Merge: {len(merged)} 条")

    # 4. Read content for merged results
    for r in merged:
        if not r.get("content"):
            r["content"] = read_file_content(
                r["repo"], r["path"],
                r.get("start_line", 1), r.get("end_line", r.get("start_line", 1) + 30))

    # 5. AST & Graph
    ast_facts = search_ast_structure(question, merged)
    graph_facts = search_graph_relations(question, merged)
    print(f"  AST: {len(ast_facts)} 条  |  Neo4j: {len(graph_facts)} 条")

    # 6. Build hits
    hits = to_hits(src, "sourcebot") + to_hits(qdr, "qdrant")
    for fact in ast_facts:
        hits.append(RetrievalHit("ast", "ast-service", "structure", "", fact, "structure"))
    for fact in graph_facts:
        hits.append(RetrievalHit("neo4j", "ast-service", "graph", "", fact, "graph"))
    ranked_repos = rank_repositories(hits)

    if ranked_repos:
        top_repos_str = ", ".join(f"{r['repo']}({r['score']})" for r in ranked_repos[:3])
        print(f"  Top repos: {top_repos_str}")

    # 7. Precision search
    if should_run_precision_search(plan, ranked_repos):
        top_repo = ranked_repos[0]["repo"]
        try:
            if plan.precision.get("read_manifests"):
                hits.extend(read_manifest(REPOS_ROOT, top_repo))
            for pattern in plan.precision.get("patterns", [])[:5]:
                hits.extend(local_tool_grep(
                    REPOS_ROOT, top_repo, pattern,
                    include=["*.js", "*.ts", "*.tsx", "*.jsx", "*.py", "*.json", "*.toml", "*.yaml", "*.yml"],
                    exclude=["node_modules/*", "vendor/*", "dist/*", "build/*", ".git/*"],
                    max_matches=10, context_lines=1,
                ))
            seen_paths: set[str] = set()
            for hit in hits:
                if hit.source in ("local_tool", "sourcebot") and hit.repo == top_repo:
                    file_key = f"{hit.repo}:{hit.path}"
                    if file_key not in seen_paths:
                        seen_paths.add(file_key)
                        rh = local_tool_read(REPOS_ROOT, top_repo, hit.path, max_lines=200)
                        if rh:
                            hits.append(rh)
                    if len(seen_paths) >= 3:
                        break
            precision_count = sum(1 for h in hits if h.source in ("local_tool", "precision_search"))
            print(f"  Precision: {precision_count} 条精搜结果")
        except Exception as exc:
            print(f"  ⚠️  精搜出错: {exc}", file=sys.stderr)

    # 8. Evidence pack
    evidence_pack = build_evidence_pack(question, plan, hits, ranked_repos)
    print(f"\n📊 Evidence Pack: {len(evidence_pack['evidence'])} items  |  confidence={evidence_pack['confidence']}")

    # 9. LLM answer
    print("\n⏳ LLM 思考中...\n")
    print("=" * 60)
    try:
        answer = ask_llm_with_evidence(question, evidence_pack)
    except Exception as e:
        print(f"\n❌ LLM 调用失败: {e}", file=sys.stderr)
        print(f"\nEvidence Pack (前 3 条):")
        for item in evidence_pack.get("evidence", [])[:3]:
            print(f"  {item['id']} [{item['tier']}] {item['repo']}/{item['path']}:{item['line_range']}")
            print(f"    {item['content'][:200]}")
        sys.exit(1)

    print(answer)
    print("=" * 60)


if __name__ == "__main__":
    main()
