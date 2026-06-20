"""
repo-bot Chat UI — 混合检索对话框
Qdrant 语义搜索 (text-embedding-v4) + Sourcebot Zoekt 搜索
"""
import os, json
from openai import OpenAI
import streamlit as st
from dotenv import load_dotenv
from sourcebot_client import search_sourcebot as search_sourcebot_client
from retrieval.planner import plan_query, validate_llm_plan, merge_llm_plan
from retrieval.ranking import rank_repositories, should_run_precision_search
from retrieval.precision import grep_repo, read_file_window, read_manifest
from retrieval.evidence import build_evidence_pack
from retrieval.models import RetrievalHit
from prompts.synthesizer import build_system_prompt, build_user_message
load_dotenv()

USE_LLM_PLANNER = os.environ.get("LLM_PLANNER_ENABLED", "false").lower() == "true"

import hashlib

st.set_page_config(page_title="repo-bot", page_icon="🤖", layout="wide")

def _auth_token() -> str:
    user = os.environ.get("CHAT_USERNAME", "admin")
    pwd = os.environ.get("CHAT_PASSWORD", "admin123")
    return hashlib.sha256(f"{user}:{pwd}".encode()).hexdigest()[:16]

@st.cache_resource
def _session_store() -> dict[str, list[dict]]:
    return {}

# === 鉴权（URL token 持久化） ===
if "authenticated" not in st.session_state:
    token = st.query_params.get("token")
    st.session_state.authenticated = (token == _auth_token())
    if st.session_state.authenticated and token:
        st.session_state._session_token = token

# === 退出登录 ===
if st.query_params.get("logout") == "1" and st.session_state.authenticated:
    token = st.session_state.get("_session_token")
    if token:
        _session_store().pop(token, None)
    st.session_state.authenticated = False
    st.session_state.messages = []
    st.query_params.clear()
    st.rerun()

if not st.session_state.authenticated:
    st.title("repo-bot — 登录")
    with st.form("login_form"):
        username = st.text_input("用户名")
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("登录")
        if submitted:
            env_user = os.environ.get("CHAT_USERNAME", "admin")
            env_pass = os.environ.get("CHAT_PASSWORD", "admin123")
            if username == env_user and password == env_pass:
                st.session_state.authenticated = True
                token = _auth_token()
                st.query_params["token"] = token
                st.session_state._session_token = token
                st.rerun()
            else:
                st.error("用户名或密码错误")
    st.stop()

st.title("repo-bot — 本地代码知识库")

# === Embedding helper ===
@st.cache_resource
def get_openai_client():
    return OpenAI(
        api_key=os.environ.get("EMBEDDING_API_KEY", os.environ.get("DASHSCOPE_API_KEY", "")),
        base_url=os.environ.get("EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    )

@st.cache_resource
def get_qdrant_client():
    from qdrant_client import QdrantClient
    return QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))

def embed_query(text: str) -> list[float]:
    client = get_openai_client()
    model = os.environ.get("EMBEDDING_MODEL", "text-embedding-v4")
    dim = int(os.environ.get("EMBEDDING_DIM", "1024"))
    resp = client.embeddings.create(model=model, input=text, dimensions=dim, encoding_format="float")
    return resp.data[0].embedding

# === 侧边栏 ===
with st.sidebar:
    st.header("配置")
    st.caption(f"Embedding: {os.environ.get('EMBEDDING_MODEL', 'text-embedding-v4')}")
    st.caption(f"Qdrant: {os.environ.get('QDRANT_URL', 'http://localhost:6333')}")
    st.caption(f"Sourcebot: {os.environ.get('SOURCEBOT_URL', 'http://localhost:3000')}")
    st.caption(f"LLM: {os.environ.get('LLM_MODEL', 'claude-sonnet-4-6')}")
    st.caption(f"AST: {os.environ.get('AST_SERVICE_URL', 'http://ast-service:8502')}")
    st.caption(f"Neo4j: {os.environ.get('NEO4J_URI', 'bolt://localhost:7687')} ({os.environ.get('NEO4J_DATABASE', 'neo4j')})")
    st.divider()
    use_qdrant = st.checkbox("Qdrant 语义搜索（向量库）", value=True)
    use_sourcebot = st.checkbox("Sourcebot 精确搜索（匹配关键词）", value=True)
    use_ast = st.checkbox("AST 结构检索（代码树）", value=True)
    use_graph = st.checkbox("Neo4j 图关系检索（图谱）", value=True)
    st.divider()
    if st.button("🆕 新对话", use_container_width=True):
        token = st.session_state.get("_session_token")
        if token:
            _session_store().pop(token, None)
        st.session_state.messages = []
        st.rerun()
    st.caption(f"👤 {os.environ.get('CHAT_USERNAME', 'admin')}")
    st.markdown("<a href='?logout=1' target='_self' style='font-size:14px;'>退出登录</a>", unsafe_allow_html=True)

# === 搜索后端 ===
def search_qdrant(query: str, top_k: int = 10) -> list[dict]:
    client = get_qdrant_client()
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

def search_sourcebot(query: str, top_k: int = 10) -> list[dict]:
    result = search_sourcebot_client(query, top_k=top_k)
    st.session_state.sourcebot_error = result.error
    return result.items

def read_file_content(repo: str, path: str, start_line: int, end_line: int) -> str:
    fp = os.path.join(os.environ.get("REPOS_ROOT", "/repos"), path)
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
    import re

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


def search_ast_structure(query: str, results: list[dict], limit: int = 8) -> list[str]:
    import requests

    url = os.environ.get("AST_SERVICE_URL", "http://ast-service:8502").rstrip("/")
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

    # Fallback: 按 repo 查询 top symbols（比无差别调用更有信息量）
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

def search_graph_relations(query: str, results: list[dict], limit: int = 12) -> list[str]:
    import requests

    url = os.environ.get("AST_SERVICE_URL", "http://ast-service:8502").rstrip("/")
    symbols = candidate_symbols(query, results, limit=6)
    facts: list[str] = []
    seen: set[str] = set()

    repos = [r.get("repo") for r in results if r.get("repo")]
    repos = list(dict.fromkeys(repos))[:3]

    # 候选符号匹配
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

    # Fallback: 先查 repo 内 top symbols，再查影响关系
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


def ask_llm(question: str, ctx_json: str, history: list[dict] | None = None) -> str:
    import anthropic, httpx
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    if not api_key:
        return "❌ 未配置 ANTHROPIC_API_KEY"

    ctx = json.loads(ctx_json)
    if not ctx:
        return "未找到相关代码，请尝试更精确的搜索词。"

    ctx_text = "\n\n".join([
        f"[{c['repo']}] {c['path']}:{c['line']}\n```{c.get('language','')}\n{c['content']}\n```"
        for c in ctx[:10] if c.get("content")
    ])

    if not ctx_text.strip():
        return "未找到相关代码内容。"

    messages: list[dict] = []
    if history:
        messages.extend(history[-10:])
    messages.append({"role": "user", "content": f"上下文代码:\n{ctx_text}\n\n问题: {question}"})

    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client_kwargs["http_client"] = httpx.Client(verify=False)
    client = anthropic.Anthropic(**client_kwargs)
    resp = client.messages.create(
        model=os.environ.get("LLM_MODEL", "claude-sonnet-4-6"),
        max_tokens=2000,
        system="你是代码知识库助手。根据提供的代码片段用中文回答用户问题，引用具体文件路径和行号。你可以结合对话历史理解上下文和指代。",
        messages=messages,
    )
    for block in resp.content:
        if hasattr(block, "text") and block.text:
            return block.text
    return "(模型未生成文本回答，可能只返回了 thinking block)"

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

# === 主界面 ===
if "messages" not in st.session_state:
    token = st.session_state.get("_session_token")
    st.session_state.messages = _session_store().get(token, []) if token else []

def _persist_messages():
    token = st.session_state.get("_session_token")
    if token:
        _session_store()[token] = list(st.session_state.messages)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("输入你的问题..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    _persist_messages()
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        plan = plan_query(prompt)

        if USE_LLM_PLANNER and plan.intent in {"dependency_relation", "call_chain", "troubleshooting"}:
            try:
                import anthropic, httpx
                api_key = os.environ.get("ANTHROPIC_API_KEY", "")
                base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
                if api_key:
                    client_kwargs = {"api_key": api_key}
                    if base_url:
                        client_kwargs["base_url"] = base_url
                    client_kwargs["http_client"] = httpx.Client(verify=False)
                    client = anthropic.Anthropic(**client_kwargs)
                    resp = client.messages.create(
                        model=os.environ.get("LLM_MODEL", "claude-sonnet-4-6"),
                        max_tokens=500,
                        system="你是一个代码检索规划器。根据用户问题返回 JSON 格式的检索增强建议。只输出 JSON，不要有其他内容。",
                        messages=[{"role": "user", "content": f"问题：{prompt}\n\n输出格式：\n{{\n  \"query_rewrites\": {{\"sourcebot\": [\"...\"], \"qdrant\": [\"...\"]}},\n  \"entity_hints\": {{\"likely_repo\": \"...\", \"likely_dependency\": \"...\", \"likely_api_symbols\": [\"...\"]}},\n  \"precision_search\": {{\"extra_patterns\": [\"...\"], \"important_files\": [\"...\"]}}\n}}"}],
                    )
                    text = ""
                    for block in resp.content:
                        if hasattr(block, "text") and block.text:
                            text += block.text
                    llm_plan = validate_llm_plan(text)
                    if llm_plan:
                        plan = merge_llm_plan(plan, llm_plan)
            except Exception:
                pass  # Fallback: keep rule planner result

        with st.spinner("搜索中..."):
            src = search_sourcebot(prompt) if use_sourcebot else []
            qdr = search_qdrant(prompt) if use_qdrant else []
            merged = merge_results(src, qdr)

        for r in merged:
            if not r.get("content"):
                r["content"] = read_file_content(
                    r["repo"], r["path"],
                    r.get("start_line", 1), r.get("end_line", r.get("start_line", 1) + 30))

        ast_facts = search_ast_structure(prompt, merged) if use_ast else []
        graph_facts = search_graph_relations(prompt, merged) if use_graph else []

        # Build typed hits for ranking and evidence
        hits = to_hits(src, "sourcebot") + to_hits(qdr, "qdrant")
        for fact in ast_facts:
            hits.append(RetrievalHit("ast", "ast-service", "structure", "", fact, "structure"))
        for fact in graph_facts:
            hits.append(RetrievalHit("neo4j", "ast-service", "graph", "", fact, "graph"))
        ranked_repos = rank_repositories(hits)

        # Precision search for complex queries
        repos_root = os.environ.get("REPOS_ROOT", "/repos")
        if should_run_precision_search(plan, ranked_repos):
            top_repo = ranked_repos[0]["repo"]
            try:
                if plan.precision.get("read_manifests"):
                    hits.extend(read_manifest(repos_root, top_repo))
                for pattern in plan.precision.get("patterns", [])[:5]:
                    hits.extend(grep_repo(repos_root, top_repo, pattern, max_matches=10))
            except Exception as exc:
                st.warning(f"精搜过程出错: {exc}")

        # 构建来源统计标题
        src_parts = [f"{len(src)} Sourcebot", f"{len(qdr)} Qdrant"]
        if use_ast:
            src_parts.append(f"{len(ast_facts)} AST")
        if use_graph:
            src_parts.append(f"{len(graph_facts)} Neo4j")
        src_title = " + ".join(src_parts)

        with st.expander(f"📎 检索到 {len(merged)} 条 ({src_title})", expanded=False):
            if st.session_state.get("sourcebot_error"):
                st.warning(st.session_state.sourcebot_error)
            for r in merged:
                st.caption(f"[{r['source']}] `{r['repo']}/{r['path']}:{r['line']}` (score: {r.get('score','-')})")
                if r.get("content"):
                    st.code(r["content"][:2000], language=r.get("language", ""))

        if use_ast:
            with st.expander(f"AST 结构上下文 {len(ast_facts)} 条", expanded=False):
                if ast_facts:
                    for fact in ast_facts:
                        st.caption(fact)
                else:
                    st.caption("未找到相关结构信息")

        if use_graph:
            with st.expander(f"Neo4j 图关系上下文 {len(graph_facts)} 条", expanded=False):
                if graph_facts:
                    for fact in graph_facts:
                        st.caption(fact)
                else:
                    st.caption("未找到相关调用链")

        with st.spinner("LLM 思考中..."):
            evidence_pack = build_evidence_pack(prompt, plan, hits, ranked_repos)
            history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages[:-1]]
            try:
                answer = ask_llm_with_evidence(prompt, evidence_pack, history)
            except Exception:
                # Fallback to old flat answer path
                ctx_items = [{
                    "repo": r["repo"], "path": r["path"], "line": r["line"],
                    "language": r.get("language", ""), "content": r.get("content", ""),
                } for r in merged]
                if ast_facts:
                    ctx_items.append({
                        "repo": "ast-service",
                        "path": "structure",
                        "line": "",
                        "language": "text",
                        "content": "\n".join(ast_facts),
                    })
                if graph_facts:
                    ctx_items.append({
                        "repo": "ast-service",
                        "path": "graph",
                        "line": "",
                        "language": "text",
                        "content": "\n".join(graph_facts),
                    })
                ctx_json = json.dumps(ctx_items)
                answer = ask_llm(prompt, ctx_json, history)

        with st.expander(f"📊 Evidence Pack ({evidence_pack.get('confidence', '-')})", expanded=False):
            st.json({
                "intent": evidence_pack.get("intent"),
                "candidate_repos": evidence_pack.get("candidate_repos", [])[:5],
                "retrieval_coverage": evidence_pack.get("retrieval_coverage"),
                "evidence_count": len(evidence_pack.get("evidence", [])),
            })

        st.markdown(answer)
        st.session_state.messages.append({"role": "assistant", "content": answer})
        _persist_messages()
