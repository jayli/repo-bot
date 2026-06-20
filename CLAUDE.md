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
npm run open:neo4j             # 打开 Neo4j Browser http://localhost:7474

npm run dev:ast                # 本地开发 ast-service（停止容器内 ast-service，宿主机跑 uvicorn）
npm run graph:sync             # 同步 Neo4j 图关系（从 SQLite 重建）

npm run index                  # 全量重建向量索引（复制 index_code.py 到容器内执行）
npm run index:incr             # 增量向量索引
npm run search                 # 命令行语义搜索测试

npm run index:ast              # 全量 AST 结构索引（所有仓库）
npm run index:ast:incr         # 增量 AST 结构索引

npm run deploy                 # 快速部署 chat-ui（--no-deps --build）
npm run deploy:chat-ui         # 同上
npm run deploy:ast-service     # 快速部署 ast-service
npm run deploy:all             # 全量重建并部署所有服务

npm run install                # 一键安装脚本（新手部署，交互设置参数）

npm run build_push:chat-ui     # 构建当前平台 chat-ui 镜像并推送到阿里云 ACR
npm run build_push:ast-service # 构建当前平台 ast-service 镜像并推送
npm run build_push:chat-ui:arm / :x86  # 指定平台构建推送
npm run build_push:ast-service:arm / :x86
npm run build_push:chat-ui:all # 构建双平台（amd64+arm64）镜像并推送 + manifest
npm run build_push:ast-service:all

npm run docker_push:chat-ui    # 将本地已编译好的 chat-ui 镜像推送远端（自动识架构）
npm run docker_push:ast-service# 同上，ast-service
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

五个 Docker 服务（`docker-compose.yml`）：

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
    ├─ Neo4j (5-community, :7474/:7687) — 图关系存储，ast-service 派生写入
    │   节点: Repository / File / Symbol / ExternalSymbol / Module
    │   关系: CONTAINS / DEFINES / CALLS / IMPORTS
    │   约束: 唯一性约束 (repo+name, repo+path, repo+symbol_id 等)
    │
    ├─ ast-service (FastAPI :8502) — ast-grep 结构索引，SQLite + Neo4j 持久化
    │   全量/增量索引 → SQLite（权威） + Neo4j（派生图）→ REST API → Chat UI 结构上下文
    │   pytest 测试（API / DB / 索引 / SCIP / normalizer / 扫描器 / graph）
    │
    └─ Chat UI (Streamlit :8501) — chat-ui/app.py
        侧边栏可独立开关三路搜索（Qdrant / Sourcebot / AST 结构检索）
        三路结果 RRF 融合 + AST 调用关系作为结构上下文喂 LLM
        支持多轮对话（最近 10 轮送 LLM），会话以 URL token 持久化在服务端
```

**Embedding**：通过环境变量配置（OpenAI 兼容接口），支持任意 embedding 服务。关键变量：`EMBEDDING_MODEL`（默认 text-embedding-v4）、`EMBEDDING_DIM`（默认 1024）、`EMBEDDING_BASE_URL`（默认 DashScope 直连）、`EMBEDDING_API_KEY`（留空则 fallback `DASHSCOPE_API_KEY`）。`chat-ui/app.py` 和 `chat-ui/index_code.py` 均从这些环境变量读取，不再硬编码。

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
- **Docker 镜像发布**：chat-ui / ast-service 推送到阿里云 ACR 个人版 `crpi-x1zji86f6jpcd7t1.cn-hangzhou.personal.cr.aliyuncs.com/lijing00333/`。单平台镜像用 `latest-amd64` / `latest-arm64` 标签，双平台用 `latest` manifest。Qdrant 和 Sourcebot 为公共镜像，不纳入构建发布流程。
- **安装脚本**：`scripts/install.sh` 内联 `docker-compose.yml` 和 `config/sourcebot.json` 模板，引导新手交互输入关键参数后一键拉起全部服务。镜像从远端拉取（chat-ui/ast-service 来自 ACR，qdrant/sourcebot/neo4j 来自 Docker Hub/GHCR）。
- **Neo4j 图关系**：Neo4j 是派生图存储，SQLite 是权威来源。`NEO4J_ENABLED` 默认 true（Compose）/ false（Python `GraphConfig.from_env()`）。ast-service 通过 lifespan 管理 driver 单例，索引时在 `finish_index_run("ok")` 前刷新图关系。图刷新先 DETACH DELETE 整个 repo 再 MERGE 重建，每 repo 一个写事务，batch_size=1000。测试用 FakeDriver/FakeSession/FakeTransaction，默认不需要真实 Neo4j。Neo4j 5-community，驱动 `neo4j>=5.20,<6`。
