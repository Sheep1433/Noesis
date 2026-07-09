# syntax=docker/dockerfile:1
# Noesis 后端生产镜像（由 deploy/docker-compose.yml 引用，构建上下文为仓库根目录）
FROM python:3.11-slim-bookworm AS builder

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    default-libmysqlclient-dev \
    python3-dev \
  && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONPATH=/app

COPY backend/pyproject.toml backend/uv.lock ./

RUN uv sync --frozen --no-install-project --no-dev

COPY backend/ ./

# Skills 包（深度研究 / Skills 管理 API）
COPY extensions/skills ./extensions/skills

RUN uv sync --frozen --no-dev

FROM python:3.11-slim-bookworm

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    libmariadb3 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH"

COPY --from=builder /app /app

EXPOSE 8089

HEALTHCHECK --interval=20s --timeout=5s --start-period=90s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8089/health >/dev/null || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8089"]
