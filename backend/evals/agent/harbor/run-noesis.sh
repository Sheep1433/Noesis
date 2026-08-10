#!/usr/bin/env bash
# 用法：./run-noesis.sh [smoke|cli-10]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
HARBOR_BIN="${SCRIPT_DIR}/.venv/bin/harbor"
MODE="${1:-smoke}"
export PYTHONPATH="${BACKEND_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

if [[ ! -x "${HARBOR_BIN}" ]]; then
  echo "Harbor 评测环境不存在，请先运行：${SCRIPT_DIR}/setup.sh" >&2
  exit 1
fi

export OPENCODE_API_KEY="${OPENCODE_API_KEY:-public}"
export HARBOR_NOESIS_MODEL="${HARBOR_NOESIS_MODEL:-opencode/deepseek-v4-flash-free}"
TASKS_PATH="${SCRIPT_DIR}/datasets/terminal-bench-cli-10"

case "${MODE}" in
  smoke)
    RUN_ARGS=(--n-tasks 1 --include-task-name fix-git --job-name noesis-smoke)
    ;;
  cli-10)
    RUN_ARGS=(--n-tasks 10 --job-name noesis-cli-10)
    ;;
  *)
    echo "用法: $0 [smoke|cli-10]" >&2
    exit 2
    ;;
esac

cd "${SCRIPT_DIR}"
mkdir -p results
exec "${HARBOR_BIN}" run \
  -p "${TASKS_PATH}" \
  --agent-import-path evals.agent.harbor.noesis_agent:NoesisHarborAgent \
  -m "${HARBOR_NOESIS_MODEL}" \
  -n 1 \
  -o results \
  --agent-timeout-multiplier 4 \
  --allow-agent-host opencode.ai \
  "${RUN_ARGS[@]}"
