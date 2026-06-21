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

from retrieval.agent_loop import RetrievalBackends, run_retrieval_loop
from retrieval.planner import plan_query, validate_llm_plan, merge_llm_plan
from retrieval.ranking import rank_repositories
from retrieval.precision import grep_repo, read_file_window, read_manifest, local_tool_grep, local_tool_read, local_tool_list
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

# ── LLM Planner Adapter ─────────────────────────────────────────────────────

def llm_plan_query(question: str, plan) -> dict:
    if not USE_LLM_PLANNER or plan.intent not in {"dependency_relation", "call_chain", "troubleshooting"}:
        return {}
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {}
    try:
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
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
        return llm_plan if llm_plan else {}
    except Exception:
        return {}

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("用法: python test_chat.py \"你的问题\"", file=sys.stderr)
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    print(f"🔍 问题: {question}\n")

    # 1-7. Retrieval loop
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
        llm_plan=llm_plan_query,
    )

    print("⏳ 检索中...")
    result = run_retrieval_loop(question, repos_root=REPOS_ROOT, backends=backends)

    plan = result.plan
    hits = result.hits
    merged = result.merged
    ast_facts = result.ast_facts
    graph_facts = result.graph_facts
    ranked_repos = result.ranked_repos

    print(f"📋 意图: {plan.intent}")

    # Round diagnostics
    for round_info in result.rounds:
        print(f"  Round {round_info.index}: Sourcebot={len(round_info.sourcebot_queries)} Qdrant={len(round_info.qdrant_queries)} AST={len(round_info.ast_queries)} Graph={len(round_info.graph_queries)} Local={len(round_info.local_actions)} NewHits={round_info.new_hits}")
        if round_info.notes:
            for note in round_info.notes:
                print(f"    ⚠️  {note}")

    print(f"  Total: Sourcebot={len([h for h in hits if h.source == 'sourcebot'])} Qdrant={len([h for h in hits if h.source == 'qdrant'])} Merge={len(merged)}")
    print(f"  AST: {len(ast_facts)} 条  |  Neo4j: {len(graph_facts)} 条")

    if ranked_repos:
        top_repos_str = ", ".join(f"{r['repo']}({r['score']})" for r in ranked_repos[:3])
        print(f"  Top repos: {top_repos_str}")

    precision_count = sum(1 for h in hits if h.source in ("local_tool", "precision_search"))
    if precision_count:
        print(f"  Precision: {precision_count} 条精搜结果")

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
