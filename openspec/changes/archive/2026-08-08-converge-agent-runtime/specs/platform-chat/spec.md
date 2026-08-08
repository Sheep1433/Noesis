## ADDED Requirements

### Requirement: SSE SHALL 表达统一 Agent Stop Reason

RunEvent 到 `/api/chat` SSE 与 assistant 终态映射 SHALL 支持稳定的 Agent stop reason，至少覆盖 `context_exhausted`、`length_stop`、`safety_stop`、`partial_output`、`empty_after_tools`、`tool_loop_limit`、`tool_call_limit`、`subagent_concurrency_limit`、`subagent_total_limit` 与 `subagent_depth_limit`。新增 reason SHALL 作为兼容字段出现在 `finish`、`error` 或现行终态事件中；旧客户端忽略该字段时 SHALL 仍能完成消息收尾。

#### Scenario: length stop 保留正文

- **WHEN** 模型因长度限制结束且已经产生正文
- **THEN** assistant SHALL 保存已有正文并进入 partial 或现行等价非 completed 终态
- **AND** SSE 终态 SHALL 携带 `length_stop`

#### Scenario: governor 停止仍可展示

- **WHEN** Run Governor 因工具循环达到硬限制而停止
- **THEN** assistant SHALL 保留停止前的 reasoning、tool parts 与正文
- **AND** 终态 SHALL 携带 `tool_loop_limit`

