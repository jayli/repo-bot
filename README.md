# repo-bot

本地代码知识库 — 为 `REPOS_ROOT` 下所有仓库提供代码搜索 + 语义检索 + AI 对话。

## 架构

```
REPOS_ROOT/* 仓库
    │
    ├──→ Sourcebot（Zoekt trigram 引擎）    → 精确匹配、正则搜索、代码导航
    │
    └──→ Qdrant（向量库，1024d COSINE）       → 语义检索、中文理解
            │
    ┌── RRF 混合检索融合 ──→ Chat UI（Streamlit）← LLM 生成回答
              │
    Embedding: text-embedding-v4（DashScope 直连）
    LLM:       deepseek-v4-flash（yui.cool 代理）
```

## 快速开始

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 API Key
```

### 2. 启动服务

```bash
npm run up
```

### 3. 构建向量索引

```bash
npm run index
```

### 4. 访问界面

```bash
npm run open              # Chat UI      → http://localhost:8501
npm run open:sourcebot    # 代码搜索      → http://localhost:3000
npm run open:qdrant       # 向量库面板   → http://localhost:6333/dashboard
```

## 日常命令

| 命令 | 说明 |
|------|------|
| `npm run up` | 启动所有服务 |
| `npm run down` | 停止所有服务 |
| `npm run restart` | 重启 |
| `npm run logs` | 查看容器日志 |
| `npm run ps` | 查看容器状态 |
| `npm run index` | 全量重建向量索引 |
| `npm run open` | 打开 Chat UI |
| `npm run open:sourcebot` | 打开 Sourcebot |
| `npm run open:qdrant` | 打开 Qdrant 面板 |

## 配置说明

- **REPOS_ROOT**（`.env`）：代码仓库根目录，默认 `~/projects`，通过 Docker volume 只读挂载到容器 `/repos`
- **Sourcebot 仓库列表**：`config/sourcebot.json` 中逐一列举（key 名不能含 `.`）
- **LLM 对话**：走 Anhropic 协议兼容端点（yui.cool），`.env` 中配置 `ANTHROPIC_BASE_URL`
- **Embedding 向量化**：走阿里云 DashScope 直连，`DASHSCOPE_API_KEY` 配置

## 项目结构

```
repo-bot/
├── docker-compose.yml           # Sourcebot + Qdrant + Chat UI
├── package.json                 # npm run 命令入口
├── config/
│   └── sourcebot.json           # Sourcebot 仓库连接配置
├── scripts/
│   ├── index-vectors.py         # 向量化索引（旧入口，qdrant/chroma）
│   └── incremental-index.sh     # 增量更新
├── chat-ui/
│   ├── app.py                   # Streamlit Chat 界面（混合检索 + LLM）
│   ├── index_code.py            # 向量化索引（当前主入口）
│   ├── requirements.txt
│   └── Dockerfile
├── .env.example                 # 环境变量模板
└── CLAUDE.md                    # 开发指南
```
