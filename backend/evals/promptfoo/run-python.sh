#!/usr/bin/env bash
# promptfoo 调用的 Python 入口：在 backend 目录下用 uv 执行。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
exec uv run python "$@"
