# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 常用命令

全部通过 `npm run` 执行，定义在 `package.json`：

```bash
npm run up / down / restart   # Docker Compose 启停
npm run logs                   # 查看三个容器日志
npm run ps                     # 容器状态

npm run index                  # 全量重建向量索引（复制 index_code.py 到容器内执行）
npm run open                   # 打开 Chat UI http://localhost:8501
npm run open:sourcebot         # 打开 Sourcebot http://localhost:3000
npm run open:qdrant            # 打开 Qdrant Dashboard http://localhost:6333/dashboard
```

**重启 chat-ui 让代码变更生效**（不改模型/APi key 时 skip）：
```bash
docker compose build --no-cache chat-ui && docker compose up -d chat-ui
```

**仅环境变量变更需要重建容器**（`docker compose restart` 不会重新加载 `.env`）：
```bash
docker compose up -d chat-ui
```

## 架构

三个 Docker 服务（`docker-compose.yml`）：

```
REPOS_ROOT (只读挂载 :ro)
    │
    ├─ Sourcebot (v4, :3000) — Zoekt trigram 搜索引擎，精确/正则匹配
    │   配置: config/sourcebot.json，逐一列举仓库名
    │   ⚠️ 必须用 v4.0.0，v5 需要 PG + Redis + 加密密钥
    │   ⚠️ config key 名不能含 .，用了会违反 schema (^[a-zA-Z0-9_-]+$)
    │
    ├─ Qdrant (latest, :6333) — 向量数据库，1024 维 COSINE
    │   Collection: jayli_code_v4
    │
    └─ Chat UI (Streamlit :8501) — chat-ui/app.py
        左侧可独立开关两路搜索
        两路结果 RRF 融合后喂 LLM 生成回答
```

**Embedding**：`text-embedding-v4`，阿里云 DashScope 直连（`https://dashscope.aliyuncs.com/compatible-mode/v1`），不走 yui.cool。索引脚本 `chat-ui/index_code.py` 通过 `docker exec` 在容器内运行。

**LLM**：yui.cool 代理（`https://yui.cool:996`），Anthropic 协议格式，当前模型 `deepseek-v4-flash`。

## 关键注意

- **环境变量**：`.env` 不提交。`.env.example` 是模板。
- **Sourcebot v4 管理员**：email `admin@local.dev`，用户由 SQLite 直插创建，非注册页。清 `sourcebot_data` 卷会丢失登录态。
- **容器内 SSL**：yui.cool 自签证书 → `chat-ui/app.py` 里 `httpx.Client(verify=False)`。
- **ThinkingBlock**：DeepSeek 返回推理块无 `.text` 属性 → `ask_llm` 遍历 `resp.content` 找有 `.text` 的 block。
- **Qdrant API**：新版 qdrant-client 用 `client.query_points(collection, query=vector, limit=n)`，返回 `.points`。
- **DashScope 限制**：单次请求 ≤ 20 条、总字符 ≤ ~33000 → `index_code.py` 动态分批 + 单条截断 2000 字符。
- **容器内路径**：代码仓库在容器内挂载为 `/repos`，通过 `REPOS_ROOT` 环境变量控制，源代码只在宿主机，通过 `REPOS_ROOT` 环境变量指定。
