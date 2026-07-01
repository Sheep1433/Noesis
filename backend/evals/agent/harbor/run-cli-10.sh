#!/usr/bin/env bash
# Terminal-Bench：本地 CLI 精选 10 题（datasets/terminal-bench-cli-10）
#
# 偏命令行 / 配置 / 文本处理，无图像与视频题；题目已预下载，无需 registry 拉取。
#
# 用法:
#   ./run-cli-10.sh --job-name tbench-cli-10
#   ./run-cli-10.sh --include-task-name fix-git --job-name fix-git-only
#   ./run-cli-10.sh -n 2 --job-name tbench-cli-10-par
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

export HARBOR_TASKS_PATH="${HARBOR_TASKS_PATH:-${SCRIPT_DIR}/datasets/terminal-bench-cli-10}"
# CLI 子集相对全量难题更轻；仍可通过环境变量覆盖。
export HARBOR_AGENT_TIMEOUT_MULT="${HARBOR_AGENT_TIMEOUT_MULT:-4}"

exec "${SCRIPT_DIR}/run-opencode.sh" \
  --n-tasks 10 \
  "$@"
