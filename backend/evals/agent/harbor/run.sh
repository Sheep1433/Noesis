#!/usr/bin/env bash
# Terminal-Bench: Harbor + Claude Code
# 前置: Docker, uv tool install harbor, claude CLI, ~/.claude/settings.json
# 用法: ./run.sh --n-tasks 1 --job-name smoke
set -euo pipefail

cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"

eval "$(python3 <<'PY'
import json, pathlib, shlex
p = pathlib.Path.home() / ".claude/settings.json"
for k, v in (json.loads(p.read_text()).get("env") or {}).items():
    if k.startswith("ANTHROPIC_"):
        print(f"export {k}={shlex.quote(str(v))}")
PY
)"

mkdir -p results
exec harbor run \
  -d terminal-bench@2.0 \
  -a claude-code \
  -m "${ANTHROPIC_MODEL:-deepseek-v4-flash}" \
  --n-tasks 1 \
  -n 1 \
  -o results \
  --agent-setup-timeout-multiplier 3 \
  "$@"
