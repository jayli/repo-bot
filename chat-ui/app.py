"""
repo-bot Chat UI — 混合检索对话框
结合 Sourcebot（代码搜索） + Qdrant（语义检索），用 LLM 生成回答
"""
import os
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="repo-bot", page_icon="🤖", layout="wide")
st.title("repo-bot — 本地代码知识库")

# === 侧边栏 ===
with st.sidebar:
    st.header("配置")
    provider = st.selectbox("LLM", ["anthropic", "openai"], index=0)
    model = st.selectbox("Model", [
        "claude-sonnet-4-6", "claude-opus-4-8", "gpt-4o"
    ], index=0)
    st.divider()

    repos_root = os.path.expanduser(os.environ.get("REPOS_ROOT", "~/jayli"))
    st.caption(f"仓库目录: {repos_root}")

    st.divider()
    st.caption("Sourcebot: [localhost:3000](http://localhost:3000)")
    st.caption("Qdrant: [localhost:6333](http://localhost:6333/dashboard)")

# === 搜索后端 ===
def search_sourcebot(query: str) -> list[dict]:
    """调用 Sourcebot API 做代码搜索"""
    import requests
    url = os.environ.get("SOURCEBOT_URL", "http://localhost:3000") + "/api/search"
    try:
        resp = requests.get(url, params={"q": query, "limit": 10}, timeout=10)
        data = resp.json()
        results = []
        for r in data.get("results", data.get("matches", []))[:10]:
            results.append({
                "source": "sourcebot",
                "repo": r.get("repo", ""),
                "path": r.get("path", r.get("file", "")),
                "line": r.get("line", ""),
                "content": r.get("content", r.get("match", "")),
            })
        return results
    except Exception as e:
        return [{"source": "sourcebot", "error": str(e)}]


def search_qdrant(query: str, top_k: int = 10) -> list[dict]:
    """调用 Qdrant 做语义搜索"""
    from qdrant_client import QdrantClient
    from sentence_transformers import SentenceTransformer

    try:
        client = QdrantClient(url=os.environ.get("QDRANT_URL", "http://localhost:6333"))
        model = SentenceTransformer("BAAI/bge-m3")
        vector = model.encode(query).tolist()
        hits = client.search("jayli_code", query_vector=vector, limit=top_k)
        return [{
            "source": "qdrant",
            "repo": h.payload.get("repo", ""),
            "path": h.payload.get("path", ""),
            "line": f"L{h.payload.get('start_line', '')}",
            "content": "",
            "score": h.score,
        } for h in hits]
    except Exception as e:
        return [{"source": "qdrant", "error": str(e)}]


def merge_results(src_results: list, qdrant_results: list, top_k: int = 15) -> list[dict]:
    """RRF 融合两路检索结果"""
    k = 60
    scores = {}
    all_results = {}
    for rank, r in enumerate(src_results):
        key = f"{r.get('repo','')}:{r.get('path','')}:{r.get('line','')}"
        scores[key] = scores.get(key, 0) + 1 / (k + rank + 1)
        all_results[key] = r
    for rank, r in enumerate(qdrant_results):
        key = f"{r.get('repo','')}:{r.get('path','')}:{r.get('line','')}"
        scores[key] = scores.get(key, 0) + 1 / (k + rank + 1)
        all_results[key] = r
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
    return [all_results[k] for k, _ in ranked]


def chat_with_llm(question: str, context: list[dict]) -> str:
    """用 Anthropic API 根据检索到的上下文回答问题"""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "未配置 ANTHROPIC_API_KEY"

    # 构建上下文
    ctx_text = "\n\n".join([
        f"[{r['repo']}] {r['path']}:{r['line']}\n```\n{r.get('content', '')}\n```"
        for r in context if r.get("content")
    ][:10])

    if not ctx_text.strip():
        return "未找到相关代码，请尝试更精确的搜索词。"

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=os.environ.get("LLM_MODEL", "claude-sonnet-4-6"),
        max_tokens=2000,
        system="你是代码搜索助手。根据提供的代码片段回答用户问题，引用具体文件和行号。",
        messages=[{
            "role": "user",
            "content": f"上下文代码:\n{ctx_text}\n\n问题: {question}"
        }],
    )
    return resp.content[0].text


# === 主界面 ===
if "messages" not in st.session_state:
    st.session_state.messages = []

# 显示历史
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# 输入框
if prompt := st.chat_input("输入你的问题..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("搜索中..."):
            src = search_sourcebot(prompt)
            qdr = search_qdrant(prompt)
            merged = merge_results(src, qdr)

        # 显示检索结果
        with st.expander(f"📎 检索到 {len(merged)} 条结果", expanded=False):
            for r in merged:
                st.caption(f"[{r.get('source','')}] `{r.get('repo','')}/{r.get('path','')}:{r.get('line','')}`")
                if r.get("content"):
                    st.code(r["content"], language="")

        with st.spinner("LLM 思考中..."):
            answer = chat_with_llm(prompt, merged)
        st.markdown(answer)
        st.session_state.messages.append({"role": "assistant", "content": answer})
