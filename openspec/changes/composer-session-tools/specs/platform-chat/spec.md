## ADDED Requirements

### Requirement: 会话 extra SHALL 支持 mcp_servers 与 enabled_skills

`TChatSession.extra`（JSON）SHALL 允许客户端与服务端读写：

- `mcp_servers`：string 数组
- `enabled_skills`：string 数组

`ChatService.merge_session_extra` SHALL 浅合并这些键；非法类型 SHALL 在写入前规范化为空数组或拒绝。

#### Scenario: merge 保留其它 extra 键

- **WHEN** 已有 `extra.model_id` 且客户端 merge `{ "mcp_servers": ["a"] }`
- **THEN** 结果 SHALL 同时保留 `model_id` 与 `mcp_servers`
