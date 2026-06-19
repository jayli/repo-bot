# repo-bot

本地代码知识库 — 为 `REPOS_ROOT`（替换为你仓库集合根目录） 下所有仓库提供代码搜索 + 语义检索 + 结构检索 + AI 对话。

## 架构

```
REPOS_ROOT/* 仓库
    │
    ├──→ Sourcebot（Zoekt trigram 引擎） → 精确/正则匹配、代码导航
    ├──→ Qdrant（向量库）                → 语义检索、中文理解
    └──→ ast-service（ast-grep 结构索引）→ 调用关系、符号定义跳转
            │
    ┌── RRF 混合检索融合 ──→ Chat UI（Streamlit）← LLM 生成回答
              │
    Embedding: OpenAI 兼容接口（默认 DashScope text-embedding-v4）
    LLM:       Anthropic 协议兼容端点
```

## 新手安装

### 1）前期准备

安装前准备好以下信息（脚本会逐项询问，按 Enter 使用默认值）：

1. **代码仓库根目录** — 本地所有 git 仓库的父目录（默认 `~/projects`）
2. **LLM 配置** — Provider 类型（anthropic/openai）、API Key、模型名、代理地址（可选）
3. **Embedding 配置** — 模型名（默认 `text-embedding-v4`）、维度（默认 1024）、API URL、API Key（留空则复用 LLM Key）
4. **向量库集合名** — Qdrant collection 名称（默认 `codebase`）
5. **Chat UI 登录** — 用户名/密码（默认 `admin`/`admin123`）
6. **Sourcebot API Key** — 可先留空，安装后去 `http://localhost:3000/~/settings/apiKeys` 后台生成，再填入 `~/.repo-bot/.env`

### 2）执行安装

无需克隆仓库，一个脚本完成全部部署：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/jayli/repo-bot/main/scripts/install.sh)
```

脚本会依次完成：
1. **环境检查** — 确认 Docker、磁盘空间、内存
2. **交互配置** — 引导填写 LLM Key、仓库路径、Embedding 参数等，自动生成 `.env`
3. **拉取镜像** — 从阿里云 ACR 拉取 chat-ui/ast-service，从 Docker Hub/GHCR 拉取 Qdrant/Sourcebot
4. **启动服务** — `docker compose up -d`，等待全部就绪
5. **数据索引** — 可选立即执行向量索引和 AST 结构索引

安装完成后访问：

| 服务 | 地址 | 说明 |
|------|------|------|
| Chat UI | http://localhost:8501 | AI 代码问答 |
| Sourcebot | http://localhost:3000 | 代码搜索引擎 |
| Qdrant Dashboard | http://localhost:6333/dashboard | 向量库面板 |
| AST API Docs | http://localhost:8502/docs | 结构检索 API |

> **提示**：安装后需前往 Sourcebot 注册管理员账号，在设置页创建 API Key 并填入 `~/.repo-bot/.env` 中的 `SOURCEBOT_API_KEY`，同时触发 reindex 构建代码搜索索引。

## 开发者

### 命令总览

所有命令通过 `npm run` 执行：

```bash
# 服务管理
npm run up / down / restart        # Docker Compose 启停
npm run logs                        # 查看容器日志
npm run ps                          # 容器状态

# 本地构建部署
npm run deploy                      # 快速重建 chat-ui（代码变更后生效）
npm run deploy:chat-ui              # 同上
npm run deploy:ast-service          # 快速重建 ast-service
npm run deploy:all                  # 全量重建所有服务

# 数据索引
npm run index                       # 全量重建向量索引
npm run index:incr                  # 增量向量索引
npm run index:ast                   # 全量 AST 结构索引
npm run index:ast:incr              # 增量 AST 结构索引

# 镜像发布
npm run build_push:chat-ui          # 构建当前平台 chat-ui 并推送 ACR
npm run build_push:ast-service      # 同上
npm run build_push:chat-ui:arm/:x86 # 指定平台
npm run build_push:chat-ui:all      # 双平台构建推送 + manifest
npm run docker_push:chat-ui         # 推送已有本地镜像到 ACR

# 开发
npm run dev                         # 本地开发 chat-ui（宿主机跑 Streamlit）
npm run init                        # 初始化 .env 和 config/sourcebot.json
npm run clean                       # 清理悬空镜像
```

### 初始化

```bash
git clone git@github.com:jayli/repo-bot.git && cd repo-bot
npm run init    # 生成 .env 和 config/sourcebot.json
# 编辑 .env、config/sourcebot.json
npm run up      # 启动服务
npm run index   # 构建向量索引
npm run index:ast # 构建 AST 索引
```

## 配置说明

- **REPOS_ROOT**（`.env`）：代码仓库根目录，默认 `~/projects`，通过 Docker volume 只读挂载到容器 `/repos`
- **Sourcebot 仓库列表**：`config/sourcebot.json` 中逐一列举（key 名不能含 `.`）
- **LLM**：支持 Anthropic / OpenAI 协议，`.env` 中配置 `LLM_PROVIDER`、`ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL` 等
- **Embedding**：OpenAI 兼容接口，通过 `EMBEDDING_MODEL`、`EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY` 配置（默认 DashScope），留空 `EMBEDDING_API_KEY` 则复用 LLM Key

## 项目结构

```
repo-bot/
├── docker-compose.yml              # 四服务编排
├── package.json                    # npm run 命令入口
├── .env.example                    # 环境变量模板
├── config/
│   └── sourcebot.json              # Sourcebot 仓库连接配置
├── scripts/
│   ├── install.sh                  # 一键安装脚本
│   ├── docker-build-push.sh        # 构建+推送 ACR
│   ├── docker-push-existing.sh     # 推送已有镜像
│   ├── index-vectors.py            # 向量化索引
│   └── incremental-index.sh        # 增量索引
├── chat-ui/
│   ├── app.py                      # Streamlit Chat 界面
│   ├── index_code.py               # 向量化索引
│   ├── requirements.txt
│   └── Dockerfile
├── ast-service/
│   ├── main.py                     # FastAPI 入口
│   ├── indexer.py                  # ast-grep 索引
│   ├── tests/                      # 28 个 pytest
│   └── Dockerfile
└── CLAUDE.md                       # 开发指南
```
