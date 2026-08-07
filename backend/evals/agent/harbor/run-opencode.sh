#!/usr/bin/env bash
# 用法：./run-opencode.sh [smoke|cli-10]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HARBOR_BIN="${SCRIPT_DIR}/.venv/bin/harbor"
MODE="${1:-smoke}"
cd "${SCRIPT_DIR}"

if [[ ! -x "${HARBOR_BIN}" ]]; then
  echo "Harbor 评测环境不存在，请先运行：${SCRIPT_DIR}/setup.sh" >&2
  exit 1
fi

export OPENCODE_API_KEY="${OPENCODE_API_KEY:-public}"
export OPENCODE_FAKE_VCS="${OPENCODE_FAKE_VCS:-git}"
export HARBOR_OPENCODE_MODEL="${HARBOR_OPENCODE_MODEL:-opencode/deepseek-v4-flash-free}"

case "${MODE}" in
  smoke)
    RUN_ARGS=(--n-tasks 1 --include-task-name fix-git --job-name opencode-smoke)
    ;;
  cli-10)
    RUN_ARGS=(--n-tasks 10 --job-name opencode-cli-10)
    ;;
  *)
    echo "用法: $0 [smoke|cli-10]" >&2
    exit 2
    ;;
esac

mkdir -p results
exec "${HARBOR_BIN}" run \
  -p "${SCRIPT_DIR}/datasets/terminal-bench-cli-10" \
  -a opencode \
  -m "${HARBOR_OPENCODE_MODEL}" \
  -n 1 \
  -o results \
  --agent-setup-timeout-multiplier 3 \
  --environment-build-timeout-multiplier 2 \
  --agent-timeout-multiplier 4 \
  --allow-agent-host opencode.ai \
  --ae "OPENCODE_API_KEY=${OPENCODE_API_KEY}" \
  --ae "OPENCODE_FAKE_VCS=${OPENCODE_FAKE_VCS}" \
  "${RUN_ARGS[@]}"
