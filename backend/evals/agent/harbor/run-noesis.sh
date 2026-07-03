#!/usr/bin/env bash
# Terminal-Bench: Harbor + Noesis SuperAgent（Host 侧 Agent，容器内 exec 桥接）
#
# 架构：Harbor 进程（Python 3.13）加载薄适配器 → `uv run` 子进程（backend venv）
# 经 TCP 代理把 ls/read/execute 转发到任务 Docker 容器。
#
# 前置: Docker、uv tool install harbor、backend 已 `uv sync`
#
# 用法:
#   cd backend && chmod +x evals/agent/harbor/run-noesis.sh
#   ./evals/agent/harbor/run-noesis.sh --include-task-name fix-git --job-name smoke-noesis
#   HARBOR_TASKS_PATH=evals/agent/harbor/datasets/terminal-bench-cli-10 \
#     ./evals/agent/harbor/run-noesis.sh --n-tasks 1 --job-name smoke-noesis
#
# 模型默认 opencode/deepseek-v4-flash-free（与 OpenCode baseline 对齐）:
#   export OPENCODE_API_KEY=public
#   export HARBOR_NOESIS_MODEL=opencode/deepseek-v4-flash-free
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
export PYTHONPATH="${BACKEND_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export PATH="$HOME/.local/bin:$PATH"

export OPENCODE_API_KEY="${OPENCODE_API_KEY:-public}"
export HARBOR_NOESIS_MODEL="${HARBOR_NOESIS_MODEL:-opencode/deepseek-v4-flash-free}"
export HARBOR_DATASET="${HARBOR_DATASET:-terminal-bench@2.0}"
export HARBOR_TASKS_PATH="${HARBOR_TASKS_PATH:-}"
export HARBOR_AGENT_TIMEOUT_MULT="${HARBOR_AGENT_TIMEOUT_MULT:-4}"

if [[ -n "${HARBOR_TASKS_PATH}" && "${HARBOR_TASKS_PATH}" != /* ]]; then
  HARBOR_TASKS_PATH="${BACKEND_ROOT}/${HARBOR_TASKS_PATH}"
fi

DATASET_ARGS=()
if [[ -n "${HARBOR_TASKS_PATH}" ]]; then
  if [[ ! -d "${HARBOR_TASKS_PATH}" ]]; then
    echo "HARBOR_TASKS_PATH not found: ${HARBOR_TASKS_PATH}" >&2
    exit 1
  fi
  DATASET_ARGS=(-p "${HARBOR_TASKS_PATH}")
else
  DATASET_ARGS=(-d "${HARBOR_DATASET}")
fi

cd "${SCRIPT_DIR}"
mkdir -p results
exec harbor run \
  "${DATASET_ARGS[@]}" \
  --agent-import-path evals.agent.harbor.noesis_agent:NoesisHarborAgent \
  -m "${HARBOR_NOESIS_MODEL}" \
  --n-tasks 1 \
  -n 1 \
  -o results \
  --agent-timeout-multiplier "${HARBOR_AGENT_TIMEOUT_MULT}" \
  --allow-agent-host opencode.ai \
  "$@"
