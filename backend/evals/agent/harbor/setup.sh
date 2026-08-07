#!/usr/bin/env bash
# 一次性创建 Noesis Harbor 评测环境。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

uv venv --python 3.13 "${VENV_DIR}"
uv pip install \
  --python "${VENV_DIR}/bin/python" \
  'harbor==0.15.0' \
  'litellm==1.90.0' \
  'psycopg[binary,pool]>=3.2.0' \
  --editable "${BACKEND_ROOT}/packages/harness"

echo "Harbor 评测环境已创建：${VENV_DIR}"
