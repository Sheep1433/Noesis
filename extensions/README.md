# extensions

Noesis 可插拔扩展的统一目录，与 `backend/`、`frontend/` 应用代码分离。

| 子目录 | 说明 |
|--------|------|
| `skills/` | Agent Skills 包（`SKILL.md` 等），深度研究 Agent 以 `/skills/` 虚拟路径只读挂载 |
| `mcp/docker-ssh/` | 故障运维 MCP 服务（Docker + SSH 沙箱），`START_MCP=1` 时由启动脚本拉起 |

## 路径覆盖

- **Skills 根目录**：`backend/config.yaml` → `other.skills_filesystem_root`（空则默认 `extensions/skills`）
- **扩展根目录**：环境变量 `EXTENSIONS_DIR`（默认仓库根下 `extensions/`）
- **MCP 服务目录**：环境变量 `MCP_DIR`（默认 `extensions/mcp/docker-ssh`）

## MCP 本地启动

```bash
cd extensions/mcp/docker-ssh && uv run python server.py --transport http --port 8000
```

或通过全栈脚本：`START_MCP=1 ./scripts/run.sh dev`
