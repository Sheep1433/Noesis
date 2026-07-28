## MODIFIED Requirements

### Requirement: 流式问答与 SSE 核心契约

浏览器实时响应 SHALL 使用 `/api/chat` 下的 run 创建与 SSE 订阅端点。系统 SHALL 提供独立的 run 创建、状态查询、SSE 订阅和停止能力，并 SHALL 删除 `POST /api/chat/sessions/stream`。浏览器主实时通道仍为 SSE，不要求 WebSocket。

事件类型至少覆盖：`run-snapshot`、`run-status`、`reasoning-*`、`text-*`、`tool-call-*` / `tool-input-*`、`tool-output-available`、`usage-update`、`context-update`、`hitl-required`、`error`、`finish`、`[DONE]`。业务事件 SHALL 携带 `run_id` 与 sequence；keepalive 注释帧 SHALL 仅由传输层注入。

#### Scenario: 创建后独立订阅
- **WHEN** 已认证用户成功创建 run
- **THEN** 创建响应 SHALL 返回 `run_id` 与 `assistant_message_id`
- **AND** 客户端 SHALL 能使用独立 SSE 端点订阅该 run

#### Scenario: 文本增量兼容
- **WHEN** run 产生文本增量且客户端订阅 SSE
- **THEN** 客户端 SHALL 收到兼容的 `text-delta` 帧

### Requirement: SSE 传输稳定性

流式路径 SHALL 配置合理的代理/应用超时；服务端 MAY 按可配置间隔发送 SSE 注释保活帧。连接类写入失败 SHALL 可观测，并 SHALL 只关闭对应 subscription，不得笼统降级为 run 业务错误或取消 producer。

客户端 SHALL 检查业务事件 sequence。发现 sequence gap、网络异常或未收到终态的 EOF 时，客户端 SHALL 查询权威 run 状态并重新订阅，SHALL NOT 把该 EOF 当作成功完成。

#### Scenario: 保活不污染总线
- **WHEN** SseDelivery 注入 keepalive
- **THEN** PersistSink / ChannelDelivery SHALL NOT 将其当作 RunEvent 业务事件

#### Scenario: 无终态 EOF 触发恢复
- **WHEN** 浏览器流在未收到终态事件时结束
- **THEN** chat 页 SHALL 保持 run 未完成语义并查询/重订阅
- **AND** SHALL NOT 调用成功收尾回调

### Requirement: 流式 assistant 消息 SHALL 按骨架—检查点—终态单次落库

系统 SHALL 保证同一 run 对应 DB 一行 assistant（`message_id = assistant_message_id`）：骨架（`streaming`）→ 可选节流 parts/context 检查点 → 终态 UPDATE。终态互斥：`completed` / `error` / `partial`。

系统 SHALL NOT 按 token 增量 UPDATE assistant 正文；完整工具结束、阶段结束、HITL pending 或可配置节流条件满足时 MAY 更新同一行 parts 检查点。落库 SHALL NOT 依赖浏览器 SSE 仍存活。

HITL 暂停时 assistant SHALL 保持 `streaming`；resume 续写同一 `run_id` 与 `assistant_message_id`。服务重启导致无法继续时，run SHALL 为 `interrupted`，assistant SHALL 为 `partial`，`finish_reason=server_restart`。

#### Scenario: 无浏览器仍终态
- **WHEN** run 所有浏览器订阅均断开后正常完成
- **THEN** assistant SHALL 更新为 completed

#### Scenario: 服务重启保留检查点
- **WHEN** 后端启动恢复发现悬空 run 且 assistant 已有检查点
- **THEN** assistant SHALL 保留已有 parts 并更新为 partial
- **AND** SHALL 标记 `finish_reason=server_restart`

#### Scenario: 用户停止 → partial
- **WHEN** 用户明确触发 stop 且 run 尚未终态
- **THEN** assistant SHALL 为 partial
- **AND** SHALL 带 `finish_reason=stopped`

### Requirement: 停止生成

系统 SHALL 提供按 `run_id` 停止当前执行的 API；用户停止、浏览器断开、生成失败与服务重启 SHALL 分流。刷新、关闭页面和普通网络断开 SHALL NOT 自动调用 stop。chat 页停止 UI SHALL 等待服务端 run 进入终态，避免本地假完成。

#### Scenario: stop → partial
- **WHEN** run 所有者明确调用 stop 且 run 仍在进行
- **THEN** 服务端 SHALL 中止 Agent 并将 assistant 标为 partial

#### Scenario: beforeunload 不停止
- **WHEN** 浏览器在 run 进行中刷新或关闭页面
- **THEN** 客户端 SHALL NOT 因 `beforeunload` 调用 stop
- **AND** 后端 SHALL 允许 run 继续

### Requirement: 流式问答入口 SHALL 经 Run Fan-out 投递

`POST /api/chat/runs`（或本 change 约定的等价创建端点）SHALL 创建由 RunManager 持有的 producer，并注册 PersistSink；独立 SSE 订阅端点 SHALL 通过 RunEvent 总线的 SseDelivery 输出。问答编排 SHALL NOT 在单一 HTTP generator 内同时拥有 producer、落库和客户端生命周期。

旧 `POST /api/chat/sessions/stream` SHALL 被删除，问答编排 SHALL NOT 保留第二条发送路径。

#### Scenario: 新入口快速返回 run 身份
- **WHEN** 已认证用户对 `/api/chat/runs` 发起合法创建请求
- **THEN** 服务端 SHALL 在 run 注册和消息骨架落库后返回 run 身份
- **AND** SHALL NOT 等待 Agent 完成才响应

#### Scenario: 旧入口不可用
- **WHEN** 客户端请求 `/api/chat/sessions/stream`
- **THEN** 系统 SHALL 返回 404 或路由不存在的等价结果
- **AND** SHALL NOT 通过隐藏包装创建 run

### Requirement: 停止生成 SHALL 走统一 Run 生命周期

停止生成接口 SHALL 通过统一 RunManager/cancel 入口通知目标 `run_id`，使 PersistSink 与仍订阅的 Delivery 观察到一致的中止语义。停止 SHALL 鉴权且幂等；系统 SHALL NOT 使用与 run 身份无关的 session 全局布尔量误停其它执行。

#### Scenario: 停止后 partial 落库
- **WHEN** 用户对所属 active run 调用停止接口
- **THEN** assistant SHALL 进入 partial
- **AND** 仍在线 Delivery SHALL 收到一致终态

## ADDED Requirements

### Requirement: chat 页 SHALL 从权威 run snapshot 恢复

chat 页 SHALL 保存当前 run_id、assistant_message_id 与 last_sequence。页面重新加载或连接恢复时，若历史/session 信息表明存在 active run，客户端 SHALL 查询 run 并重新订阅；收到 `run-snapshot` 时 SHALL 按 replace 语义重建该 assistant parts，而不是重复 append。

#### Scenario: 刷新后继续显示增量
- **WHEN** 用户在 run 进行中刷新并重新进入同一 session
- **THEN** chat 页 SHALL 加载当前 snapshot 并订阅后续事件
- **AND** 用户 SHALL 继续看到同一 assistant_message_id 的生成过程

#### Scenario: 重复事件按 sequence 忽略
- **WHEN** 重订阅补发了 sequence 小于等于客户端 last_sequence 的事件
- **THEN** 客户端 SHALL 忽略该重复事件

#### Scenario: HITL 续跑更新原工具块
- **WHEN** 刷新恢复或批准续跑再次产生相同 `tool_call_id` 的工具输入与结果事件
- **THEN** 服务端 snapshot 与 chat 页 SHALL 更新原工具块的输入、HITL 和执行状态
- **AND** SHALL NOT 追加第二个工具块

#### Scenario: 多会话审批状态隔离
- **WHEN** 一个或多个 session 分别处于 `hitl_pending` 且用户切换当前会话
- **THEN** chat 页 SHALL 只展示当前 session 对应的审批面板
- **AND** 切走 SHALL 仅隐藏该面板而不丢弃其 pending 状态
- **AND** 切回后 SHALL 从 session 本地状态或权威 run snapshot 恢复该面板
- **AND** 提交 SHALL 使用该审批自身绑定的 `session_id`、`run_id` 与 `interrupt_id`

#### Scenario: 审批后等待继续输出
- **WHEN** 用户提交审批且权威 run snapshot 从 `hitl_pending` 变为 `queued`、`running` 或 `retrying`
- **THEN** chat 页 SHALL 立即显示“正在继续生成”或等价状态
- **AND** SHALL NOT 等到下一段正文 token 到达后才恢复生成提示

#### Scenario: 切换会话隔离旧 Run 订阅
- **WHEN** 用户从存在 active Run 的会话切换到另一会话或新对话
- **THEN** 客户端 SHALL 释放旧会话的本地 subscription 但 SHALL NOT 停止服务端 Run
- **AND** 旧 subscription 的迟到 snapshot 或 delta SHALL NOT 修改新会话界面
- **AND** HITL 审批 SHALL 严格使用当前 session 对应的 `run_id`

### Requirement: chat 页 SHALL 区分临时重试与终态失败

客户端收到 `run-status` 且 `will_retry=true` 时 SHALL 保持 loading 并显示受控重试状态；恢复到 running 后 SHALL 清除临时提示。只有 run `error` 或终态 error 事件 SHALL 触发最终失败 UI。

#### Scenario: LLM 重试期间用户可感知
- **WHEN** 模型流断开且服务端正在自动重试
- **THEN** 用户 SHALL 看到“正在重试”或等价状态
- **AND** 当前已生成内容 SHALL 保留

#### Scenario: 重试耗尽显示最终错误
- **WHEN** 所有自动重试均失败
- **THEN** chat 页 SHALL 显示脱敏终态错误
- **AND** SHALL 结束该 run 的 loading

### Requirement: chat 页 SHALL 对创建和重连实施幂等与限速

chat 页 SHALL 为一次用户发送生成稳定 `client_request_id`，在创建响应未知时使用同一身份重试，不得因网络错误生成新的幂等键。SSE 重连 SHALL 使用指数退避与随机抖动，并限制连续自动重连频率；页面重新可见或网络恢复时 MAY 再次查询权威 run 状态。

达到自动重连上限时，客户端 SHALL 保留 run 的非终态语义并提供手动重连，不得伪装 completed。多个标签页 MAY 分别订阅同一 run，单个标签页的通知或失败状态 SHALL NOT 修改服务端 run。

#### Scenario: 创建响应未知后重试
- **WHEN** 创建请求可能已经成功但客户端因网络错误未收到响应
- **THEN** 客户端 SHALL 使用原 client_request_id 重试
- **AND** 服务端返回的仍是原 run

#### Scenario: 服务恢复时避免重连风暴
- **WHEN** SSE 连续失败
- **THEN** 客户端 SHALL 使用带随机抖动的指数退避
- **AND** SHALL NOT 立即无限循环请求 run 与 stream 接口

#### Scenario: 达到自动重连上限
- **WHEN** 客户端达到连续自动重连次数上限且服务端终态未知
- **THEN** 页面 SHALL 显示连接恢复操作并保留已有内容
- **AND** SHALL NOT 将 run 标记为成功或最终失败
