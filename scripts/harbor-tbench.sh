#!/usr/bin/env bash
# Terminal-Bench via Harbor + Claude Code，模型/API 与 ~/.claude/settings.json 对齐（DeepSeek 兼容端点等）。
#
# 前置：Docker 已启动；uv tool install harbor；本机已安装 claude CLI。
#
# 可选：容器内装 claude-code 较慢时加大（默认 3 → 1080s）
# export NOESIS_HARBOR_AGENT_SETUP_TIMEOUT_MULTIPLIER=3
# 可选：单题 agent 执行超时倍数（Terminal-Bench 难题建议 2~4）
# export NOESIS_HARBOR_AGENT_TIMEOUT_MULTIPLIER=2
#   ./scripts/harbor-tbench.sh --max-tasks 1
#   ./scripts/harbor-tbench.sh --max-tasks 10 --job-name tbench-ds-10
# 额外参数原样传给 harbor run（在 -- 之后），例如：
#   ./scripts/harbor-tbench.sh -- --n-concurrent 2
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JOBS_DIR="${NOESIS_HARBOR_JOBS_DIR:-$ROOT/.data/harbor-jobs}"
SETTINGS="${NOESIS_CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
TAG="${NOESIS_HARBOR_JOB_NAME:-tbench-$(date +%Y%m%d-%H%M%S)}"
MAX_TASKS=1
EXTRA=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-tasks)
      MAX_TASKS="${2:?}"
      shift 2
      ;;
    --job-name)
      TAG="${2:?}"
      shift 2
      ;;
    --)
      shift
      EXTRA=("$@")
      break
      ;;
    *)
      EXTRA+=("$1")
      shift
      ;;
  esac
done

if [[ ! -f "$SETTINGS" ]]; then
  echo "未找到 Claude 配置: $SETTINGS" >&2
  exit 2
fi

eval "$(SETTINGS_PATH="$SETTINGS" python3 <<'PY'
import json, os, pathlib, shlex
settings = json.loads(pathlib.Path(os.environ["SETTINGS_PATH"]).read_text())
env = settings.get("env") or {}
for key, val in env.items():
    if key.startswith("ANTHROPIC_"):
        print(f"export {key}={shlex.quote(str(val))}")
PY
)"

if [[ -z "${ANTHROPIC_AUTH_TOKEN:-}${ANTHROPIC_API_KEY:-}" ]]; then
  echo "settings.json 中缺少 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY" >&2
  exit 2
fi

export PATH="${HOME}/.local/bin:${PATH}"
if ! command -v harbor >/dev/null 2>&1; then
  echo "未找到 harbor，请执行: uv tool install harbor" >&2
  exit 2
fi

MODEL="${NOESIS_HARBOR_MODEL:-${ANTHROPIC_MODEL:-deepseek-v4-flash}}"
mkdir -p "$JOBS_DIR"

echo "Harbor Terminal-Bench smoke/full run"
echo "  jobs_dir: $JOBS_DIR"
echo "  job_name: $TAG"
echo "  model:    $MODEL"
echo "  base_url: ${ANTHROPIC_BASE_URL:-<default anthropic>}"
echo "  n_tasks:  $MAX_TASKS"
echo

exec harbor run \
  -d terminal-bench@2.0 \
  -a claude-code \
  -m "$MODEL" \
  --n-tasks "$MAX_TASKS" \
  -n 1 \
  -o "$JOBS_DIR" \
  --job-name "$TAG" \
  --agent-setup-timeout-multiplier "${NOESIS_HARBOR_AGENT_SETUP_TIMEOUT_MULTIPLIER:-3}" \
  --agent-timeout-multiplier "${NOESIS_HARBOR_AGENT_TIMEOUT_MULTIPLIER:-1}" \
  ${EXTRA[@]+"${EXTRA[@]}"}
