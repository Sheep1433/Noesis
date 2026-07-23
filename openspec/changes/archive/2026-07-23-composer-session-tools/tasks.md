## 1. Backend — user MCP + loader

- [x] 1.1 `user_data_paths`：`get_user_mcp_path` / ensure
- [x] 1.2 扩展 `mcp_config`：合并平台+用户、按 id 解析 connections、用户 transport 校验
- [x] 1.3 `load_mcp_tools_by_names(names, user_id)`（单 server 失败跳过+日志）；可选短 TTL 缓存
- [x] 1.4 `McpService` + `api/mcp_api.py`：list / PUT / DELETE / probe；注册 router
- [x] 1.5 `qa_service`：解析 `mcp_servers`（FAULT 回退）与 `enabled_skills`，加载工具并传入各 Agent
- [x] 1.6 `GeneralQAAgent` / `FaultOperationAgent` / `SuperAgent` 接受 `mcp_tools`；SuperAgent 按 `enabled_skills` 过滤 sources
- [x] 1.7 单测：merge、禁止 stdio、缺省 profile

## 2. Frontend — Composer

- [x] 2.1 `api/mcp.ts` + 会话 extra 勾选
- [x] 2.2 重构 `ChatComposerToolbar`：+ 菜单 Models / Skills / MCP
- [x] 2.3 Skills / MCP 勾选 + Add MCP 对话框
- [x] 2.4 `chat.vue` 同步会话 extra

## 3. Docs / verify

- [x] 3.1 `docs/NOTES.md` 追加知识卡片
- [x] 3.2 全量 pytest / 前端 lint（按影响范围）
  - 验证（2026-07-23）：`uv run pytest tests/ -q` → 539 passed；MCP/sandbox 相关子集通过。全仓 `pnpm lint` 有未关联 WIP 报错；scoped eslint 对 ChatComposerToolbar/Hitl/userStore 等仅剩 mcp.ts 既有 linebreak 风格问题。
