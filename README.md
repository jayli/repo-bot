# repo-bot

本地代码知识库 — 为 `~/jayli/` 下所有仓库提供代码搜索 + 语义检索 + AI 对话。

## 架构

```
~/jayli/* 仓库
    │
    ├──→ Sourcebot（trigram 代码搜索）    → 精确搜索、正则、代码导航
    │
    └──→ Qdrant（向量库，bge-m3）         → 语义检索、中文理解
            │
    ┌── RRF 混合检索融合 ──→ Chat UI（Streamlit）← LLM 生成回答
```

## 快速开始

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 ANTHROPIC_API_KEY
```

### 2. 启动服务

```bash
docker compose up -d
```

### 3. 构建向量索引

```bash
pip install sentence-transformers qdrant-client
python scripts/index-vectors.py
```

### 4. 访问界面

| 服务 | 地址 |
|------|------|
| Chat UI | http://localhost:8501 |
| Sourcebot | http://localhost:3000 |
| Qdrant Dashboard | http://localhost:6333/dashboard |

## 增量更新

```bash
# 手动
bash scripts/incremental-index.sh

# 定时 cron（每 4 小时）
0 */4 * * * $HOME/jayli/repo-bot/scripts/incremental-index.sh
```

## 项目结构

```
repo-bot/
├── docker-compose.yml           # Sourcebot + Qdrant + Chat UI
├── config/
│   └── sourcebot.json           # Sourcebot 配置
├── scripts/
│   ├── index-vectors.py         # 向量化索引（qdrant/chroma）
│   └── incremental-index.sh     # 增量更新
├── chat-ui/
│   ├── app.py                   # Streamlit Chat 界面
│   ├── requirements.txt
│   └── Dockerfile
└── .env.example
```
