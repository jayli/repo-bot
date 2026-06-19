# 安装脚本设计

## 目标

一个 `install.sh` 完成 repo-bot 的新手部署，无需克隆仓库。

## 流程

1. **环境检查** — docker/docker compose 可用性、磁盘、内存
2. **交互问答** — 收集 7 个关键参数写入 .env
3. **拉取镜像** — 4 个 Docker 镜像 (qdrant + sourcebot 公共镜像, chat-ui + ast-service 阿里云私有镜像)
4. **生成配置** — sourcebot.json (基于 REPOS_ROOT 下仓库名)
5. **启动服务** — docker compose up -d，等待健康检查
6. **数据索引** — 向量索引 + AST 结构索引，提示 Sourcebot 后台 reindex

## 交互参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| REPOS_ROOT | ~/projects | 代码仓库根目录 |
| LLM_PROVIDER | anthropic | anthropic / openai |
| LLM_MODEL | claude-sonnet-4-6 | 模型名 |
| LLM_API_KEY | (必填) | LLM API Key |
| LLM_BASE_URL | (可选) | 自定义代理 |
| EMBEDDING_MODEL | text-embedding-v4 | Embedding 模型名 |
| EMBEDDING_BASE_URL | https://dashscope.aliyuncs.com/compatible-mode/v1 | Embedding URL |
| EMBEDDING_API_KEY | (可选) | Embedding Key |

## 代码改动

- `.env.example` / `.env` — 新增 EMBEDDING_MODEL, EMBEDDING_BASE_URL, EMBEDDING_API_KEY
- `docker-compose.yml` — chat-ui 服务传递 3 个新 env
- `chat-ui/app.py` — 硬编码 embedding 配置改为读 env
- `chat-ui/index_code.py` — 同上
- `scripts/install.sh` — 新建，完整安装流程 (heredoc 内联 docker-compose.yml 和 sourcebot.json 模板)
