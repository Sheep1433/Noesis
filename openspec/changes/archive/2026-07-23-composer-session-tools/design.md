## Context

- 平台 MCP：`extensions/mcp/mcp.json`（`mcpServers` + `profiles`），`FaultOperationAgent` 硬绑 `fault_operation`。
- Skills：平台 + 用户双源，已有 `/api/skills/fs/*` 与管理页；仅 `SuperAgent` 挂 `SkillsMiddleware`。
- Composer：`ChatComposerToolbar` 仅有附件、KB、独立 `ModelSelector`。
- 会话已有 `extra.model_id` / `kb_*` 合并模式可复用。

## Goals / Non-Goals

**Goals**

- Composer `+` 菜单可浏览 Models / Skills / MCP（元数据 list，打开菜单不连 MCP）。
- 按会话勾选 MCP server；所有生产 `qa_type` 发问时按勾选加载工具。
- 用户可 CRUD 自己的 MCP server（HTTP/SSE）。
- Skills 按会话勾选过滤 SuperAgent 索引（未勾选不出现在 skills 提示）。

**Non-Goals**

- 不做 Plan/Debug/Ask 模式（继续用顶栏 `qa_type`）。
- 不做 Cursor 级实时 multiplayer；不做 stdio 用户 MCP。
- 不在打开菜单时 `get_tools()` / probe（probe 为显式按钮）。
- 不把 Skills 文件内容塞进菜单（只列包名）。

## Decisions

1. **配置合并**：`resolve_server_config(name, user_id)` 先查用户 `mcp.json`，再查平台；同名用户覆盖平台。
2. **会话字段**：
   - `extra.mcp_servers: string[]` — 启用的 server id；键缺失时：FAULT 回退 profile，其它 `[]`。
   - `extra.enabled_skills: string[]` — SuperAgent 启用的 skill 包名；键缺失 = 全部可用。
3. **加载时机**：`qa_service` 解析 session → `load_mcp_tools_by_names` → 传入各 Agent；失败时记录 warning 并以空工具继续（或对 FAULT 保留现有错误体验——实现选「单 server 失败跳过并日志」）。
4. **Skills 过滤**：在挂载 `SkillsMiddleware` 前，按 `enabled_skills` 过滤可见包（物理目录仍挂载；middleware/prompt 只索引勾选包）。若 deepagents 不支持过滤，则用自定义 listing 包装或 prompt 白名单——优先 middleware sources 子集目录（临时 symlink/过滤 backend 过重），v1 用 **prompt 白名单 + SkillsMiddleware 全量挂载但 system 指引只使用 enabled**；若过弱再加强。更稳：扫描 skill 根，仅把启用的包路径传给 SkillsMiddleware（若 API 要 directory list，传过滤后的 source 根不可行）。**选定**：扩展 `SKILL_SOURCES` 解析为包级路径列表，仅 enabled 包进入 `SkillsMiddleware(sources=...)`。
5. **前端性能**：菜单打开只打 `GET /api/mcp/servers` 与 skills tree 摘要（可缓存到 Pinia，TTL 或会话级）；Models 复用现有 catalog。
6. **安全**：用户 MCP 禁止 `stdio`/`command`；URL 须 http(s)；密钥可放 header 字段但 API 响应脱敏。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| COMMON 挂 MCP 扩大工具面与延迟 | 默认不勾选；连接超时包装为 ToolNetworkError |
| 每轮 `get_tools()` 慢 | 可后续加进程内短 TTL 缓存（本变更可加 60s 缓存按 server 集合） |
| 用户 MCP 指向内网 SSRF | 文档提示；后续可加 URL allowlist（本变更不做） |
| Skills 过滤与 middleware API 不匹配 | 包级 sources；单测覆盖 |

## Migration

- 现有 FAULT 会话无 `mcp_servers` → 行为不变（profile 回退）。
- 平台 `mcp.json` 不变；用户配置新建空文件按需创建。
