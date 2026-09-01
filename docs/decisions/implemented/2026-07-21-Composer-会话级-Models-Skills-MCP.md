# 决策：Composer 会话级 Models / Skills / MCP

状态：implemented
日期：2026-07-21
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **动机**：补齐用户自定义 MCP + Cursor 式 Composer 勾选；此前 MCP 仅部署级 `mcp.json` 且绑死 `FAULT_OPERATION_QA`。
- **配置**：平台 `extensions/mcp/mcp.json` + 用户 `.noesis/users/{uid}/mcp.json`（同名用户覆盖）；用户仅允许 `streamable_http`/`sse`。
- **会话 extra**：`mcp_servers`（缺省：FAULT 回退 profile，其它 `[]`）、`enabled_skills`（缺省全部）。
- **API**：`/api/mcp/servers` list/PUT/DELETE/probe；打开菜单只拉元数据，不 `get_tools()`。
- **Agent**：COMMON / FAULT / SUPER 均可按勾选挂 MCP；SUPER 按 `enabled_skills` 过滤 SkillsMiddleware sources；TEST_CASE 协调器不挂 MCP。
- **UI**：`ChatComposerToolbar` `+` 菜单含 Models / Skills / MCP；添加 MCP 对话框写用户配置。
- **OpenSpec**：`openspec/changes/composer-session-tools/`。
