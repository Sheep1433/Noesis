#!/usr/bin/env bash
# 在目标服务器上执行：拉取指定分支并重建 Docker Compose 栈。
# 密钥与服务器地址由服务器本地 deploy/.env.docker 承载，勿写入本脚本或 CI。
#
# 环境变量：
#   DEPLOY_BRANCH       部署分支，默认 main（生产）；开发环境传 dev
#   COMPOSE_PROJECT_NAME / NOESIS_HTTP_PORT  由分支自动推导，一般无需手改
set -euo pipefail

DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"

case "$DEPLOY_BRANCH" in
  dev)
    export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-noesis-dev}"
    export NOESIS_HTTP_PORT="${NOESIS_HTTP_PORT:-28469}"
    ;;
  main|*)
    export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-noesis}"
    export NOESIS_HTTP_PORT="${NOESIS_HTTP_PORT:-28468}"
    ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="$ROOT/deploy/docker-compose.yml"
ENV_FILE="$ROOT/deploy/.env.docker"
CONFIG_FILE="$ROOT/deploy/config.docker.yaml"

die() { echo "deploy-remote: $*" >&2; exit 1; }

[[ -f "$COMPOSE_FILE" ]] || die "缺少 ${COMPOSE_FILE}（请使用 deploy/docker-compose.yml，勿用仓库根目录旧 compose）"
[[ -f "$ENV_FILE" ]] || die "缺少 ${ENV_FILE}，请从 deploy/.env.docker.example 复制并填写"
[[ -f "$CONFIG_FILE" ]] || die "缺少 ${CONFIG_FILE}"

cd "$ROOT"

compose() {
  local cmd=(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE")
  if docker info >/dev/null 2>&1; then
    "${cmd[@]}" "$@"
  else
    sudo "${cmd[@]}" "$@"
  fi
}

echo "==> 同步 ${DEPLOY_BRANCH} 分支 (stack=${COMPOSE_PROJECT_NAME}, port=${NOESIS_HTTP_PORT})"
git fetch origin "${DEPLOY_BRANCH}"
git reset --hard "origin/${DEPLOY_BRANCH}"

echo "==> 构建镜像"
compose build

echo "==> 启动栈"
compose up -d --remove-orphans

echo "==> 运行状态"
compose ps

echo "==> 部署完成: http://<host>:${NOESIS_HTTP_PORT}"
