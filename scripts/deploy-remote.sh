#!/usr/bin/env bash
# 在目标服务器上执行：拉取 main 并重建 Docker Compose 栈。
# 密钥与服务器地址由服务器本地 deploy/.env.docker 承载，勿写入本脚本或 CI。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT/deploy/docker-compose.yml" ]]; then
  COMPOSE_FILE="$ROOT/deploy/docker-compose.yml"
elif [[ -f "$ROOT/docker-compose.yml" ]]; then
  COMPOSE_FILE="$ROOT/docker-compose.yml"
else
  die "未找到 docker-compose.yml"
fi
ENV_FILE="$ROOT/deploy/.env.docker"
CONFIG_FILE="$ROOT/deploy/config.docker.yaml"

die() { echo "deploy-remote: $*" >&2; exit 1; }

[[ -f "$ENV_FILE" ]] || die "缺少 ${ENV_FILE}，请从 deploy/.env.docker.example 复制并填写"
[[ -f "$CONFIG_FILE" ]] || die "缺少 ${CONFIG_FILE}"

cd "$ROOT"

compose() {
  if docker info >/dev/null 2>&1; then
    docker compose "$@"
  else
    sudo docker compose "$@"
  fi
}

echo "==> 同步 main 分支"
git fetch origin main
git reset --hard origin/main

echo "==> 构建镜像"
compose -f "$COMPOSE_FILE" build

echo "==> 启动栈"
compose -f "$COMPOSE_FILE" up -d --remove-orphans

echo "==> 运行状态"
compose -f "$COMPOSE_FILE" ps

echo "==> 部署完成"
