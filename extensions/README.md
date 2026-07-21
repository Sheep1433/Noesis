# extensions

Noesis 可插拔扩展的统一目录，与 `backend/`、`frontend/` 应用代码分离。

| 子目录 | 说明 |
|--------|------|
| `skills/` | Agent Skills 包（`SKILL.md` 等），深度研究 Agent 以 `/skills/` 虚拟路径只读挂载 |
| `mcp/ssh/` | 故障运维 MCP 服务（宿主机 SSH），`START_MCP=1` 时由启动脚本拉起 |
| `mcp/mcp.json` | Agent 侧 MCP 客户端连接配置（`mcpServers` + `profiles`） |

## 路径覆盖

- **Skills 根目录**：`backend/config.yaml` → `other.skills_filesystem_root`（空则默认 `extensions/skills`）
- **MCP 客户端配置**：`extensions/mcp/mcp.json`；可用 `other.mcp_config_path` 或环境变量 `MCP_CONFIG_PATH` 覆盖
- **扩展根目录**：环境变量 `EXTENSIONS_DIR`（默认仓库根下 `extensions/`）
- **MCP 服务目录**：环境变量 `MCP_DIR`（默认 `extensions/mcp/ssh`）

## mcp.json 示例

平台默认提供两个 HTTP MCP：

```json
{
  "mcpServers": {
    "context7": {
      "transport": "streamable_http",
      "url": "https://mcp.context7.com/mcp",
      "headers": {
        "CONTEXT7_API_KEY": "${CONTEXT7_API_KEY}"
      },
      "display_name": "Context7"
    },
    "remote_ops": {
      "transport": "streamable_http",
      "url": "${NOESIS_MCP_REMOTE_URL}",
      "display_name": "Remote Ops (SSH)"
    }
  },
  "profiles": {
    "fault_operation": ["remote_ops"],
    "simple_mcp": ["remote_ops"]
  }
}
```

- `context7`：文档检索（需 `CONTEXT7_API_KEY`，可选）
- `remote_ops`：本仓库 `extensions/mcp/ssh` 远程运维 MCP；默认 URL `http://localhost:8000/mcp`（可用 `NOESIS_MCP_REMOTE_URL` 覆盖）

- 用户个人配置在 `.data/users/{uid}/mcp.json`（首次空配置会 seed 与上表相同的两项，**字面量 URL**，不含 `${ENV}`）。`profiles` 将 Agent 场景映射到要连接的 `mcpServers` 键。

平台 `extensions/mcp/mcp.json` 仍可用 `${ENV_VAR}` 注入部署密钥（如 `CONTEXT7_API_KEY`）；**个人编辑器只接受字面量**。

## MCP 本地启动

```bash
cd extensions/mcp/ssh && uv run python server.py --transport http --port 8000
```

或通过全栈脚本：

```bash
START_MCP=1 ./scripts/run.sh dev
```
