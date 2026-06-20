#!/usr/bin/env bash
# repo-bot 一键安装脚本
# 用法: bash install.sh
set -euo pipefail

REGISTRY="crpi-x1zji86f6jpcd7t1.cn-hangzhou.personal.cr.aliyuncs.com/lijing00333"
INSTALL_DIR="${HOME}/.repo-bot"

# === 颜色 ===
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }
ask()   { echo -e "${CYAN}[?]${NC} $*"; }

# === 系统架构 ===
detect_arch() {
  case "$(uname -m)" in
    arm64|aarch64) echo "arm64" ;;
    x86_64)        echo "amd64" ;;
    *) echo "amd64" ;;
  esac
}
ARCH=$(detect_arch)

# ============================
# Step 1: 环境检查
# ============================
check_prerequisites() {
  echo ""
  echo "========================================"
  echo " Step 1/6: 环境检查"
  echo "========================================"

  info "系统架构: ${ARCH}"

  # Docker
  if ! command -v docker &>/dev/null; then
    err "未检测到 Docker，请先安装: https://docs.docker.com/get-docker/"
    exit 1
  fi
  info "docker: $(docker --version)"

  # Docker Compose
  if docker compose version &>/dev/null; then
    info "docker compose: $(docker compose version --short 2>/dev/null || echo 'v2')"
  elif command -v docker-compose &>/dev/null; then
    info "docker-compose: $(docker-compose --version)"
  else
    err "未检测到 docker compose，请安装 Docker Compose v2"
    exit 1
  fi

  # 磁盘空间 (至少 10G)
  local available
  available=$(df -m "${HOME}" | awk 'NR==2 {printf "%d", $4/1024}')
  if [ "${available:-0}" -lt 10 ]; then
    warn "磁盘可用空间: ${available}G (建议至少 10G，镜像约需 2G + 向量数据)"
  else
    info "磁盘可用空间: ${available}G"
  fi

  # 内存 (至少 4G)
  if command -v sysctl &>/dev/null; then
    local mem
    mem=$(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%d", $1/1024/1024/1024}')
    if [ "${mem:-0}" -lt 4 ]; then
      warn "系统内存: ${mem}G (建议至少 4G)"
    else
      info "系统内存: ${mem}G"
    fi
  fi

  # 创建安装目录
  mkdir -p "${INSTALL_DIR}"
}

# ============================
# Step 2: 交互问答
# ============================
collect_config() {
  echo ""
  echo "========================================"
  echo " Step 2/6: 配置参数"
  echo "========================================"
  echo "按 Enter 使用默认值"
  echo ""

  # -- REPOS_ROOT --
  echo ">>>> 代码仓库"
  default_repos="${HOME}/projects"
  read -r -p "  仓库根目录路径 [${default_repos}]: " REPOS_ROOT
  REPOS_ROOT="${REPOS_ROOT:-${default_repos}}"
  REPOS_ROOT="${REPOS_ROOT/#\~/$HOME}"
  if [ ! -d "${REPOS_ROOT}" ]; then
    warn "目录 ${REPOS_ROOT} 不存在，已自动创建"
    mkdir -p "${REPOS_ROOT}"
  fi
  echo "  -> REPOS_ROOT=${REPOS_ROOT}"
  echo ""

  # -- LLM_PROVIDER --
  echo ">>>> LLM 配置"
  echo "  [1] anthropic (默认)"
  echo "  [2] openai"
  read -r -p "  选择 LLM Provider [1]: " llm_choice
  case "${llm_choice:-1}" in
    2) LLM_PROVIDER="openai" ;;
    *) LLM_PROVIDER="anthropic" ;;
  esac
  echo "  -> LLM_PROVIDER=${LLM_PROVIDER}"
  echo ""

  # -- API_KEY --
  if [ "${LLM_PROVIDER}" = "anthropic" ]; then
    read -r -p "  ANTHROPIC_API_KEY: " ANTHROPIC_API_KEY
    OPENAI_API_KEY=""
  else
    read -r -p "  OPENAI_API_KEY: " OPENAI_API_KEY
    ANTHROPIC_API_KEY=""
  fi
  echo ""

  # -- LLM_MODEL --
  default_model="claude-sonnet-4-6"
  [ "${LLM_PROVIDER}" = "openai" ] && default_model="gpt-4o"
  read -r -p "  模型名 [${default_model}]: " LLM_MODEL
  LLM_MODEL="${LLM_MODEL:-${default_model}}"
  echo "  -> LLM_MODEL=${LLM_MODEL}"
  echo ""

  # -- BASE_URL --
  read -r -p "  自定义 API 代理地址（留空跳过）: " ANTHROPIC_BASE_URL
  echo ""

  # -- Embedding --
  echo ">>>> Embedding 配置"
  read -r -p "  Embedding 模型 [text-embedding-v4]: " EMBEDDING_MODEL
  EMBEDDING_MODEL="${EMBEDDING_MODEL:-text-embedding-v4}"

  read -r -p "  Embedding 维度 [1024]: " EMBEDDING_DIM
  EMBEDDING_DIM="${EMBEDDING_DIM:-1024}"

  default_emb_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
  read -r -p "  Embedding API URL [${default_emb_url}]: " EMBEDDING_BASE_URL
  EMBEDDING_BASE_URL="${EMBEDDING_BASE_URL:-${default_emb_url}}"

  read -r -p "  Embedding API Key（留空复用 LLM Key）: " EMBEDDING_API_KEY
  echo ""

  # -- Chat UI 登录 --
  echo ">>>> Chat UI 登录"
  read -r -p "  用户名 [admin]: " CHAT_USERNAME
  CHAT_USERNAME="${CHAT_USERNAME:-admin}"
  read -r -sp "  密码 [admin123]: " CHAT_PASSWORD
  CHAT_PASSWORD="${CHAT_PASSWORD:-admin123}"
  echo ""
  echo ""

  # -- Qdrant 集合名 --
  read -r -p "  Qdrant 集合名 [codebase]: " QDRANT_COLLECTION
  QDRANT_COLLECTION="${QDRANT_COLLECTION:-codebase}"
  echo ""

  # -- Neo4j 图关系索引 --
  echo ">>>> Neo4j 图关系索引"
  read -r -p "  启用 Neo4j 图关系索引? [Y/n]: " neo4j_enabled
  case "${neo4j_enabled:-y}" in
    [nN]*) NEO4J_ENABLED="false" ;;
    *) NEO4J_ENABLED="true" ;;
  esac
  echo "  -> NEO4J_ENABLED=${NEO4J_ENABLED}"
  read -r -p "  Neo4j 密码 [repo-bot-neo4j]: " NEO4J_PASSWORD
  NEO4J_PASSWORD="${NEO4J_PASSWORD:-repo-bot-neo4j}"
  echo ""

  # 写入 .env
  info "写入配置到 ${INSTALL_DIR}/.env ..."
  cat > "${INSTALL_DIR}/.env" <<ENVEOF
# repo-bot 配置文件
# 生成时间: $(date '+%Y-%m-%d %H:%M:%S')

# === LLM 配置 ===
LLM_PROVIDER=${LLM_PROVIDER}
LLM_MODEL=${LLM_MODEL}
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
ANTHROPIC_BASE_URL=${ANTHROPIC_BASE_URL}
OPENAI_API_KEY=${OPENAI_API_KEY}

# === Embedding 配置 ===
EMBEDDING_MODEL=${EMBEDDING_MODEL}
EMBEDDING_DIM=${EMBEDDING_DIM}
EMBEDDING_BASE_URL=${EMBEDDING_BASE_URL}
EMBEDDING_API_KEY=${EMBEDDING_API_KEY}
DASHSCOPE_API_KEY=${EMBEDDING_API_KEY}

# === Qdrant 向量库 ===
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=${QDRANT_COLLECTION}

# === Sourcebot ===
SOURCEBOT_URL=http://localhost:3000
SOURCEBOT_ORG_DOMAIN=~

# === Chat UI 登录 ===
CHAT_USERNAME=${CHAT_USERNAME}
CHAT_PASSWORD=${CHAT_PASSWORD}

# === Neo4j 图关系索引 ===
NEO4J_ENABLED=${NEO4J_ENABLED}
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=${NEO4J_PASSWORD}

# === 代码仓库根目录 ===
REPOS_ROOT=${REPOS_ROOT}
ENVEOF

  info "配置已保存"
}

# ============================
# Step 3: 拉取镜像
# ============================
pull_images() {
  echo ""
  echo "========================================"
  echo " Step 3/6: 拉取 Docker 镜像"
  echo "========================================"

  info "拉取 qdrant/qdrant:latest ..."
  docker pull "qdrant/qdrant:latest"

  info "拉取 ghcr.io/sourcebot-dev/sourcebot:v4.0.0 ..."
  docker pull "ghcr.io/sourcebot-dev/sourcebot:v4.0.0"

  info "拉取 ${REGISTRY}/repo-bot-chat-ui:latest-${ARCH} ..."
  docker pull "${REGISTRY}/repo-bot-chat-ui:latest-${ARCH}"
  docker tag "${REGISTRY}/repo-bot-chat-ui:latest-${ARCH}" "repo-bot-chat-ui:latest"

  info "拉取 ${REGISTRY}/repo-bot-ast-service:latest-${ARCH} ..."
  docker pull "${REGISTRY}/repo-bot-ast-service:latest-${ARCH}"
  docker tag "${REGISTRY}/repo-bot-ast-service:latest-${ARCH}" "repo-bot-ast-service:latest"

  info "拉取 neo4j:5-community ..."
  docker pull "neo4j:5-community"

  info "所有镜像拉取完成"
}

# ============================
# Step 4: 生成配置文件
# ============================
generate_files() {
  echo ""
  echo "========================================"
  echo " Step 4/6: 生成配置文件"
  echo "========================================"

  # 读取 REPOS_ROOT
  REPOS_ROOT=$(grep '^REPOS_ROOT=' "${INSTALL_DIR}/.env" | cut -d= -f2)

  # 生成 sourcebot.json
  info "扫描 ${REPOS_ROOT} 下仓库..."
  local repos_json=""
  local first=true
  for dir in "${REPOS_ROOT}"/*/; do
    [ -d "${dir}" ] || continue
    local name
    name=$(basename "${dir}")
    [ "${name}" = "." ] || [ "${name}" = ".." ] && continue
    [ -d "${dir}.git" ] || continue
    if [ "${first}" = true ]; then
      first=false
    else
      repos_json+=$',\n    '
    fi
    repos_json+="\"${name}\":  { \"type\": \"git\", \"url\": \"file:///repos/${name}\" }"
  done

  local sourcebot_json
  if [ -z "${repos_json}" ]; then
    warn "未检测到 git 仓库，生成空模板，请稍后手动编辑 config/sourcebot.json"
    sourcebot_json=$(cat <<'SBEOF'
{
  "$schema": "https://raw.githubusercontent.com/sourcebot-dev/sourcebot/main/schemas/v3/index.json",
  "connections": {
    "example-repo": { "type": "git", "url": "file:///repos/example-repo" }
  }
}
SBEOF
)
  else
    sourcebot_json=$(cat <<SBEOF
{
  "\$schema": "https://raw.githubusercontent.com/sourcebot-dev/sourcebot/main/schemas/v3/index.json",
  "connections": {
    ${repos_json}
  }
}
SBEOF
)
  fi

  mkdir -p "${INSTALL_DIR}/config"
  echo "${sourcebot_json}" > "${INSTALL_DIR}/config/sourcebot.json"
  info "已生成 config/sourcebot.json"

  # 生成 docker-compose.yml
  info "生成 docker-compose.yml ..."
  cat > "${INSTALL_DIR}/docker-compose.yml" <<'COMPOSEEOF'
services:
  sourcebot:
    image: ghcr.io/sourcebot-dev/sourcebot:v4.0.0
    ports:
      - "3000:3000"
    volumes:
      - ${REPOS_ROOT}:/repos:ro
      - ./config/sourcebot.json:/data/config.json:ro
      - sourcebot_data:/data
    environment:
      - CONFIG_PATH=/data/config.json
    restart: unless-stopped

  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - qdrant_data:/qdrant/storage
    restart: unless-stopped

  neo4j:
    image: ${NEO4J_IMAGE:-neo4j:5-community}
    ports:
      - "7474:7474"
      - "7687:7687"
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs
      - neo4j_plugins:/plugins
    environment:
      - NEO4J_AUTH=neo4j/${NEO4J_PASSWORD:-repo-bot-neo4j}
      - NEO4J_server_memory_heap_initial__size=${NEO4J_HEAP_INITIAL:-512m}
      - NEO4J_server_memory_heap_max__size=${NEO4J_HEAP_MAX:-2G}
      - NEO4J_server_memory_pagecache_size=${NEO4J_PAGECACHE:-1G}
    healthcheck:
      test: ["CMD-SHELL", "cypher-shell -u neo4j -p \"$${NEO4J_PASSWORD:-repo-bot-neo4j}\" 'RETURN 1'"]
      interval: 5s
      timeout: 5s
      retries: 20
      start_period: 20s
    restart: unless-stopped

  chat-ui:
    image: repo-bot-chat-ui:latest
    ports:
      - "8501:8501"
    volumes:
      - ${REPOS_ROOT}:/repos:ro
    env_file:
      - .env
    environment:
      - QDRANT_URL=http://qdrant:6333
      - SOURCEBOT_URL=http://sourcebot:3000
      - SOURCEBOT_ORG_DOMAIN=${SOURCEBOT_ORG_DOMAIN:-~}
      - REPOS_ROOT=/repos
      - AST_SERVICE_URL=http://ast-service:8502
    depends_on:
      - qdrant
      - sourcebot
      - ast-service
    restart: unless-stopped

  ast-service:
    image: repo-bot-ast-service:latest
    ports:
      - "8502:8502"
    volumes:
      - ${REPOS_ROOT}:/repos:ro
      - ast_data:/data
    environment:
      - REPOS_ROOT=/repos
      - AST_DB_PATH=/data/ast.sqlite
      - NEO4J_ENABLED=${NEO4J_ENABLED:-true}
      - NEO4J_URI=bolt://neo4j:7687
      - NEO4J_USER=${NEO4J_USER:-neo4j}
      - NEO4J_PASSWORD=${NEO4J_PASSWORD:-repo-bot-neo4j}
      - NEO4J_DATABASE=${NEO4J_DATABASE:-neo4j}
    depends_on:
      neo4j:
        condition: service_healthy
    restart: unless-stopped

volumes:
  sourcebot_data:
  qdrant_data:
  ast_data:
  neo4j_data:
  neo4j_logs:
  neo4j_plugins:
COMPOSEEOF

  info "已生成 docker-compose.yml"
  info "安装目录: ${INSTALL_DIR}"
  ls -la "${INSTALL_DIR}/"
}

# ============================
# Step 5: 启动服务
# ============================
start_services() {
  echo ""
  echo "========================================"
  echo " Step 5/6: 启动服务"
  echo "========================================"

  cd "${INSTALL_DIR}"

  info "docker compose up -d ..."
  docker compose up -d

  # 等待就绪
  info "等待服务就绪 (最多 60s)..."
  local waited=0
  while [ $waited -lt 60 ]; do
    local all_ok=true

    if ! curl -s http://localhost:6333/health &>/dev/null; then
      all_ok=false
    fi

    if ! curl -s -o /dev/null -w "%{http_code}" http://localhost:3000 | grep -q '200\|302'; then
      all_ok=false
    fi

    if ! curl -s -o /dev/null -w "%{http_code}" http://localhost:8501 | grep -q '200\|302'; then
      all_ok=false
    fi

    if ! curl -s -o /dev/null -w "%{http_code}" http://localhost:8502/docs | grep -q '200'; then
      all_ok=false
    fi

    if ! curl -s http://localhost:7474 &>/dev/null; then
      all_ok=false
    fi

    if [ "${all_ok}" = true ]; then
      break
    fi
    sleep 3
    waited=$((waited + 3))
    echo -n "."
  done
  echo ""

  info "服务状态:"
  docker compose ps

  local all_running=true
  for svc in qdrant sourcebot chat-ui ast-service neo4j; do
    if docker compose ps "${svc}" 2>/dev/null | grep -q 'Up'; then
      info "  ${svc}: 运行中"
    else
      warn "  ${svc}: 可能未就绪"
      all_running=false
    fi
  done

  if [ "${all_running}" = false ]; then
    warn "部分服务未就绪，可稍后运行 'cd ${INSTALL_DIR} && docker compose logs -f' 排查"
  fi
}

# ============================
# Step 6: 数据索引
# ============================
run_indexing() {
  echo ""
  echo "========================================"
  echo " Step 6/6: 数据索引"
  echo "========================================"

  echo ""
  info "首次启动完成！接下来需要初始化数据索引："
  echo ""
  echo "  1. Sourcebot 后台索引："
  echo "     访问 http://localhost:3000 注册管理员，然后进入仓库管理触发 reindex"
  echo "     （Sourcebot 会扫描 /repos 下所有仓库并构建 trigram 索引）"
  echo ""
  echo "  2. 向量索引（chat-ui 语义搜索）："
  echo "     docker exec \$(docker compose -f ${INSTALL_DIR}/docker-compose.yml ps -q chat-ui) python /app/index_code.py"
  echo ""
  echo "  3. AST 结构索引："
  echo "     docker exec \$(docker compose -f ${INSTALL_DIR}/docker-compose.yml ps -q ast-service) python /app/indexer.py --mode full"
  echo ""

  read -r -p "  是否立即执行向量索引 (需要 Embedding API Key 已配置)? [Y/n]: " do_index
  do_index="${do_index:-y}"
  if [ "${do_index}" = "y" ] || [ "${do_index}" = "Y" ]; then
    info "执行向量索引..."
    local chat_cid
    chat_cid=$(docker compose -f "${INSTALL_DIR}/docker-compose.yml" ps -q chat-ui)
    if [ -n "${chat_cid}" ]; then
      docker exec "${chat_cid}" python /app/index_code.py || warn "向量索引失败，可稍后重试"
    else
      warn "chat-ui 容器未运行，跳过向量索引"
    fi
  fi

  read -r -p "  是否立即执行 AST 结构索引? [Y/n]: " do_ast
  do_ast="${do_ast:-y}"
  if [ "${do_ast}" = "y" ] || [ "${do_ast}" = "Y" ]; then
    info "执行 AST 结构索引..."
    local ast_cid
    ast_cid=$(docker compose -f "${INSTALL_DIR}/docker-compose.yml" ps -q ast-service)
    if [ -n "${ast_cid}" ]; then
      docker exec "${ast_cid}" python /app/indexer.py --mode full || warn "AST 索引失败，可稍后重试"
    else
      warn "ast-service 容器未运行，跳过 AST 索引"
    fi
  fi
}

# ============================
# Main
# ============================
main() {
  echo ""
  echo "================================================"
  echo "  repo-bot 安装脚本"
  echo "  Chat UI: http://localhost:8501"
  echo "  Sourcebot: http://localhost:3000"
  echo "  Qdrant Dashboard: http://localhost:6333/dashboard"
  echo "  AST API Docs: http://localhost:8502/docs"
  echo "  Neo4j Browser: http://localhost:7474"
  echo "================================================"

  check_prerequisites
  collect_config
  pull_images
  generate_files
  start_services
  run_indexing

  echo ""
  echo "================================================"
  info "安装完成！"
  echo ""
  echo "  访问地址:"
  echo "    Chat UI:     http://localhost:8501"
  echo "    Sourcebot:   http://localhost:3000  (注册管理员 + 设置 API Key)"
  echo "    Qdrant:      http://localhost:6333/dashboard"
  echo "    AST API:     http://localhost:8502/docs"
  echo "    Neo4j:       http://localhost:7474  (neo4j / 你的密码)"
  echo ""
  echo "  后续操作:"
  echo "    1. 在 Sourcebot 设置页创建 API Key 并更新到 ${INSTALL_DIR}/.env"
  echo "    2. 重启 chat-ui: docker compose -f ${INSTALL_DIR}/docker-compose.yml up -d chat-ui"
  echo "    3. 核心命令: cd ${INSTALL_DIR} && docker compose <cmd>"
  echo "================================================"
}

main "$@"
