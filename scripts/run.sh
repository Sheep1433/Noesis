#!/usr/bin/env bash
# Noesis 统一启动入口
#
#   ./scripts/run.sh dev              本地开发（热重载）
#   ./scripts/run.sh prod             裸机生产形态（build + preview，无热重载）
#   ./scripts/run.sh docker           推荐生产（nginx + backend + qdrant）
#
# 配置:
#   dev    → backend/.env + backend/config.yaml
#   prod   → backend/.env.prod + backend/config.prod.yaml
#   docker → deploy/.env.docker + deploy/config.docker.yaml

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/backend"
DEPLOY="$ROOT/deploy"
COMPOSE_FILE="$DEPLOY/docker-compose.yml"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[run]${NC} $*"; }
warn() { echo -e "${YELLOW}[run]${NC} $*"; }
die() { echo -e "${RED}[run]${NC} $*" >&2; exit 1; }

ensure_file() {
  local target="$1"
  local template="$2"
  if [[ -f "$target" ]]; then
    return 0
  fi
  if [[ ! -f "$template" ]]; then
    die "缺少 ${target}，且模板 ${template} 不存在"
  fi
  cp "$template" "$target"
  warn "已从模板创建 ${target}，请按需修改后重新执行"
  exit 1
}

usage() {
  cat <<'EOF'
Noesis 启动脚本（统一入口）

部署方式（三选一）:
  dev      本地开发：Qdrant + 后端热重载 + Vite :2048（MCP 默认关）
  prod     裸机验收：Qdrant + uvicorn + pnpm build + preview :4173（MCP 默认关）
  docker   生产推荐：Compose 启动 nginx(:80) + backend + qdrant

子命令:
  ./scripts/run.sh dev
  ./scripts/run.sh prod
  ./scripts/run.sh docker              # 启动栈
  ./scripts/run.sh docker:build        # 构建镜像
  ./scripts/run.sh docker:down         # 停止栈
  ./scripts/run.sh docker:logs         # backend 日志

环境变量（dev / prod 共用）:
  START_MCP=0|1           是否启动 extensions/mcp/ssh（默认 0，故障运维需显式开启）
  START_LANGFUSE=0|1      是否启动 Langfuse 栈（默认 0）
  NOESIS_CONFIG_PATH        覆盖 yaml 路径

prod 额外:
  FRONTEND_PORT=4173      preview 端口
  SKIP_FRONTEND_BUILD=1   跳过 pnpm build（dist 已存在时）

配置矩阵:
  模式     | 密钥文件              | 运行参数
  dev      | backend/.env          | backend/config.yaml
  prod     | backend/.env.prod     | backend/config.prod.yaml
  docker   | deploy/.env.docker    | deploy/config.docker.yaml

Compose 文件: deploy/docker-compose.yml
EOF
}

mode_dev() {
  export APP_ENV=dev
  export NOESIS_CONFIG_PATH="${NOESIS_CONFIG_PATH:-$BACKEND/config.yaml}"
  ensure_file "$BACKEND/config.yaml" "$BACKEND/config.example.yaml"
  if [[ ! -f "$BACKEND/.env" ]]; then
    ensure_file "$BACKEND/.env" "$BACKEND/.env.example"
  fi
  log "→ scripts/dev.sh"
  exec "$ROOT/scripts/dev.sh" "$@"
}

mode_prod() {
  export APP_ENV=prod
  export NOESIS_CONFIG_PATH="${NOESIS_CONFIG_PATH:-$BACKEND/config.prod.yaml}"
  log "→ scripts/prod.sh"
  exec "$ROOT/scripts/prod.sh" "$@"
}

compose_env_file="$DEPLOY/.env.docker"

export_compose_host_env() {
  export NOESIS_HOST_DATA_DIR="${NOESIS_HOST_DATA_DIR:-$ROOT/.data}"
  export NOESIS_HOST_SKILLS_DIR="${NOESIS_HOST_SKILLS_DIR:-$ROOT/extensions/skills}"
}

docker_compose() {
  export_compose_host_env
  docker compose -f "$COMPOSE_FILE" --env-file "$compose_env_file" "$@"
}

mode_docker() {
  export APP_ENV=prod
  ensure_file "$compose_env_file" "$DEPLOY/.env.docker.example"
  if [[ ! -f "$DEPLOY/config.docker.yaml" ]]; then
    die "缺少 $DEPLOY/config.docker.yaml"
  fi
  log "模式=docker | compose=$COMPOSE_FILE | env=$compose_env_file"
  cd "$ROOT"
  docker_compose up -d "$@"
  log "访问 http://localhost | 健康: curl -sS http://localhost/health"
}

mode_docker_build() {
  ensure_file "$compose_env_file" "$DEPLOY/.env.docker.example"
  cd "$ROOT"
  docker_compose build "$@"
}

mode_docker_down() {
  cd "$ROOT"
  docker_compose down "$@"
}

mode_docker_logs() {
  cd "$ROOT"
  docker_compose logs -f backend "$@"
}

main() {
  local cmd="${1:-}"
  shift || true

  case "$cmd" in
    dev) mode_dev "$@" ;;
    prod) mode_prod "$@" ;;
    docker) mode_docker "$@" ;;
    docker:build) mode_docker_build "$@" ;;
    docker:down) mode_docker_down "$@" ;;
    docker:logs) mode_docker_logs "$@" ;;
    -h|--help|help|"") usage ;;
    *)
      die "未知命令: ${cmd}（使用 ./scripts/run.sh help）"
      ;;
  esac
}

main "$@"
