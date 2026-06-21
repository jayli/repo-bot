"""
repo-bot Chat UI — 混合检索对话框
Qdrant 语义搜索 (text-embedding-v4) + Sourcebot Zoekt 搜索
"""
import os, json
from openai import OpenAI
import streamlit as st
from dotenv import load_dotenv
from sourcebot_client import search_sourcebot as search_sourcebot_client
from retrieval.agent_loop import RetrievalBackends, run_retrieval_loop
from retrieval.answer_loop import run_answer_tool_loop
from retrieval.planner import validate_llm_plan
from retrieval.precision import read_manifest, local_tool_grep, local_tool_read, local_tool_list
from retrieval.evidence import build_evidence_pack
from retrieval.tool_dispatch import TOOLS, dispatch_tool
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

@st.cache_resource
def get_indexed_repos() -> list[str]:
    import itertools
    try:
        client = get_qdrant_client()
        collection = os.environ.get("QDRANT_COLLECTION", "codebase")
        repos: set[str] = set()
        # scroll first ~5000 points to collect unique repo names
        points, next_offset = client.scroll(collection, limit=1000)
        for i in range(5):
            for p in points:
                repo = p.payload.get("repo", "")
                if repo:
                    repos.add(repo)
            if next_offset:
                points, next_offset = client.scroll(collection, limit=1000, offset=next_offset)
            else:
                break
        return sorted(repos)
    except Exception:
        return []

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

def ask_llm_with_evidence(
    question: str,
    evidence_pack: dict,
    history: list[dict] | None = None,
    on_tool_call=None,
) -> str:
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

    repos_root = os.environ.get("REPOS_ROOT", "/repos")

    def _dispatch(name, args):
        return dispatch_tool(
            name,
            args,
            evidence_pack=evidence_pack,
            repos_root=repos_root,
            search_sourcebot=search_sourcebot,
            search_qdrant=search_qdrant,
            search_ast_structure=search_ast_structure,
            search_graph_relations=search_graph_relations,
            read_manifest=read_manifest,
            local_tool_grep=local_tool_grep,
            local_tool_read=local_tool_read,
            local_tool_list=local_tool_list,
        )

    return run_answer_tool_loop(
        client=client,
        model=os.environ.get("LLM_MODEL", "claude-sonnet-4-6"),
        system=build_system_prompt(evidence_pack.get("answer_template", "generic_code_answer")),
        messages=messages,
        tools=TOOLS,
        dispatch_tool=_dispatch,
        max_tokens=3000,
        on_tool_call=on_tool_call,
    )

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
        # LLM Planner adapter
        def llm_plan_query(question: str, plan) -> dict:
            if not USE_LLM_PLANNER:
                return {}
            try:
                import anthropic, httpx
                api_key = os.environ.get("ANTHROPIC_API_KEY", "")
                if not api_key:
                    return {}
                base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
                client_kwargs = {"api_key": api_key}
                if base_url:
                    client_kwargs["base_url"] = base_url
                client_kwargs["http_client"] = httpx.Client(verify=False)
                client = anthropic.Anthropic(**client_kwargs)

                context = plan.entities.get("round1_context", "") if hasattr(plan, "entities") else ""
                context_block = f"\n## 初步检索命中\n{context}" if context else ""

                resp = client.messages.create(
                    model=os.environ.get("LLM_MODEL", "claude-sonnet-4-6"),
                    max_tokens=500,
                    system="你是一个代码检索规划器。根据用户问题和初步检索结果返回 JSON 格式的检索增强建议。只输出 JSON，不要有其他内容。\n\nintent 可选值：dependency_relation（依赖关系）、call_chain（调用链）、implementation_location（实现定位）、troubleshooting（排错）、generic_code_answer（通用问答）。根据问题的实际语义选择，不要被字面关键词误导。\n\n当提供初步检索命中时，请根据实际代码内容判断：哪些仓库最相关、代码中出现了哪些关键符号/依赖、需要进一步搜索什么模式。",
                    messages=[{"role": "user", "content": f"问题：{question}{context_block}\n\n输出格式：\n{{\n  \"intent\": \"generic_code_answer\",\n  \"search_facets\": [\"...\"],\n  \"repo_candidates\": [\"...\"],\n  \"query_rewrites\": {{\"sourcebot\": [\"...\"], \"qdrant\": [\"...\"]}},\n  \"entity_hints\": {{\"likely_repo\": \"...\", \"likely_dependency\": \"...\", \"likely_api_symbols\": [\"...\"]}},\n  \"precision_search\": {{\"extra_patterns\": [\"...\"], \"important_files\": [\"...\"]}}\n}}"}],
                )
                llm_text = ""
                for block in resp.content:
                    if hasattr(block, "text") and block.text:
                        llm_text += block.text
                llm_plan = validate_llm_plan(llm_text)
                return llm_plan if llm_plan else {}
            except Exception:
                return {}

        with st.spinner("搜索中..."):
            repos_root = os.environ.get("REPOS_ROOT", "/repos")
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
                available_repos=get_indexed_repos(),
            )
            result = run_retrieval_loop(
                prompt,
                repos_root=repos_root,
                backends=backends,
                use_sourcebot=use_sourcebot,
                use_qdrant=use_qdrant,
                use_ast=use_ast,
                use_graph=use_graph,
            )

        plan = result.plan
        hits = result.hits
        merged = result.merged
        ast_facts = result.ast_facts
        graph_facts = result.graph_facts
        ranked_repos = result.ranked_repos

        # 构建来源统计标题
        src_count = len([h for h in hits if h.source == "sourcebot"])
        qdr_count = len([h for h in hits if h.source == "qdrant"])
        src_parts = [f"{src_count} Sourcebot", f"{qdr_count} Qdrant"]
        if use_ast:
            src_parts.append(f"{len(ast_facts)} AST")
        if use_graph:
            src_parts.append(f"{len(graph_facts)} Neo4j")
        src_title = " + ".join(src_parts)

        # Round diagnostics
        with st.expander(f"检索轮次 {len(result.rounds)}", expanded=False):
            for round_info in result.rounds:
                st.caption(
                    f"Round {round_info.index}: Sourcebot={len(round_info.sourcebot_queries)} "
                    f"Qdrant={len(round_info.qdrant_queries)} AST={len(round_info.ast_queries)} "
                    f"Graph={len(round_info.graph_queries)} Local={len(round_info.local_actions)} "
                    f"NewHits={round_info.new_hits}"
                )
                if round_info.notes:
                    for note in round_info.notes:
                        st.caption(f"  ⚠️ {note}")

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
            evidence_pack = build_evidence_pack(prompt, plan, hits, ranked_repos, available_repos=get_indexed_repos())
            history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages[:-1]]
            tool_trace: list[dict] = []

            def _record_tool_call(name, args, result_text):
                preview = result_text[:300].replace("\n", " ").strip()
                if len(result_text) > 300:
                    preview += f" ... ({len(result_text)} chars total)"
                tool_trace.append({"name": name, "args": args, "preview": preview})

            try:
                answer = ask_llm_with_evidence(prompt, evidence_pack, history, on_tool_call=_record_tool_call)
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
                "evidence_count": len(evidence_pack.get("evidence", [])),
            })

        if tool_trace:
            with st.expander(f"🔧 LLM 工具调用 {len(tool_trace)} 次", expanded=False):
                for item in tool_trace:
                    st.caption(f"{item['name']}({json.dumps(item['args'], ensure_ascii=False)[:160]})")
                    st.code(item["preview"], language="text")

        st.markdown(answer)
        st.session_state.messages.append({"role": "assistant", "content": answer})
        _persist_messages()
