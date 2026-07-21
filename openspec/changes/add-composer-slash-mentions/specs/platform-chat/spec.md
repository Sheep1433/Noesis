## ADDED Requirements

### Requirement: 问答请求 MAY 携带 mentions 并由服务端校验注入

系统 SHALL 允许流式/非流式问答请求体携带可选字段 `mentions`（结构化数组）。服务端 SHALL 校验：

- 每项 `type` 属于允许集合（`skill` | `file` | `folder` | `subagent`）；
- `file` / `folder` 路径属于当前认证用户且属于当前 `session_id` 可访问范围（拒绝对 `../` 或其它用户/会话路径穿越）；
- `skill` id 属于当前用户可见的平台或用户 Skills；
- `subagent` id 属于当前 `qa_type` 已注册的子 Agent 名称。

校验失败时 SHALL 返回 4xx 业务错误（不得静默丢弃后当作无 mentions 成功执行，除非显式文档化为降级——首期 SHALL 失败）。

通过校验后，`qa_service`（或等价编排）SHALL 在调用 Agent 前将 mentions 解析为 prompt 附加块和/或工具约束（如收窄本轮 `enabled_skills`），**SHALL NOT** 为此新增 SSE 事件类型。

用户消息持久化时，若请求含 `mentions`，SHALL 写入 `extra.mentions`。

#### Scenario: 合法 file mention 注入

- **WHEN** 已认证用户对本人会话提交含合法 `type=file` 的 `mentions` 与非空 query
- **THEN** 系统 SHALL 在 Agent 运行前注入路径引用（或阈值内短文本），并将会话用户消息 `extra.mentions` 保存该数组

#### Scenario: 路径穿越拒绝

- **WHEN** `mentions` 中 `file.path` 指向其它会话或用户目录外路径
- **THEN** 系统 SHALL 返回 4xx，**SHALL NOT** 启动该轮 Agent 流式生成

#### Scenario: 省略 mentions

- **WHEN** 请求体不含 `mentions`
- **THEN** 系统 SHALL 与变更前行为一致地路由并生成回答
