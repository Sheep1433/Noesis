#!/usr/bin/env bash
# 在目标服务器上执行：拉取指定分支并重建 Docker Compose 栈。
# 密钥与服务器地址由服务器本地 deploy/.env.docker 承载，勿写入本脚本或 CI。
#
# 环境变量：
#   DEPLOY_BRANCH   部署分支，默认 main（生产）；偶尔线上调试 dev 时传 dev
#
# 线上仅一套 compose（:28468）。生产 / 调试通过 deploy/.env.docker 区 MySQL 库：
#   生产  POSTGRES_DATABASE=noesis
#   调试  POSTGRES_DATABASE=noesis_dev  （部署前手动改，调试完改回）
set -euo pipefail

DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-noesis}"
export NOESIS_HTTP_PORT="${NOESIS_HTTP_PORT:-28468}"

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

echo "==> 部署前释放磁盘（避免 docker build 因 no space left on device 失败）"
avail_kb="$(df --output=avail / | tail -1 | tr -d ' ')"
echo "deploy-remote: 根分区可用 ${avail_kb}KB"
if docker info >/dev/null 2>&1; then
  docker builder prune -af >/dev/null 2>&1 || true
  docker image prune -f >/dev/null 2>&1 || true
else
  sudo docker builder prune -af >/dev/null 2>&1 || true
  sudo docker image prune -f >/dev/null 2>&1 || true
fi
# Langfuse ClickHouse 异常时会狂写 json-file 日志，部署前截断超大日志
while IFS= read -r logfile; do
  [[ -n "${logfile}" ]] || continue
  echo "deploy-remote: 截断超大容器日志 ${logfile}"
  truncate -s 0 "${logfile}"
done < <(find /var/lib/docker/containers -name '*-json.log' -size +200M 2>/dev/null || true)

echo "==> 构建镜像"
# --progress=plain 持续输出构建日志，避免 CI SSH 长时间无输出被断开（Broken pipe）
compose build --progress=plain

echo "==> 启动栈"
compose up -d --remove-orphans

echo "==> 运行状态"
compose ps

echo "==> 部署完成: http://<host>:${NOESIS_HTTP_PORT}"
