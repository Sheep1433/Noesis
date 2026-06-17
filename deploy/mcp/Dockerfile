# MCP 故障运维沙箱镜像：内含 openssh-client / sshpass，供 extensions/mcp/docker-ssh 在容器内执行远程 SSH
# 构建: docker build -t noesis/mcp-ubuntu-ssh:latest -f deploy/mcp/Dockerfile deploy/mcp
FROM ubuntu:24.04

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    openssh-client \
    sshpass \
    file \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
