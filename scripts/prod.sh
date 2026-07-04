#!/usr/bin/env bash
# 生产裸机全栈：Qdrant +（可选）extensions/mcp/ssh + 后端 uvicorn + 前端 build + preview
# 由 ./scripts/run.sh prod 调用

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export ROOT

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
export RED GREEN YELLOW NC

# shellcheck source=lib/stack-common.sh
source "$ROOT/scripts/lib/stack-common.sh"

FRONTEND_PORT="${FRONTEND_PORT:-4173}"

ensure_file() {
  local target="$1" template="$2"
  if [[ -f "$target" ]]; then
    return 0
  fi
  if [[ ! -f "$template" ]]; then
    log_error "缺少 ${target}，且模板 ${template} 不存在"
    exit 1
  fi
  cp "$template" "$target"
  log_warn "已从模板创建 ${target}，请修改后重新执行"
  exit 1
}

trap stack_cleanup EXIT INT TERM

wait_for_backend() {
  local label="$1"
  local url="http://127.0.0.1:${PORT}/health"
  log_info "等待后端就绪 (${label}) ..."
  for _ in $(seq 1 90); do
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
      log_error "后端进程已退出 (PID: ${BACKEND_PID})"
      exit 1
    fi
    if curl -fsS "$url" &>/dev/null; then
      log_info "后端已就绪"
      return 0
    fi
    sleep 1
  done
  log_error "后端超时未就绪: ${url}"
  exit 1
}

main() {
  export APP_ENV=prod
  export NOESIS_CONFIG_PATH="${NOESIS_CONFIG_PATH:-$BACKEND_DIR/config.prod.yaml}"

  ensure_file "$BACKEND_DIR/config.prod.yaml" "$BACKEND_DIR/config.prod.example.yaml"
  ensure_file "$BACKEND_DIR/.env.prod" "$BACKEND_DIR/.env.prod.example"

  check_command uv
  check_command pnpm

  log_info "模式=prod | APP_ENV=$APP_ENV | CONFIG=$NOESIS_CONFIG_PATH"

  start_qdrant
  start_langfuse
  start_mcp
  start_sandbox_runner

  read -r HOST PORT < <(
    cd "$BACKEND_DIR" && uv run python -c "from config.env import AppConfig; print(AppConfig.app_host, AppConfig.app_port)"
  )

  log_info "启动后端 (uvicorn, reload=off) ${HOST}:${PORT} ..."
  cd "$BACKEND_DIR"
  uv run uvicorn server:app --host "$HOST" --port "$PORT" &
  BACKEND_PID=$!
  log_info "Backend started (PID: $BACKEND_PID)"
  wait_for_backend "启动后"

  cd "$FRONTEND_DIR"
  if [[ "${SKIP_FRONTEND_BUILD:-0}" != "1" ]]; then
    log_info "构建前端 (pnpm build) ..."
    pnpm build
  else
    log_warn "SKIP_FRONTEND_BUILD=1，跳过 pnpm build"
  fi
  wait_for_backend "构建后"

  log_info "启动前端预览 ${FRONTEND_PORT} (pnpm preview, /api → 127.0.0.1:${PORT}) ..."
  export FRONTEND_PREVIEW_PORT="$FRONTEND_PORT"
  pnpm preview --host 0.0.0.0 --port "$FRONTEND_PORT" &
  FRONTEND_PID=$!
  log_info "Frontend preview started (PID: $FRONTEND_PID)"

  log_info ""
  log_info "=========================================="
  log_info "Noesis prod (bare metal) 已启动"
  log_info "  - Frontend: http://127.0.0.1:${FRONTEND_PORT}"
  log_info "  - Backend:  http://127.0.0.1:${PORT}"
  log_info "  - Qdrant:   http://127.0.0.1:6333/dashboard"
  if [[ "${START_LANGFUSE:-0}" == "1" || "${START_LANGFUSE:-0}" == "true" ]]; then
    log_info "  - Langfuse: http://localhost:3000"
  fi
  if [[ -n "$MCP_PID" ]]; then
    log_info "  - MCP:      PID $MCP_PID"
  fi
  if [[ -n "$SANDBOX_RUNNER_PID" ]]; then
    log_info "  - Sandbox:  PID $SANDBOX_RUNNER_PID (AIO runner)"
  fi
  log_info "=========================================="
  log_info "推荐生产部署: ./scripts/run.sh docker（nginx + 静态资源）"
  log_info "按 Ctrl+C 停止应用进程"

  wait "$BACKEND_PID" "$FRONTEND_PID"
  if [[ -n "$MCP_PID" ]]; then
    wait "$MCP_PID" 2>/dev/null || true
  fi
}

main "$@"
