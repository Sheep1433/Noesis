# 决策：MCP 目录与配置对齐（Context7 + remote_ops）

状态：implemented
日期：2026-07-21
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **问题**：管理页状态列出平台 `fault_ops`/`ssh`，右侧编辑器却是空 `mcpServers`，两边不一致；连接失败日志只见 `TaskGroup` 笼统信息。
- **平台默认**：`extensions/mcp/mcp.json` 改为 `context7`（https://mcp.context7.com/mcp）+ `remote_ops`（`${NOESIS_MCP_REMOTE_URL}`，默认 `http://localhost:8000/mcp`）；FAULT/simple_mcp profile 指向 `remote_ops`。
- **用户 seed**：无文件或 `mcpServers` 为空时写入与平台相同的两项，保证编辑器 ↔ 状态列表一致。
- **管理页 scope**：`/servers/status` 默认 `scope=user`；Composer 目录仍用 `scope=all` 合并视图。
- **环境变量**：`CONTEXT7_API_KEY`、`NOESIS_MCP_REMOTE_URL`；未设密钥置空，未设远程 URL 用默认 localhost。
- **日志**：loader 展开 ExceptionGroup / TaskGroup 子异常，并打印目标 URL。
