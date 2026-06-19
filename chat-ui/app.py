"""
repo-bot Chat UI — 混合检索对话框
Qdrant 语义搜索 (text-embedding-v4) + Sourcebot Zoekt 搜索
"""
import os, json
from openai import OpenAI
import streamlit as st
from dotenv import load_dotenv
load_dotenv()

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
        if st.form_submit_button("登录"):
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
        api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

@st.cache_resource
def get_qdrant_client():
    from qdrant_client import QdrantClient
    return QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))

def embed_query(text: str) -> list[float]:
    client = get_openai_client()
    resp = client.embeddings.create(model="text-embedding-v4", input=text, dimensions=1024, encoding_format="float")
    return resp.data[0].embedding

# === 侧边栏 ===
with st.sidebar:
    st.header("配置")
    st.caption(f"Embedding: text-embedding-v4 (DashScope 直连)")
    st.caption(f"Qdrant: {os.environ.get('QDRANT_URL', 'http://localhost:6333')}")
    st.caption(f"Sourcebot: {os.environ.get('SOURCEBOT_URL', 'http://localhost:3000')}")
    st.caption(f"LLM: {os.environ.get('LLM_MODEL', 'claude-sonnet-4-6')} (yui.cool)")
    st.divider()
    use_qdrant = st.checkbox("Qdrant 语义搜索（向量库）", value=True)
    use_sourcebot = st.checkbox("Sourcebot 精确搜索（匹配关键词）", value=True)
    use_ast = st.checkbox("AST 结构检索", value=True)
    st.caption(f"AST: {os.environ.get('AST_SERVICE_URL', 'http://ast-service:8502')}")
    st.divider()
    if st.button("🆕 新对话", use_container_width=True):
        token = st.session_state.get("_session_token")
        if token:
            _session_store().pop(token, None)
        st.session_state.messages = []
        st.rerun()
    st.caption(f"👤 {os.environ.get('CHAT_USERNAME', 'admin')}")
    st.markdown("<a href='?logout=1' style='font-size:14px;'>退出登录</a>", unsafe_allow_html=True)

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
    import requests
    url = os.environ.get("SOURCEBOT_URL", "http://sourcebot:3000") + "/api/search/stream"
    try:
        resp = requests.post(url, json={"query": query, "limit": top_k}, timeout=10)
        data = resp.json()
        results = []
        for r in data.get("results", [])[:top_k]:
            results.append({
                "source": "sourcebot",
                "repo": r.get("repo", ""),
                "path": r.get("fileName", r.get("path", "")),
                "line": r.get("line", ""),
                "content": r.get("match", r.get("content", "")),
            })
        return results
    except Exception:
        return []

def read_file_content(repo: str, path: str, start_line: int, end_line: int) -> str:
    fp = os.path.join(os.environ.get("REPOS_ROOT", "/repos"), path)
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[max(0, start_line - 1):end_line])
    except Exception:
        return ""

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

        with st.expander(f"📎 检索到 {len(merged)} 条 ({len(src)} Sourcebot + {len(qdr)} Qdrant)", expanded=False):
            for r in merged:
                st.caption(f"[{r['source']}] `{r['repo']}/{r['path']}:{r['line']}` (score: {r.get('score','-')})")
                if r.get("content"):
                    st.code(r["content"][:2000], language=r.get("language", ""))

        if ast_facts:
            with st.expander(f"结构上下文 {len(ast_facts)} 条", expanded=False):
                for fact in ast_facts:
                    st.caption(fact)

        with st.spinner("LLM 思考中..."):
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
            ctx_json = json.dumps(ctx_items)
            history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages[:-1]]
            answer = ask_llm(prompt, ctx_json, history)
        st.markdown(answer)
        st.session_state.messages.append({"role": "assistant", "content": answer})
        _persist_messages()
