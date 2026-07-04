# sandbox-runner：内网用户沙箱 lifecycle + docker exec 代理（Docker socket / DooD）
FROM python:3.11-slim-bookworm

RUN apt-get update \
  && apt-get install -y --no-install-recommends curl ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY deploy/sandbox-runner/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY deploy/sandbox-runner/ ./

ENV SANDBOX_RUNNER_HOST=0.0.0.0 \
    SANDBOX_RUNNER_PORT=8090

EXPOSE 8090

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${SANDBOX_RUNNER_PORT}/health" || exit 1

CMD ["python", "main.py"]
