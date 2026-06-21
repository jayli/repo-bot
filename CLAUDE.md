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

**运行测试**：
```bash
python3 -m pytest chat-ui/tests -q          # chat-ui 全部（47 个）
cd ast-service && python -m pytest -v       # ast-service（50 个）
python3 -m pytest chat-ui/tests/test_agent_loop.py -q  # 仅检索循环测试
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
        侧边栏可独立开关四路检索（Qdrant / Sourcebot / AST / Neo4j 图谱）
        通过 run_retrieval_loop() 编排多轮检索 → RRF 融合 → Evidence Pack → LLM 生成回答
        支持多轮对话（最近 10 轮送 LLM），会话以 URL token 持久化在服务端
```

### Chat UI 检索模块 (`chat-ui/retrieval/`)

| 模块 | 职责 |
|------|------|
| `agent_loop.py` | 检索编排器：`run_retrieval_loop()` — 多轮检索循环、query 扩展、gap 观察、precision 目标选择、discovered-term 提取。不依赖 Streamlit/Anthropic/OpenAI，不直接读 REPOS_ROOT |
| `planner.py` | 规则规划器：`plan_query()` 意图分类+实体提取、`merge_llm_plan()` 合并 LLM 规划建议（含 `entity_hints` 保留） |
| `ranking.py` | 仓库排序：`rank_repositories()` 全证据排序、`rank_code_repositories()` 排除 `SYNTHETIC_REPOS`、`should_run_precision_search()` |
| `precision.py` | 本地精搜工具：`read_manifest()`、`local_tool_grep()`、`local_tool_read()`、`local_tool_list()`，均需 `repos_root` 参数 |
| `evidence.py` | Evidence Pack 构建：`build_evidence_pack()`、`evidence_tier()`、`_repo_roots()`，导入共享 `SYNTHETIC_REPOS` |
| `models.py` | 数据类：`RetrievalPlan`、`RetrievalHit`、`EvidenceItem` |

### Multi-Round Retrieval Loop 核心流程

```
plan_query() → [optional LLM merge] → expand_queries()
    → Round 1: Sourcebot + Qdrant global search
    → Snippet hydration (read_file_content)
    → AST + Graph search (基于 candidate symbols)
    → confirmed_repos 提取 (排除 SYNTHETIC_REPOS)
    → rank_code_repositories()
    → observe_gaps() → LocalAction 执行 (仅 confirmed_repos)
    → extract_discovered_terms() → Round 2 follow-up Sourcebot 查询
    → 循环至 max_rounds 或无新 hits
    → RetrievalLoopResult → build_evidence_pack() → LLM 合成
```

关键设计决策：
- `RetrievalBackends` dataclass 注入所有后端函数，测试用 fake backends，生产由 app.py/test_chat.py 传入
- `confirmed_repos` 仅来自全局搜索结果中非 synthetic 的 repo 名，LLM `entity_hints.likely_repo` 只有出现在 confirmed 后才被接受
- `observe_gaps()` 纯函数：依赖 `precision_search`/`local_tool` 命中判定是否已满足，不依赖 Sourcebot/Qdrant 片段
- `SYNTHETIC_REPOS = {"ast-service"}` 定义在 ranking.py，evidence.py 和 agent_loop.py 共用
- Round 2 先执行本地 gap actions，再用 discovered terms 做全局 Sourcebot 查询
- `read_file_content(repo, path, start_line, end_line)` 签名不同于 precision 工具（历史原因：闭包 REPOS_ROOT），由调用方注入

### Chat UI 其他模块

| 模块 | 职责 |
|------|------|
| `prompts/templates.py` | 系统提示模板：`BASE_SYSTEM`、`TOOL_CATALOG`（四路检索+精搜工具描述及使用原则）、`EVIDENCE_RULES`、`DEPENDENCY_TEMPLATE`、`GENERIC_TEMPLATE` |
| `prompts/synthesizer.py` | `build_system_prompt(template)` + `build_user_message(question, evidence_pack)` |
| `sourcebot_client.py` | Sourcebot v4 API client |
| `app.py` | Streamlit UI，通过 `run_retrieval_loop()` 调用检索，保留侧边栏开关和 expander 展示 |
| `test_chat.py` | CLI 测试脚本，同样通过 `run_retrieval_loop()` 调用检索 |

### 测试架构 (`chat-ui/tests/`)

47 个测试，7 个文件，均用动态 import 模式（无 pytest 插件依赖）：

```python
def load_module(name):
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(name, root / (name.replace(".", "/") + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
```

- `test_agent_loop.py` (15 tests): `FakeBackends` / `FakeBackendsWithHits` 注入，验证 planner rewrites 执行、query 扩展、confirmed repos 门控、gap 观察、AST/Graph 调用、max_rounds、early stop
- `test_ranking.py` (4 tests): 验证 `rank_code_repositories` 排除 synthetic、`rank_repositories` 保留全证据
- `test_evidence.py` (3 tests): 验证 tier 分级、confidence、repo_roots 排除 synthetic
- `test_planner.py` (5 tests): 验证意图分类、entity 提取、LLM plan 合并、entity_hints 保留
- `test_precision.py` (15 tests): 验证本地工具（manifest、grep、read、list）和路径安全
- `test_sourcebot_client.py` (3 tests)
- `test_synthesizer.py` (2 tests): 验证系统提示包含证据规则和工具约束

设计文档位于 `docs/superpowers/specs/` 和 `docs/superpowers/plans/`。

## 关键注意

- **环境变量**：`.env` 和 `config/sourcebot.json` 不提交。`.env.example` 和 `config/sourcebot.json.example` 是模板，`npm run init` 一键复制（不会覆盖已有文件）。
- **Sourcebot v4 管理员**：首次启动后访问 http://localhost:3000 注册管理员账号。清 `sourcebot_data` 卷会丢失登录态。
- **Chat UI 认证**：URL token 鉴权（`?token=<sha256(user:pwd).hex[:16]>`），`_session_store`（`st.cache_resource`）按 token 持久化会话。退出登录或点「新对话」清除服务端会话。
- **容器内 SSL**：yui.cool 自签证书 → `chat-ui/app.py` 里 `httpx.Client(verify=False)` 和 `anthropic.Anthropic(http_client=...)`。
- **ThinkingBlock**：某些 LLM 返回推理块无 `.text` 属性 → `ask_llm` 遍历 `resp.content` 找有 `.text` 的 block。
- **Qdrant API**：新版 qdrant-client 用 `client.query_points(collection, query=vector, limit=n)`，返回 `.points`。
- **DashScope 限制**：单次请求 ≤ 10 条、总字符 ≤ ~33000 → `index_code.py` 动态分批 + 单条截断 2000 字符。
- **容器内路径**：代码仓库在容器内挂载为 `/repos`，通过 `REPOS_ROOT` 环境变量控制。
- **ast-service 测试**：在 `ast-service/` 目录下执行 `python -m pytest -v`（50 个测试）。测试不依赖数据库文件，用 fixture 仓库和内存 SQLite。
- **SCIP protobuf**：`ast-service/scip_proto/scip_pb2.py` 由真实 `scip.proto` 通过 `grpc_tools.protoc` 生成，非手写 stub。导出端点 `/scip/export?repo=xxx` 返回有效 SCIP payload，可被 `scip_pb2.Index.ParseFromString()` 反序列化。
- **调用图链接**：`link_calls_in_file()` 按符号行范围窄先匹配（防外层类覆盖内层方法），`link_callee_symbols()` 跨文件按名称匹配。
- **Docker 镜像发布**：chat-ui / ast-service 推送到阿里云 ACR 个人版 `crpi-x1zji86f6jpcd7t1.cn-hangzhou.personal.cr.aliyuncs.com/lijing00333/`。单平台镜像用 `latest-amd64` / `latest-arm64` 标签，双平台用 `latest` manifest。Qdrant 和 Sourcebot 为公共镜像，不纳入构建发布流程。
- **安装脚本**：`scripts/install.sh` 内联 `docker-compose.yml` 和 `config/sourcebot.json` 模板，引导新手交互输入关键参数后一键拉起全部服务。镜像从远端拉取（chat-ui/ast-service 来自 ACR，qdrant/sourcebot/neo4j 来自 Docker Hub/GHCR）。
- **Neo4j 图关系**：Neo4j 是派生图存储，SQLite 是权威来源。`NEO4J_ENABLED` 默认 true（Compose）/ false（Python `GraphConfig.from_env()`）。ast-service 通过 lifespan 管理 driver 单例，索引时在 `finish_index_run("ok")` 前刷新图关系。图刷新先 DETACH DELETE 整个 repo 再 MERGE 重建，每 repo 一个写事务，batch_size=1000。测试用 FakeDriver/FakeSession/FakeTransaction，默认不需要真实 Neo4j。Neo4j 5-community，驱动 `neo4j>=5.20,<6`。
- **Chat UI 图检索**：`search_graph_relations()` 通过 `/graph/impact` 查询多跳调用链，两级 fallback：候选符号匹配 → `/symbols?repo=X` 取 top symbols → 逐个查 impact。`query_impact` 和 `query_call_paths` 同时匹配 `Symbol` 和 `ExternalSymbol` 标签（未解析调用目标是 ExternalSymbol）。CALL 子查询使用 `CALL (s) { ... }` 语法（Neo4j 5.x），非旧式 `CALL { WITH s ... }`。
- **本地开发 SQLite 同步**：`dev` / `dev:ast` 脚本启动时自动检测 `.data/ast.sqlite`，若为空则从容器 `docker cp repo-bot-ast-service-1:/data/ast.sqlite` 同步到宿主机。
- **agent_loop.py 约束**：不可导入 Streamlit，不可直接构造 Anthropic/OpenAI 客户端，不可直接读取 `REPOS_ROOT`/`os.environ`。`repos_root` 由调用方显式传入。
- **entity ≠ repo**：`RetrievalPlan.entities["subject"]`/`["object"]` 是搜索词（包名、符号），不是仓库名。仓库资格必须通过全局搜索结果确认。`entity_hints.likely_repo` 仅在被 confirmed_repos 或 ranked_repos 证实后才能用于 precision 目标选择。
- **app.py 和 test_chat.py 共用一个检索路径**：两者都通过 `RetrievalBackends(...)` 注入后端函数 + `run_retrieval_loop(...)` 执行检索。不要在这两个文件中重复实现检索逻辑。
