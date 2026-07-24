# Noesis Agent 沙箱 slim 镜像：bash + git/curl/python3/nodejs，经 sandbox-runner docker exec 使用
# 构建: docker build -t noesis/sandbox-slim:latest -f deploy/sandbox-slim/Dockerfile .
FROM debian:bookworm-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    nodejs \
    npm \
    python3 \
    python3-pip \
    python3-venv \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd --gid 10001 sandbox \
  && useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash sandbox \
  && mkdir -p /workspace /skills/public /skills/personal \
  && chown -R sandbox:sandbox /workspace /home/sandbox \
  && node -v && npm -v && python3 --version

ENV HOME=/home/sandbox \
    USER=sandbox \
    NPM_CONFIG_PREFIX=/home/sandbox/.npm-global \
    PATH=/home/sandbox/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

USER sandbox
WORKDIR /workspace

# 用户级 npm 全局目录（避免写系统路径）
RUN mkdir -p /home/sandbox/.npm-global

CMD ["sleep", "infinity"]
