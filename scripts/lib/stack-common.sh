# shellcheck shell=bash
# 本地/裸机全栈共用：Qdrant、Langfuse、MCP。由 dev.sh / prod.sh source。

: "${ROOT:?ROOT must be set before sourcing stack-common.sh}"
BACKEND_DIR="${BACKEND_DIR:-$ROOT/backend}"
FRONTEND_DIR="${FRONTEND_DIR:-$ROOT/frontend}"
EXTENSIONS_DIR="${EXTENSIONS_DIR:-$ROOT/extensions}"
MCP_DIR="${MCP_DIR:-$EXTENSIONS_DIR/mcp/ssh}"
QDRANT_CONTAINER="${QDRANT_CONTAINER:-noesis-qdrant}"
QDRANT_STORAGE="${QDRANT_STORAGE:-$ROOT/.data/qdrant}"

MCP_PID=""
BACKEND_PID=""
FRONTEND_PID=""
SANDBOX_RUNNER_PID=""

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
  local enable="${START_MCP:-0}"
  if [[ "$enable" != "1" && "$enable" != "true" ]]; then
    log_info "MCP 未启动（故障运维等能力不可用）。需要时: START_MCP=1 ./scripts/run.sh <dev|prod>"
    return 0
  fi

  if ! command -v ssh &>/dev/null; then
    log_warn "未找到 ssh 客户端，MCP 远程诊断不可用。请安装 openssh-client。"
  elif ! command -v sshpass &>/dev/null; then
    log_warn "未找到 sshpass；setup_passwordless_login 不可用（read/bash/grep 仍可用）。"
  fi

  log_info "启动 MCP server..."
  cd "$MCP_DIR"
  # 显式 http + 8000，与 mcp.json / NOESIS_MCP_REMOTE_URL 默认一致
  uv run python server.py --transport http --host 127.0.0.1 --port 8000 &
  MCP_PID=$!
  log_info "MCP server started (PID: $MCP_PID) → http://127.0.0.1:8000/mcp"
}

_should_start_sandbox_runner() {
  if [[ "${SANDBOX_BACKEND:-}" == "local_shell" ]]; then
    return 1
  fi
  local config="${NOESIS_CONFIG_PATH:-$BACKEND_DIR/config.yaml}"
  if [[ -f "$config" ]] && grep -A8 '^sandbox:' "$config" | grep -qE 'backend:[[:space:]]*local_shell'; then
    return 1
  fi
  return 0
}

_sandbox_runner_health_url() {
  local config="${NOESIS_CONFIG_PATH:-$BACKEND_DIR/config.yaml}"
  local url="http://127.0.0.1:8090"
  if [[ -f "$config" ]]; then
    local parsed
    parsed="$(grep -A8 '^sandbox:' "$config" | grep -E 'runner_url:' | head -1 | sed -E 's/.*runner_url:[[:space:]]*//; s/[[:space:]]+#.*//; s/^"//; s/"$//')"
    if [[ -n "$parsed" ]]; then
      url="${parsed%/}"
    fi
  fi
  echo "$url"
}

wait_sandbox_runner() {
  local base url
  base="$(_sandbox_runner_health_url)"
  for _ in $(seq 1 30); do
    if curl -fsS "${base}/health" &>/dev/null; then
      log_info "sandbox-runner 就绪: ${base}"
      return 0
    fi
    sleep 1
  done
  log_warn "sandbox-runner 健康检查超时 (${base})"
  return 1
}

start_sandbox_runner() {
  if ! _should_start_sandbox_runner; then
    log_info "sandbox.backend=local_shell，跳过 sandbox-runner"
    return 0
  fi

  local base
  base="$(_sandbox_runner_health_url)"
  if curl -fsS "${base}/health" &>/dev/null; then
    log_info "sandbox-runner 已在运行 (${base})"
    return 0
  fi

  if ! command -v docker &>/dev/null; then
    log_warn "Docker 未安装，无法启动 sandbox-runner（AIO 沙箱不可用）"
    return 0
  fi

  if [[ "${SANDBOX_RUNTIME:-docker}" == "docker" ]]; then
    local slim_image="${SANDBOX_DOCKER_IMAGE:-noesis/sandbox-slim:latest}"
    if ! docker image inspect "$slim_image" &>/dev/null; then
      log_info "本地无沙箱镜像 ${slim_image}，开始构建..."
      if ! docker build -t "$slim_image" -f "$ROOT/deploy/sandbox-slim/Dockerfile" "$ROOT"; then
        log_warn "沙箱镜像构建失败，docker 沙箱模式将不可用（可改用 sandbox.backend=local_shell）"
      fi
    fi
  fi

  if ! command -v uv &>/dev/null; then
    log_warn "uv 未安装，无法自动启动 sandbox-runner"
    return 0
  fi

  log_info "启动 sandbox-runner（路径自动对齐仓库 .data/ 与 extensions/skills）..."
  cd "$ROOT/deploy/sandbox-runner"
  export SANDBOX_RUNTIME="${SANDBOX_RUNTIME:-docker}"
  export SANDBOX_DOCKER_IMAGE="${SANDBOX_DOCKER_IMAGE:-noesis/sandbox-slim:latest}"
  uv run python main.py &
  SANDBOX_RUNNER_PID=$!
  log_info "sandbox-runner started (PID: $SANDBOX_RUNNER_PID)"
  wait_sandbox_runner || true
}

stack_cleanup() {
  log_info "Stopping app processes..."
  if [[ -n "$SANDBOX_RUNNER_PID" ]]; then
    kill "$SANDBOX_RUNNER_PID" 2>/dev/null || true
  fi
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
