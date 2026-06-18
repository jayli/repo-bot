"""
repo-bot Chat UI — 混合检索对话框
Qdrant 语义搜索 + Sourcebot Zoekt 搜索
"""
import os, json, hashlib
import streamlit as st
from dotenv import load_dotenv
load_dotenv()

st.set_page_config(page_title="repo-bot", page_icon="🤖", layout="wide")
st.title("repo-bot — 本地代码知识库")

# === 侧边栏 ===
with st.sidebar:
    st.header("配置")
    st.caption(f"Qdrant: {os.environ.get('QDRANT_URL', 'http://localhost:6333')}")
    st.caption(f"Sourcebot: {os.environ.get('SOURCEBOT_URL', 'http://localhost:3000')}")
    st.caption(f"LLM: {os.environ.get('LLM_MODEL', 'claude-sonnet-4-6')}")
    st.divider()
    use_qdrant = st.checkbox("Qdrant 语义搜索", value=True)
    use_sourcebot = st.checkbox("Sourcebot 精确搜索", value=True)

# === 缓存模型（只加载一次）===
@st.cache_resource
def get_embed_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("all-MiniLM-L6-v2", device="cpu")

@st.cache_resource
def get_qdrant_client():
    from qdrant_client import QdrantClient
    return QdrantClient(url=os.environ.get("QDRANT_URL", "http://qdrant:6333"))

# === 搜索后端 ===
def search_qdrant(query: str, top_k: int = 10) -> list[dict]:
    client = get_qdrant_client()
    model = get_embed_model()
    vector = model.encode(query).tolist()
    hits = client.query_points("jayli_code", query=vector, limit=top_k)
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
    """通过 Zoekt HTTP API 搜索代码"""
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
    """从挂载的 /repos 目录读取代码内容"""
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

@st.cache_data(ttl=600)
def ask_llm(question: str, ctx_json: str) -> str:
    import anthropic
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

    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = anthropic.Anthropic(**client_kwargs)
    resp = client.messages.create(
        model=os.environ.get("LLM_MODEL", "claude-sonnet-4-6"),
        max_tokens=2000,
        system="你是代码知识库助手。根据提供的代码片段用中文回答用户问题，引用具体文件路径和行号。",
        messages=[{"role": "user", "content": f"上下文代码:\n{ctx_text}\n\n问题: {question}"}],
    )
    return resp.content[0].text

# === 主界面 ===
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("输入你的问题..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        # 检索
        with st.spinner("搜索中..."):
            src = search_sourcebot(prompt) if use_sourcebot else []
            qdr = search_qdrant(prompt) if use_qdrant else []
            merged = merge_results(src, qdr)

        # 读取文件内容
        for r in merged:
            if not r.get("content"):
                r["content"] = read_file_content(
                    r["repo"], r["path"],
                    r.get("start_line", 1), r.get("end_line", r.get("start_line", 1) + 30))

        # 显示搜索结果
        with st.expander(f"📎 检索到 {len(merged)} 条 ({len(src)} Sourcebot + {len(qdr)} Qdrant)", expanded=False):
            for r in merged:
                st.caption(f"[{r['source']}] `{r['repo']}/{r['path']}:{r['line']}` (score: {r.get('score','-')})")
                if r.get("content"):
                    st.code(r["content"][:2000], language=r.get("language", ""))

        # LLM 回答
        with st.spinner("LLM 思考中..."):
            ctx_json = json.dumps([{
                "repo": r["repo"], "path": r["path"], "line": r["line"],
                "language": r.get("language", ""), "content": r.get("content", ""),
            } for r in merged])
            answer = ask_llm(prompt, ctx_json)
        st.markdown(answer)
        st.session_state.messages.append({"role": "assistant", "content": answer})
