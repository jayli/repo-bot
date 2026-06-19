#!/usr/bin/env bash
# 构建 Docker 镜像并推送到阿里云容器镜像仓库
# 参照 block-proxy 的构建推送模式
#
# 用法:
#   docker-build-push.sh <service>             构建当前平台并推送
#   docker-build-push.sh <service> arm64       构建 arm64 并推送
#   docker-build-push.sh <service> amd64       构建 amd64 并推送
#   docker-build-push.sh <service> all         构建双平台并推送 manifest
#
# service: chat-ui | ast-service

set -euo pipefail

SERVICE="$1"
PLATFORM="${2:-auto}"

REGISTRY="crpi-x1zji86f6jpcd7t1.cn-hangzhou.personal.cr.aliyuncs.com/lijing00333"

case "$SERVICE" in
  chat-ui)
    IMAGE_NAME="repo-bot-chat-ui"
    BUILD_DIR="chat-ui"
    ;;
  ast-service)
    IMAGE_NAME="repo-bot-ast-service"
    BUILD_DIR="ast-service"
    ;;
  *)
    echo "用法: $0 <chat-ui|ast-service> [arm64|amd64|all]"
    exit 1
    ;;
esac

# 检测当前平台
detect_platform() {
  local arch
  arch=$(uname -m)
  case "$arch" in
    arm64|aarch64)  echo "linux/arm64" ;;
    x86_64)         echo "linux/amd64" ;;
    *)
      echo "未知架构: $arch，默认使用 linux/amd64" >&2
      echo "linux/amd64"
      ;;
  esac
}

# 构建并推送单平台
build_push_single() {
  local platform="$1"
  local tag_suffix="$2"

  local platform_short
  case "$platform" in
    linux/amd64) platform_short="amd64" ;;
    linux/arm64) platform_short="arm64" ;;
  esac

  echo ">>> 构建并推送: ${IMAGE_NAME}:latest-${tag_suffix} (platform: ${platform})"
  docker buildx build \
    --platform "$platform" \
    --push \
    -t "${REGISTRY}/${IMAGE_NAME}:latest-${tag_suffix}" \
    "$BUILD_DIR"

  echo ">>> 完成: ${REGISTRY}/${IMAGE_NAME}:latest-${tag_suffix}"
}

# 构建并推送双平台 + manifest
build_push_all() {
  echo ">>> 构建并推送 amd64..."
  docker buildx build \
    --platform linux/amd64 \
    --push \
    -t "${REGISTRY}/${IMAGE_NAME}:latest-amd64" \
    "$BUILD_DIR"

  echo ">>> 构建并推送 arm64..."
  docker buildx build \
    --platform linux/arm64 \
    --push \
    -t "${REGISTRY}/${IMAGE_NAME}:latest-arm64" \
    "$BUILD_DIR"

  echo ">>> 创建 multi-arch manifest: ${REGISTRY}/${IMAGE_NAME}:latest"
  docker manifest create --amend \
    "${REGISTRY}/${IMAGE_NAME}:latest" \
    "${REGISTRY}/${IMAGE_NAME}:latest-amd64" \
    "${REGISTRY}/${IMAGE_NAME}:latest-arm64"

  echo ">>> 推送 manifest: ${REGISTRY}/${IMAGE_NAME}:latest"
  docker manifest push "${REGISTRY}/${IMAGE_NAME}:latest"

  echo ">>> 全部完成: ${REGISTRY}/${IMAGE_NAME}:latest"
}

case "$PLATFORM" in
  auto)
    plat=$(detect_platform)
    case "$plat" in
      linux/amd64) build_push_single "$plat" "amd64" ;;
      linux/arm64) build_push_single "$plat" "arm64" ;;
    esac
    ;;
  arm64|arm)
    build_push_single "linux/arm64" "arm64"
    ;;
  amd64|x86|x86_64)
    build_push_single "linux/amd64" "amd64"
    ;;
  all)
    build_push_all
    ;;
  *)
    echo "未知平台: $PLATFORM，可选: arm64, amd64, all"
    exit 1
    ;;
esac
