#!/usr/bin/env bash
# 将已构建好的 Docker Compose 镜像 tag 并推送到阿里云容器镜像仓库
#
# 用法:
#   docker-push-existing.sh <service>
#
# service: chat-ui | ast-service

set -euo pipefail

SERVICE="$1"

REGISTRY="crpi-x1zji86f6jpcd7t1.cn-hangzhou.personal.cr.aliyuncs.com/lijing00333"

case "$SERVICE" in
  chat-ui)
    IMAGE_NAME="repo-bot-chat-ui"
    LOCAL_IMAGE="repo-bot-chat-ui"
    ;;
  ast-service)
    IMAGE_NAME="repo-bot-ast-service"
    LOCAL_IMAGE="repo-bot-ast-service"
    ;;
  *)
    echo "用法: $0 <chat-ui|ast-service>"
    exit 1
    ;;
esac

# 检测当前平台
arch=$(uname -m)
case "$arch" in
  arm64|aarch64) TAG_SUFFIX="arm64" ;;
  x86_64)        TAG_SUFFIX="amd64" ;;
  *)
    echo "未知架构: $arch，默认使用 amd64"
    TAG_SUFFIX="amd64"
    ;;
esac

REMOTE_TAG="${REGISTRY}/${IMAGE_NAME}:latest-${TAG_SUFFIX}"

echo ">>> 为已有镜像打标签: ${LOCAL_IMAGE} -> ${REMOTE_TAG}"
docker tag "${LOCAL_IMAGE}:latest" "$REMOTE_TAG"

echo ">>> 推送: ${REMOTE_TAG}"
docker push "$REMOTE_TAG"

echo ">>> 完成: ${REMOTE_TAG}"
