#!/bin/bash
# 增量向量索引更新
# 建议用 cron 定时运行，只处理最近修改的文件
# crontab 示例: 0 */4 * * * $HOME/repo-bot/scripts/incremental-index.sh

set -e
REPOS_ROOT="${REPOS_ROOT:-$HOME/projects}"
LAST_RUN_FILE="$HOME/.repo-bot/last_run"

mkdir -p "$(dirname "$LAST_RUN_FILE")"

# 找到上次运行后修改的文件
if [ -f "$LAST_RUN_FILE" ]; then
    LAST_RUN=$(cat "$LAST_RUN_FILE")
    echo "上次运行: $LAST_RUN"
    FIND_OPTS=(-newermt "$LAST_RUN")
else
    echo "首次运行，全量索引"
    FIND_OPTS=()
fi

# 记录本次运行时间
date -u +"%Y-%m-%dT%H:%M:%S" > "$LAST_RUN_FILE"

# 列出变更文件
CHANGED=$(find "$REPOS_ROOT" "${FIND_OPTS[@]}" -type f \( \
    -name "*.py" -o -name "*.ts" -o -name "*.tsx" -o -name "*.go" -o \
    -name "*.rs" -o -name "*.java" -o -name "*.js" -o -name "*.jsx" \
    \) ! -path "*/node_modules/*" ! -path "*/.git/*" ! -path "*/__pycache__/*" \
    ! -path "*/target/*" ! -path "*/dist/*" ! -path "*/.venv/*" 2>/dev/null | wc -l)

echo "检测到 $CHANGED 个变更文件"

if [ "$CHANGED" -gt 0 ]; then
    cd "$(dirname "$0")"
    python3 index-vectors.py --incremental
fi
