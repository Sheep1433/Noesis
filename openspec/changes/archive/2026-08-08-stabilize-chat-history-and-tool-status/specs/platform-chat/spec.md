## ADDED Requirements

### Requirement: 新 Run SHALL 一次性设置默认会话标题

`POST /api/chat` 下的 Run 创建入口 SHALL 在创建首条 user 消息、assistant 骨架与 run 的同一事务中，仅当会话标题仍精确等于默认值“新对话”时，根据首条非空用户可见文本设置规范化标题。创建响应 SHALL 返回服务端最终 `session_title`；前端 SHALL 立即更新当前页和会话列表，并在刷新后以会话 API 的标题为准。

#### Scenario: 首条消息设置标题

- **WHEN** 标题为“新对话”的会话成功创建首个 Run，用户文本为“你好”
- **THEN** 会话标题 SHALL 在同一事务中更新为“你好”
- **AND** 创建 Run 响应 SHALL 返回该标题

#### Scenario: 已改名会话不被覆盖

- **WHEN** 会话已有非默认标题且用户开始新一轮 Run
- **THEN** 服务端 SHALL 保留原标题
- **AND** 前端 SHALL 使用响应中的原标题，不得根据本轮问题覆盖

#### Scenario: Run 创建回滚

- **WHEN** user、assistant 或 run 任一写入失败导致事务回滚
- **THEN** 自动标题更新 SHALL 一并回滚
- **AND** 不得出现有标题但没有首轮消息的半成品会话

### Requirement: 会话消息 SHALL 使用服务端确定性序号

每条 `t_chat_message` SHALL 具有会话内唯一、严格递增的 `message_sequence`。所有消息写入口 SHALL 在锁定会话序号分配器的短事务中分配序号；同一 Run 的 user 与 assistant SHALL 连续分配，且 user 序号小于 assistant。`created_at` SHALL 仅表达时间，不再作为历史顺序权威。

#### Scenario: 同毫秒创建 user 与 assistant

- **WHEN** RunService 在同一毫秒预创建 user 和 assistant
- **THEN** user.message_sequence SHALL 等于 N
- **AND** assistant.message_sequence SHALL 等于 N+1

#### Scenario: 同会话并发写入

- **WHEN** 两个合法写入入口并发向同一会话保存消息
- **THEN** 系统 SHALL 分配不同且连续的 sequence
- **AND** 唯一约束 SHALL NOT 因竞态产生随机失败

### Requirement: 历史 API SHALL 按消息序号分页与返回

`GET /api/chat/sessions/{id}/messages` SHALL 按 `message_sequence ASC` 返回消息，并在每条消息中包含该字段。`before_id` cursor SHALL 解析目标消息的 sequence 并查询更小序号，不能继续使用 `created_at` 比较。当前 Web 前后端 SHALL 同版本升级；新响应缺少 sequence SHALL 作为协议错误处理，不保留第二套时间排序。

#### Scenario: 刷新后保持 user → assistant

- **WHEN** 用户完成一轮问答后刷新并重新加载历史
- **THEN** API SHALL 先返回该轮 user，再返回对应 assistant
- **AND** 前端 SHALL 按 sequence 稳定展示该顺序

#### Scenario: cursor 遇到相同 created_at

- **WHEN** cursor 消息与更早消息具有相同 created_at
- **THEN** API SHALL 仍依据 message_sequence 正确返回全部更早消息
- **AND** 不得漏掉同毫秒消息

### Requirement: 历史迁移 SHALL 建立可审计的稳定顺序

数据库迁移 SHALL 为已有消息一次性回填 `message_sequence`，保证会话内唯一，并保证具有 parent user 的 assistant 排在 parent 之后。迁移 SHALL 校验每会话数量、唯一性、parent 顺序与 `next_message_sequence=max+1`，不满足时 SHALL 中止而不是带缺陷上线。

#### Scenario: 旧 user 与 assistant 时间相同

- **WHEN** 旧数据中 assistant.parent_id 指向 user 且二者 created_at 相同
- **THEN** 回填后 assistant.message_sequence SHALL 大于 user.message_sequence

#### Scenario: 迁移校验失败

- **WHEN** 回填产生重复 sequence 或 parent 顺序倒置
- **THEN** 迁移 SHALL 失败并输出可定位的会话/消息标识
- **AND** NOT NULL 与唯一约束 SHALL NOT 在错误数据上提交

### Requirement: 工具卡片 SHALL 按权威 state 展示和恢复

chat 页 SHALL 按服务端 tool `state` 显示“正在执行、等待确认、已完成、执行失败、执行超时、已拒绝、已停止”等互斥状态。HITL 等待时流订阅保持 active 不得禁用授权控件；Run snapshot 或历史加载 SHALL replace 同一 assistant 的客户端 parts，纠正刷新前的旧状态。

#### Scenario: 日志已失败而旧界面仍运行中

- **WHEN** 服务端权威历史中某工具为 `state=failed`，客户端刷新前缓存为 `running`
- **THEN** 刷新恢复后工具卡片 SHALL 显示失败
- **AND** SHALL NOT 保留旧的“运行中”标签

#### Scenario: HITL 等待授权

- **WHEN** execute part 为 `approval_pending` 且 SSE subscription 仍 active
- **THEN** UI SHALL 显示“等待确认”
- **AND** 允许一次/本会话允许/拒绝控件 SHALL 可点击，除非授权结果正在提交

### Requirement: 工具失败且无回答时 SHALL 显示阻断态

前端 SHALL 从 assistant 结构化 tool states 派生无回答阻断态，不依赖模型自行判断。存在任意未成功工具，但在最后一个工具块之后仍有回答正文时 SHALL NOT 额外提示结果可能不完整；只有工具调用前的过程文本时仍 SHALL 视为没有最终回答，并显示阻断态与重试入口。单个工具详情 SHALL 保持可展开。

#### Scenario: 回答包含多个失败工具

- **WHEN** assistant 中有多个 failed/timed_out 工具且仍有正文
- **THEN** 回答级完整性提示 SHALL NOT 显示
- **AND** 每个失败工具卡片 SHALL 保留自己的状态和详情

#### Scenario: 工具失败且无回答

- **WHEN** 关键工具失败并且 assistant 没有可见正文
- **THEN** UI SHALL 告知本轮未完成
- **AND** SHALL 提供重新执行本轮的操作
