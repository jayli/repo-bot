# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 常用命令

全部通过 `npm run` 执行，定义在 `package.json`：

```bash
npm run up / down / restart   # Docker Compose 启停
npm run logs                   # 查看容器日志
npm run ps                     # 容器状态
npm run clean                  # 清理悬空镜像

npm run init                   # 首次使用：生成 .env 和 config/sourcebot.json
npm run dev                    # 本地开发 chat-ui（停止容器内 chat-ui，宿主机直接跑 streamlit）
npm run open                   # 打开 Chat UI http://localhost:8501
npm run open:sourcebot         # 打开 Sourcebot http://localhost:3000
npm run open:qdrant            # 打开 Qdrant Dashboard http://localhost:6333/dashboard
npm run open:ast               # 打开 ast-service API 文档 http://localhost:8502/docs

npm run index                  # 全量重建向量索引（复制 index_code.py 到容器内执行）
npm run index:incr             # 增量向量索引
npm run search                 # 命令行语义搜索测试

npm run index:ast              # 全量 AST 结构索引（所有仓库）
npm run index:ast:incr         # 增量 AST 结构索引

npm run deploy                 # 快速部署 chat-ui（--no-deps --build）
npm run deploy:chat-ui         # 同上
npm run deploy:ast-service     # 快速部署 ast-service
npm run deploy:all             # 全量重建并部署所有服务
```

**chat-ui 代码变更生效**：
```bash
npm run deploy:chat-ui
```

**ast-service 代码变更生效**：
```bash
npm run deploy:ast-service
```

**仅 `.env` 变更**（不重建镜像，仅重启容器重读环境变量）：
```bash
docker compose up -d chat-ui
```

## 架构

四个 Docker 服务（`docker-compose.yml`）：

```
REPOS_ROOT (只读挂载 :ro)
    │
    ├─ Sourcebot (v4, :3000) — Zoekt trigram 搜索引擎，精确/正则匹配
    │   配置: config/sourcebot.json，逐一列举仓库名
    │   ⚠️ 必须用 v4.0.0，v5 需要 PG + Redis + 加密密钥
    │   ⚠️ config key 名不能含 .，用了会违反 schema (^[a-zA-Z0-9_-]+$)
    │
    ├─ Qdrant (latest, :6333) — 向量数据库，1024 维 COSINE
    │   Collection: QDRANT_COLLECTION 环境变量控制
    │
    ├─ ast-service (FastAPI :8502) — ast-grep 结构索引，SQLite 持久化
    │   全量/增量索引 → SQLite → REST API → Chat UI 结构上下文
    │   28 个 pytest 测试（API / DB / 索引 / SCIP / normalizer / 扫描器）
    │
    └─ Chat UI (Streamlit :8501) — chat-ui/app.py
        侧边栏可独立开关三路搜索（Qdrant / Sourcebot / AST 结构检索）
        三路结果 RRF 融合 + AST 调用关系作为结构上下文喂 LLM
        支持多轮对话（最近 10 轮送 LLM），会话以 URL token 持久化在服务端
```

**Embedding**：`text-embedding-v4`，阿里云 DashScope 直连（`https://dashscope.aliyuncs.com/compatible-mode/v1`），不走 yui.cool。

**LLM**：`https://yui.cool:996`，Anthropic 协议格式，默认模型由 `LLM_MODEL` 环境变量控制。

**ast-service SQLite 表结构**：Phase 1 表 `files` / `symbols` / `calls` / `imports` / `index_runs`；Phase 2 SCIP 表 `scip_documents` / `scip_symbols` / `scip_occurrences` / `scip_relationships`。所有查询 JOIN `files` 并过滤 `deleted_at IS NULL`。

## 关键注意

- **环境变量**：`.env` 和 `config/sourcebot.json` 不提交。`.env.example` 和 `config/sourcebot.json.example` 是模板，`npm run init` 一键复制（不会覆盖已有文件）。
- **Sourcebot v4 管理员**：首次启动后访问 http://localhost:3000 注册管理员账号。清 `sourcebot_data` 卷会丢失登录态。
- **Chat UI 认证**：URL token 鉴权（`?token=<sha256(user:pwd).hex[:16]>`），`_session_store`（`st.cache_resource`）按 token 持久化会话。退出登录或点「新对话」清除服务端会话。
- **容器内 SSL**：yui.cool 自签证书 → `chat-ui/app.py` 里 `httpx.Client(verify=False)` 和 `anthropic.Anthropic(http_client=...)`。
- **ThinkingBlock**：某些 LLM 返回推理块无 `.text` 属性 → `ask_llm` 遍历 `resp.content` 找有 `.text` 的 block。
- **Qdrant API**：新版 qdrant-client 用 `client.query_points(collection, query=vector, limit=n)`，返回 `.points`。
- **DashScope 限制**：单次请求 ≤ 10 条、总字符 ≤ ~33000 → `index_code.py` 动态分批 + 单条截断 2000 字符。
- **容器内路径**：代码仓库在容器内挂载为 `/repos`，通过 `REPOS_ROOT` 环境变量控制。
- **ast-service 测试**：在 `ast-service/` 目录下执行 `python -m pytest -v`（28 个测试）。测试不依赖数据库文件，用 fixture 仓库和内存 SQLite。
- **SCIP protobuf**：`ast-service/scip_proto/scip_pb2.py` 由真实 `scip.proto` 通过 `grpc_tools.protoc` 生成，非手写 stub。导出端点 `/scip/export?repo=xxx` 返回有效 SCIP payload，可被 `scip_pb2.Index.ParseFromString()` 反序列化。
- **调用图链接**：`link_calls_in_file()` 按符号行范围窄先匹配（防外层类覆盖内层方法），`link_callee_symbols()` 跨文件按名称匹配。
