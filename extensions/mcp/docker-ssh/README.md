# docker-ssh MCP

故障运维 MCP 服务：通过 Docker 沙箱容器执行远程 SSH 操作。

位于仓库 `extensions/mcp/docker-ssh/`；详见 [extensions/README.md](../../README.md)。

## 启动

```bash
# 推荐：由 run.sh 一并拉起（含沙箱镜像自动构建）
START_MCP=1 ./scripts/run.sh dev

# 或手动
docker build -t noesis/mcp-ubuntu-ssh:latest -f deploy/mcp/Dockerfile deploy/mcp
cd extensions/mcp/docker-ssh && uv run python server.py --transport http --port 8000
```

沙箱镜像定义：`deploy/mcp/Dockerfile`（内含 `openssh-client`、`sshpass`）。
