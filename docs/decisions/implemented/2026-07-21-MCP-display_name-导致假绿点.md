# 决策：MCP display_name 导致假绿点

状态：implemented
日期：2026-07-21
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **现象**：管理页全绿，日志却是 `TypeError: _create_streamable_http_session() got an unexpected keyword argument 'display_name'`。
- **根因**：`mcp.json` 的 `display_name` 被原样塞进 `MultiServerMCPClient`；loader 吞异常返回空列表后，probe 仍因「server 已配置」标 `ok=True`。
- **修复**：`to_adapter_connection()` 白名单过滤连接字段；probe 直接 `get_tools()`，失败则 `ok=False`。
