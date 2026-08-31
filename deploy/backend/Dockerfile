# syntax=docker/dockerfile:1
# Noesis 后端生产镜像（由 deploy/docker-compose.yml 引用，构建上下文为仓库根目录）
#
# 瘦身约定（约 −500MB）：
# - DeepDoc PDF 重栈（cv2/onnxruntime/sklearn/xgboost）与飞书 SDK 是
#   optional extras，uv sync 默认不装；PDF 解析降级 pdfplumber 纯文本
# - noesis-cli（评测用）不在生产镜像安装
# - healthcheck 用 python urllib，无需 apt 安装 curl
FROM python:3.11-slim-bookworm AS builder

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    python3-dev \
  && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONPATH=/app

COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/packages/noesis-core/pyproject.toml packages/noesis-core/pyproject.toml
COPY backend/packages/noesis-cli/pyproject.toml packages/noesis-cli/pyproject.toml

RUN uv sync --frozen --no-install-project --no-install-workspace --no-dev

COPY backend/ ./

# Skills 包（深度研究 / Skills 管理 API）
COPY extensions/skills ./extensions/skills
# 平台 MCP 配置（故障运维 profile、probe 合并目录）
COPY extensions/mcp ./extensions/mcp

# --no-install-package noesis-cli：workspace 成员默认全装，显式排除评测 CLI
RUN uv sync --frozen --no-dev --no-install-package noesis-cli

FROM python:3.11-slim-bookworm

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH"

COPY --from=builder /app /app

EXPOSE 8089

HEALTHCHECK --interval=20s --timeout=5s --start-period=90s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8089/health', timeout=4).status == 200 else 1)"

# 单 worker：Noesis P0 只允许一个 active backend 进程。
# PostgreSQL advisory lock 是多容器/多进程的最终保护——第二个实例无法获取 lock 会 fail-fast。
# 禁止添加 --workers > 1 或部署多个 backend 副本。
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8089"]
