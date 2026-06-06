# shellcheck shell=bash
# 本地/裸机全栈共用：Qdrant、Langfuse、MCP。由 dev.sh / prod.sh source。

: "${ROOT:?ROOT must be set before sourcing stack-common.sh}"
BACKEND_DIR="${BACKEND_DIR:-$ROOT/backend}"
FRONTEND_DIR="${FRONTEND_DIR:-$ROOT/frontend}"
MCP_DIR="${MCP_DIR:-$ROOT/mcp_docker_ssh}"
QDRANT_CONTAINER="${QDRANT_CONTAINER:-noesis-qdrant}"
QDRANT_STORAGE="${QDRANT_STORAGE:-$ROOT/qdrant_storage}"

MCP_PID=""
BACKEND_PID=""
FRONTEND_PID=""

log_info() { echo -e "${GREEN:-}[INFO]${NC:-} $*"; }
log_warn() { echo -e "${YELLOW:-}[WARN]${NC:-} $*"; }
log_error() { echo -e "${RED:-}[ERROR]${NC:-} $*" >&2; }

check_command() {
  if ! command -v "$1" &>/dev/null; then
    log_error "$1 not found. Please install $1 first."
    exit 1
  fi
}

start_qdrant() {
  if ! command -v docker &>/dev/null; then
    log_warn "Docker 未安装，跳过 Qdrant。请确保 localhost:6333 可访问。"
    log_warn "  手动: docker run -d --name $QDRANT_CONTAINER -p 6333:6333 -p 6334:6334 -v $QDRANT_STORAGE:/qdrant/storage qdrant/qdrant"
    return 0
  fi

  if docker ps -q -f "name=^${QDRANT_CONTAINER}$" | grep -q .; then
    log_info "Qdrant 已在运行 ($QDRANT_CONTAINER)"
    return 0
  fi

  if docker ps -aq -f "name=^${QDRANT_CONTAINER}$" | grep -q .; then
    log_info "启动已有 Qdrant 容器..."
    docker start "$QDRANT_CONTAINER" >/dev/null
    log_info "Qdrant: http://127.0.0.1:6333/dashboard"
    return 0
  fi

  log_info "创建并启动 Qdrant 容器..."
  mkdir -p "$QDRANT_STORAGE"
  docker run -d \
    --name "$QDRANT_CONTAINER" \
    -p 6333:6333 \
    -p 6334:6334 \
    -v "$QDRANT_STORAGE:/qdrant/storage" \
    qdrant/qdrant >/dev/null
  log_info "Qdrant: http://127.0.0.1:6333/dashboard"
}

start_langfuse() {
  local enable="${START_LANGFUSE:-0}"
  if [[ "$enable" != "1" && "$enable" != "true" ]]; then
    log_info "Langfuse 未启动（可选）。需要时: START_LANGFUSE=1 ./scripts/run.sh <dev|prod>"
    return 0
  fi

  if ! command -v docker &>/dev/null; then
    log_warn "Docker 未安装，无法启动 Langfuse。"
    return 0
  fi

  log_info "启动 Langfuse 观测栈（首次约 2–3 分钟）..."
  docker compose -f "$ROOT/deploy/docker-compose.langfuse.yml" up -d
  log_info "Langfuse UI: http://localhost:3000"
}

start_mcp() {
  local enable="${START_MCP:-1}"
  if [[ "$enable" != "1" && "$enable" != "true" ]]; then
    log_info "MCP 未启动（故障运维等能力不可用）。需要时: START_MCP=1"
    return 0
  fi

  log_info "启动 MCP server..."
  cd "$MCP_DIR"
  uv run python server.py &
  MCP_PID=$!
  log_info "MCP server started (PID: $MCP_PID)"
}

stack_cleanup() {
  log_info "Stopping app processes..."
  if [[ -n "$MCP_PID" ]]; then
    kill "$MCP_PID" 2>/dev/null || true
  fi
  if [[ -n "$BACKEND_PID" ]]; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
  if [[ -n "$FRONTEND_PID" ]]; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
  log_info "Qdrant / Langfuse 容器默认保留；停止 Qdrant: docker stop $QDRANT_CONTAINER"
}
