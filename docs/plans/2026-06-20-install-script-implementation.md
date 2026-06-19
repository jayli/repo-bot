# 安装脚本 + Embedding 配置抽离 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 创建一键安装脚本 install.sh，并将 embedding 硬编码配置抽离到 .env 环境变量。

**Architecture:** install.sh 内联 docker-compose.yml 和 sourcebot.json 模板，交互收集 8 个参数写入 .env，从阿里云 + Docker Hub 拉取 4 个镜像，启动服务后执行向量索引和 AST 索引。

**Tech Stack:** Bash, Docker Compose, Python (Streamlit/FastAPI), Qdrant, Sourcebot v4

---

### Task 1: .env.example 新增 Embedding 变量

**Files:**
- Modify: `.env.example`

**Step 1: 修改 .env.example**

将原 `DASHSCOPE_API_KEY` 行替换为独立的 Embedding 配置段：

```diff
-# 阿里云 DashScope Embedding（直连）
-DASHSCOPE_API_KEY=sk-...
+# === Embedding 配置 ===
+# 模型名（OpenAI 兼容接口，如 text-embedding-v4 / text-embedding-3-large / bge-m3）
+EMBEDDING_MODEL=text-embedding-v4
+# Embedding API 地址
+EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
+# Embedding API Key（留空则复用 LLM_API_KEY）
+EMBEDDING_API_KEY=sk-...
```

**Step 2: 同步修改 .env**

同样替换 `.env` 中的 DASHSCOPE_API_KEY 段。

**Step 3: 提交**

```bash
git add .env.example .env
git commit -m "feat(config): 将 embedding 配置从硬编码抽离到 .env 环境变量"
```

---

### Task 2: docker-compose.yml 传递新环境变量

**Files:**
- Modify: `docker-compose.yml:25-50`

**Step 1: 在 chat-ui 的 environment 段新增 3 行**

在 `DASHSCOPE_API_KEY` 行之后新增：

```yaml
- EMBEDDING_MODEL=${EMBEDDING_MODEL:-text-embedding-v4}
- EMBEDDING_BASE_URL=${EMBEDDING_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}
- EMBEDDING_API_KEY=${EMBEDDING_API_KEY}
```

**Step 2: 提交**

```bash
git add docker-compose.yml
git commit -m "feat(docker): 为 chat-ui 传递 EMBEDDING_MODEL/BASE_URL/API_KEY 环境变量"
```

---

### Task 3: chat-ui/app.py 读取 Embedding 环境变量

**Files:**
- Modify: `chat-ui/app.py:62-83`

**Step 1: 修改 get_openai_client() 和 embed_query()**

```python
# === Embedding helper ===
@st.cache_resource
def get_openai_client():
    return OpenAI(
        api_key=os.environ.get("EMBEDDING_API_KEY", os.environ.get("DASHSCOPE_API_KEY", "")),
        base_url=os.environ.get("EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    )

@st.cache_resource
def get_qdrant_client():
    ...

def embed_query(text: str) -> list[float]:
    client = get_openai_client()
    model = os.environ.get("EMBEDDING_MODEL", "text-embedding-v4")
    dim = int(os.environ.get("EMBEDDING_DIM", "1024"))
    resp = client.embeddings.create(model=model, input=text, dimensions=dim, encoding_format="float")
    return resp.data[0].embedding
```

**Step 2: 修改侧边栏显示**

```python
st.caption(f"Embedding: {os.environ.get('EMBEDDING_MODEL', 'text-embedding-v4')}")
```

**Step 3: 提交**

```bash
git add chat-ui/app.py
git commit -m "feat(chat-ui): embedding 配置从环境变量读取，支持自定义模型和地址"
```

---

### Task 4: chat-ui/index_code.py 读取 Embedding 环境变量

**Files:**
- Modify: `chat-ui/index_code.py:1-18`

**Step 1: 替换硬编码配置**

```python
"""向量化索引 — OpenAI 兼容 Embedding, 1024d"""
import os, hashlib, sys, time
...

MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-v4")
DIM = int(os.environ.get("EMBEDDING_DIM", "1024"))
REPOS = os.environ.get("REPOS_ROOT", "/repos")
QDRANT = os.environ.get("QDRANT_URL", "http://qdrant:6333")
API_KEY = os.environ.get("EMBEDDING_API_KEY", os.environ.get("DASHSCOPE_API_KEY", ""))
BASE_URL = os.environ.get("EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
COLLECTION = os.environ.get("QDRANT_COLLECTION", "codebase")

...

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
```

**Step 2: 提交**

```bash
git add chat-ui/index_code.py
git commit -m "feat(index_code): embedding 配置从环境变量读取"
```

---

### Task 5: scripts/install.sh 创建安装脚本

**Files:**
- Create: `scripts/install.sh`

**安装脚本核心逻辑（bash）：**

```bash
#!/usr/bin/env bash
set -euo pipefail

REGISTRY="crpi-x1zji86f6jpcd7t1.cn-hangzhou.personal.cr.aliyuncs.com/lijing00333"

# === 颜色输出 ===
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# === 检测架构 ===
detect_arch() {
  case "$(uname -m)" in
    arm64|aarch64) echo "arm64" ;;
    x86_64)        echo "amd64" ;;
    *) echo "amd64" ;;  # fallback
  esac
}
ARCH=$(detect_arch)

# === Step 1: 环境检查 ===
check_prerequisites() {
  # docker, docker compose, 磁盘, 内存...
}

# === Step 2: 交互问答 → 写入 .env ===
collect_config() {
  # 依次询问 8 个参数，写入 .env
}

# === Step 3: 拉取镜像 ===
pull_images() {
  docker pull qdrant/qdrant:latest
  docker pull ghcr.io/sourcebot-dev/sourcebot:v4.0.0
  docker pull "${REGISTRY}/repo-bot-chat-ui:latest-${ARCH}"
  docker pull "${REGISTRY}/repo-bot-ast-service:latest-${ARCH}"
}

# === Step 4: 生成 docker-compose.yml 和 sourcebot.json ===
generate_files() {
  # heredoc 写入 docker-compose.yml
  # 根据 REPOS_ROOT 下目录生成 sourcebot.json
}

# === Step 5: 启动服务 ===
start_services() {
  docker compose up -d
  # 等待健康检查
}

# === Step 6: 数据索引 ===
run_indexing() {
  # 提示去 Sourcebot 创建 API Key
  # 向量索引 + AST 索引
}

main() {
  check_prerequisites
  collect_config
  pull_images
  generate_files
  start_services
  run_indexing
}
main
```

完整的 install.sh 约为 300-400 行，内联 docker-compose.yml 和 sourcebot.json 模板。

**Step 1: 编写完整 install.sh**

包含上述 6 步的完整实现。

**Step 2: 加可执行权限**

```bash
chmod +x scripts/install.sh
```

**Step 3: 提交**

```bash
git add scripts/install.sh
git commit -m "feat(scripts): 添加一键安装脚本 install.sh"
```

---

### Task 6: 构建并推送最新镜像

**Files:** 无（仅构建与推送）

**Step 1: 本地部署验证**

```bash
npm run deploy:chat-ui && npm run deploy:ast-service
```

**Step 2: 推送至阿里云**

```bash
npm run docker_push:chat-ui && npm run docker_push:ast-service
```

**Step 3: 验证推送成功**

使用 `docker manifest inspect` 确认远端镜像可拉取。
