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

```json
{
  "mcpServers": {
    "fault_ops": {
      "transport": "streamable_http",
      "url": "http://localhost:8000/mcp"
    }
  },
  "profiles": {
    "fault_operation": ["fault_ops"],
    "simple_mcp": ["fault_ops"]
  }
}
```

`profiles` 将 Agent 场景映射到要连接的 `mcpServers` 键；支持 `stdio` / `sse` / `streamable_http` 等 `langchain-mcp-adapters` 传输类型。URL 中可用 `${ENV_VAR}` 引用环境变量。

## MCP 本地启动

```bash
cd extensions/mcp/ssh && uv run python server.py --transport http --port 8000
```

或通过全栈脚本：

```bash
START_MCP=1 ./scripts/run.sh dev
```
