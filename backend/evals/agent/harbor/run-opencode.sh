#!/usr/bin/env bash
# Terminal-Bench: Harbor + OpenCode（deepseek-v4-flash-free / OpenCode Zen Free）
#
# 直连 https://opencode.ai/zen ，不经过本机 cc-switch（容器内 127.0.0.1 不可达）。
# 与 cc-switch「OpenCode Zen Free」供应商一致：OPENCODE_API_KEY 默认 public。
#
# 前置: Docker、uv tool install harbor
#
# 用法:
#   # 本地 CLI 精选 10 题（已下载到 datasets/terminal-bench-cli-10，无图像/视频）
#   ./run-cli-10.sh --job-name tbench-cli-10
#
#   # 远程 registry 数据集
#   HARBOR_DATASET=terminal-bench-sample@2.0 ./run-opencode.sh --n-tasks 1 --job-name smoke-opencode
#   ./run-opencode.sh --n-tasks 10 --job-name tbench-opencode-10
#
#   # 任意本地任务目录（相对本脚本或绝对路径）
#   HARBOR_TASKS_PATH=datasets/terminal-bench-cli-10 ./run-opencode.sh --n-tasks 10 --job-name my-job
#
# 自定义密钥（非 Zen Free）:
#   export OPENCODE_API_KEY=sk-... && ./run-opencode.sh ...
set -euo pipefail

cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"

export OPENCODE_API_KEY="${OPENCODE_API_KEY:-public}"
export OPENCODE_FAKE_VCS="${OPENCODE_FAKE_VCS:-git}"
export HARBOR_OPENCODE_MODEL="${HARBOR_OPENCODE_MODEL:-opencode/deepseek-v4-flash-free}"
export HARBOR_DATASET="${HARBOR_DATASET:-terminal-bench@2.0}"
export HARBOR_TASKS_PATH="${HARBOR_TASKS_PATH:-}"
# task.toml agent timeout 默认 900s；×6 = 5400s。gpt2-codegolf 等难题常需 >30min。
export HARBOR_AGENT_TIMEOUT_MULT="${HARBOR_AGENT_TIMEOUT_MULT:-6}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -n "${HARBOR_TASKS_PATH}" && "${HARBOR_TASKS_PATH}" != /* ]]; then
  HARBOR_TASKS_PATH="${SCRIPT_DIR}/${HARBOR_TASKS_PATH}"
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

mkdir -p results
exec harbor run \
  "${DATASET_ARGS[@]}" \
  -a opencode \
  -m "${HARBOR_OPENCODE_MODEL}" \
  --n-tasks 1 \
  -n 1 \
  -o results \
  --agent-setup-timeout-multiplier 3 \
  --environment-build-timeout-multiplier 2 \
  --agent-timeout-multiplier "${HARBOR_AGENT_TIMEOUT_MULT}" \
  --allow-agent-host opencode.ai \
  --ae "OPENCODE_API_KEY=${OPENCODE_API_KEY}" \
  --ae "OPENCODE_FAKE_VCS=${OPENCODE_FAKE_VCS}" \
  "$@"
