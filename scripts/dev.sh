#!/usr/bin/env bash
# 本地全栈开发：Qdrant +（可选）MCP + 后端 + 前端 dev server
# 由 ./scripts/run.sh dev 调用

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

trap stack_cleanup EXIT INT TERM

main() {
  export APP_ENV="${APP_ENV:-dev}"
  export NOESIS_CONFIG_PATH="${NOESIS_CONFIG_PATH:-$BACKEND_DIR/config.yaml}"

  log_info "模式=dev | APP_ENV=$APP_ENV | CONFIG=$NOESIS_CONFIG_PATH"

  check_command uv
  check_command pnpm

  start_qdrant
  start_langfuse
  start_mcp

  log_info "启动后端 (app.py, reload 见 config.yaml) ..."
  cd "$BACKEND_DIR"
  uv run app.py &
  BACKEND_PID=$!
  log_info "Backend started (PID: $BACKEND_PID)"

  log_info "启动前端 dev server ..."
  cd "$FRONTEND_DIR"
  pnpm dev &
  FRONTEND_PID=$!
  log_info "Frontend started (PID: $FRONTEND_PID)"

  log_info ""
  log_info "=========================================="
  log_info "Noesis dev 已启动"
  log_info "  - Frontend: http://127.0.0.1:2048"
  log_info "  - Backend:  http://127.0.0.1:8089"
  log_info "  - Qdrant:   http://127.0.0.1:6333/dashboard"
  if [[ "${START_LANGFUSE:-0}" == "1" || "${START_LANGFUSE:-0}" == "true" ]]; then
    log_info "  - Langfuse: http://localhost:3000"
  fi
  if [[ -n "$MCP_PID" ]]; then
    log_info "  - MCP:      PID $MCP_PID"
  fi
  log_info "=========================================="
  log_info "按 Ctrl+C 停止应用进程"

  wait "$BACKEND_PID" "$FRONTEND_PID"
  if [[ -n "$MCP_PID" ]]; then
    wait "$MCP_PID" 2>/dev/null || true
  fi
}

main "$@"
