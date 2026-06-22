#!/usr/bin/env bash
# 扫描 REPOS_ROOT 下的 git 仓库，自动重新生成 config/sourcebot.json
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# 加载 .env
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

REPOS_ROOT="${REPOS_ROOT:-$HOME/projects}"

# 展开 ~ 为实际路径
REPOS_ROOT="${REPOS_ROOT/#\~/$HOME}"

if [ ! -d "$REPOS_ROOT" ]; then
  echo "错误：REPOS_ROOT 目录不存在: $REPOS_ROOT" >&2
  echo "请编辑 .env 中的 REPOS_ROOT 指向实际仓库目录" >&2
  exit 1
fi

echo "扫描 $REPOS_ROOT ..."

# Sourcebot config key 只允许 ^[a-zA-Z0-9_-]+$，含 . 的目录名需替换
sanitize_name() {
  echo "$1" | sed 's/\./_/g'
}

# 收集仓库列表
repos=()
skipped=()
for dir in "$REPOS_ROOT"/*/; do
  [ -d "$dir" ] || continue
  name=$(basename "$dir")
  [ "$name" = "." ] || [ "$name" = ".." ] && continue
  [ -d "$dir/.git" ] || continue
  repos+=("$name")
done

if [ ${#repos[@]} -eq 0 ]; then
  echo "未在 $REPOS_ROOT 下找到任何 git 仓库"
  echo "请确认 REPOS_ROOT 路径正确，或手动编辑 config/sourcebot.json"
  exit 1
fi

echo "发现 ${#repos[@]} 个仓库"

# 读取旧配置（用于 diff 对比，存 sanitized key）
old_repos=()
if [ -f config/sourcebot.json ]; then
  while IFS= read -r line; do
    [ -n "$line" ] && old_repos+=("$line")
  done < <(python3 -c "
import json, sys
try:
    data = json.load(open('config/sourcebot.json'))
    for k in data.get('connections', {}):
        print(k)
except Exception:
    pass
" 2>/dev/null)
fi

# 生成 JSON
mkdir -p config

# 先输出警告（到 stderr，不混入 JSON）
for name in "${repos[@]}"; do
  key=$(sanitize_name "$name")
  if [ "$key" != "$name" ]; then
    echo "  [warn] $name -> key renamed to $key (Sourcebot config key 不允许含 .)" >&2
  fi
done

{
  echo '{'
  echo '  "$schema": "https://raw.githubusercontent.com/sourcebot-dev/sourcebot/main/schemas/v3/index.json",'
  echo '  "connections": {'

  first=true
  for name in "${repos[@]}"; do
    key=$(sanitize_name "$name")
    if [ "$first" = true ]; then
      first=false
    else
      echo ","
    fi
    printf '    "%s": { "type": "git", "url": "file:///repos/%s" }' "$key" "$name"
  done

  echo ""
  echo '  }'
  echo '}'
} > config/sourcebot.json

# 输出变更摘要
new_count=${#repos[@]}

echo ""
echo "已更新 config/sourcebot.json（$new_count 个仓库）"

# 列出新增和移除的仓库
added=()
removed=()
for name in "${repos[@]}"; do
  key=$(sanitize_name "$name")
  found=false
  for old in "${old_repos[@]+"${old_repos[@]}"}"; do
    [ "$key" = "$old" ] && found=true && break
  done
  $found || added+=("$name")
done
for old in "${old_repos[@]+"${old_repos[@]}"}"; do
  found=false
  for name in "${repos[@]}"; do
    key=$(sanitize_name "$name")
    [ "$key" = "$old" ] && found=true && break
  done
  $found || removed+=("$old")
done

if [ ${#added[@]} -gt 0 ]; then
  echo "  + 新增: ${added[*]}"
fi
if [ ${#removed[@]} -gt 0 ]; then
  echo "  - 移除: ${removed[*]}"
fi
if [ ${#added[@]} -eq 0 ] && [ ${#removed[@]} -eq 0 ]; then
  echo "  （无变更）"
fi

echo ""
echo "下一步：重启 Sourcebot 容器使配置生效"
echo "  docker compose restart sourcebot"
