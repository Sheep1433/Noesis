## ADDED Requirements

### Requirement: Composer SHALL 提供 Models / Skills / MCP 会话级目录

聊天输入区 `+` 菜单 SHALL 提供层级入口：附件（既有）、Models、Skills、MCP Servers；在 `COMMON_QA` 下 SHALL 继续提供知识库范围选择。菜单打开时 SHALL 仅请求元数据列表 API，SHALL NOT 为列举而连接 MCP server 或调用 `get_tools()`。

#### Scenario: 打开 + 菜单加载目录

- **WHEN** 用户打开 Composer `+` 菜单
- **THEN** 前端 SHALL 拉取模型目录、Skills 包摘要、MCP server 目录（可命中会话级缓存）
- **AND** SHALL NOT 触发 MCP 连通探测或工具枚举

### Requirement: 会话 extra SHALL 持久化 mcp_servers 与 enabled_skills

系统 SHALL 在会话 `extra` 中支持：

- `mcp_servers: string[]` — 本会话启用的 MCP server id
- `enabled_skills: string[]` — 本会话启用的 skill 包名

客户端经既有 `ensureSession` / `merge_session_extra` 写入；发问时 `qa_service` SHALL 以会话存储为准解析（请求体可显式覆盖并写回，与 `model_id` 同模式）。

#### Scenario: 勾选 MCP 后刷新仍保留

- **WHEN** 用户在会话中勾选 server `fault_ops` 并刷新页面
- **THEN** Composer MCP 子菜单 SHALL 显示 `fault_ops` 为已启用
- **AND** 后续发问 SHALL 按该勾选加载工具

### Requirement: mcp_servers 缺省回退规则 SHALL 按 qa_type 区分

当会话 `extra` **不包含**键 `mcp_servers` 时，系统 SHALL 按 `qa_type` 区分回退：

- `FAULT_OPERATION_QA` SHALL 回退为平台 profile `fault_operation` 的 server 列表
- 其它 `qa_type` SHALL 视为未启用任何 MCP（空列表）

当键存在（含空数组）时，SHALL 严格按该列表加载，SHALL NOT 再套用 profile 回退。

#### Scenario: 旧故障运维会话无 mcp_servers 键

- **WHEN** 既有 `FAULT_OPERATION_QA` 会话的 `extra` 无 `mcp_servers`
- **THEN** 发问 SHALL 仍加载平台 `fault_operation` profile 对应 server

#### Scenario: 显式清空 MCP

- **WHEN** 会话 `extra.mcp_servers` 为 `[]`
- **THEN** 发问 SHALL NOT 挂载任何 MCP 工具（含故障运维）
