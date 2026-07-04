# MCP SSH

故障运维 MCP 服务：经**宿主机** `openssh-client` 执行远程 SSH 诊断（本地 Agent 隔离由 `sandbox-runner` 负责）。

位于仓库 `extensions/mcp/ssh/`；详见 [extensions/README.md](../../README.md)。

## 依赖

MCP 进程所在环境须具备：

- `ssh`（openssh-client）
- `sshpass`（仅 `setup_passwordless_login` 一次性配密钥时需要）
- `~/.ssh` 密钥（或 `config.yaml` → `ssh.ssh_dir` / 环境变量 `MCP_SSH_DIR`）

macOS：`ssh` 通常已自带；`sshpass` 可 `brew install sshpass`（或 `brew install hudochenkov/sshpass/sshpass`）。

## 启动

```bash
# 推荐：由 run.sh 一并拉起
START_MCP=1 ./scripts/run.sh dev

# 或手动
cd extensions/mcp/ssh && uv run python server.py --transport http --port 8000
```
