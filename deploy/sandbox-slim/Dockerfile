# Noesis Agent 沙箱 slim 镜像：bash + git/curl/python3，经 sandbox-runner docker exec 使用
# 构建: docker build -t noesis/sandbox-slim:latest -f deploy/sandbox-slim/Dockerfile .
FROM debian:bookworm-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    python3 \
    python3-pip \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd --gid 10001 sandbox \
  && useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash sandbox \
  && mkdir -p /workspace /skills/public /skills/personal \
  && chown -R sandbox:sandbox /workspace

USER sandbox
WORKDIR /workspace

CMD ["sleep", "infinity"]
