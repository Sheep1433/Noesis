# SSE 流式数据架构

> 状态：Current
> OpenSpec：`platform-chat`、`agent-run-delivery`、`agent-tool-failure-handling`

## 1. 边界

前端只消费 Noesis SSE，不直接处理 LangGraph 原始事件。网页发送先创建持久化 run，再单独订阅事件；浏览器连接不持有 producer 生命周期。

```text
POST /api/chat/runs → RunService → RunManager → QaService producer
                                      └─ sequenced event buffer
                                             ├─ PersistSink → PostgreSQL snapshot
                                             ├─ SseDelivery → Browser
                                             └─ ChannelDelivery → Telegram / other channel
```

浏览器连接不是消息落库的权威。客户端断开后，producer 与检查点继续运行；刷新后由 run snapshot 和 sequence 恢复。消息通道使用同一个 `RunManager` 注册 run，并在内部通过 headless `RunOrchestrator` 映射事件，不依赖浏览器 SSE。

Web 路径只有一个持久化责任方：`RunService` 在同一事务中预建 user、assistant 骨架与 run，随后由 PersistSink 更新 checkpoint 和终态。`QaService` 只负责执行 Agent 并产生事件，不创建 Web 消息、不维护 Web active stream，也不处理 Web stop。Telegram、飞书等无浏览器通道可以使用共享的 channel persistence helper，但不得回流为第二套 Web 写入路径。

PersistSink、每个 SseDelivery 和 ChannelDelivery 都使用独立的有界 subscriber queue。RunManager 发布事件时不等待各 Delivery 完成；某个 handler 写入失败只注销该 subscriber 并记录 `delivery_failures`，不会取消 producer、其它 subscriber 或改写 run 终态。PersistSink 在 producer 最终持久化前完成已入队事件的消费，SSE 断连只释放当前浏览器队列。

## 2. 事件

当前网页流除 `run-snapshot`、`run-status` 外，还使用以下事件族：

- `reasoning-start` / `reasoning-delta` / `reasoning-end`
- `text-start` / `text-delta` / `text-end`
- `tool-call-start` / `tool-input-available` / `tool-output-available`
- `retrieval-results-available`
- `text-annotation-added`
- `usage-update` / `context-update` / `token-details`
- `hitl-required`
- `error` / `finish-step` / `finish` / `[DONE]`

业务字段使用现行协议约定。新增或修改事件时，必须同时更新 Bridge、前端解析、golden tests 和 `platform-chat` spec。

## 3. assistant 持久化

同一轮 run 对应一行 assistant，`message_id` 等于 `assistant_message_id`。user message、assistant 骨架和 run 在一个事务中创建：

```text
streaming skeleton
  → optional context checkpoint
  → exactly one terminal update
```

终态互斥：`completed`、`partial`、`error`。HITL pending 保持 `streaming`，resume 继续写同一行。流式过程中禁止按 token 更新正文。

`content.parts` 是历史与实时 UI 的共同结构：

- `text`
- `reasoning`
- `tool`
- `retrieval`：本轮知识库或 Web 检索工具返回的候选 evidence；不等价于正文已引用来源

顶层 `text` part 可带 `annotations[]`。知识库 citation 使用所属 text part 的 Unicode code point 左闭右开 offset。正文仍由 `text-delta` 交付，引用由独立的 `text-annotation-added` patch 交付，marker 不进入正文。

检索完成后的顺序为：

```text
tool-output-available
  → retrieval-results-available
  → text-delta
  → text-end
  → optional terminal text-annotation-added
  → finish
```

支持 completed segment streaming 的 provider 可以在 `text-end` 前交付 annotation。只能终态返回 typed answer 的 provider 可以在 `text-end` 后交付，但必须早于 `finish` 和终态持久化。只有通过配置门禁的模型才为 COMMON_QA 和 SUPER_AGENT_QA 主 Agent 启用 typed binding；其它模型只交付普通正文与 retrieval results，不推断 citation。

`RunProjection` 必须消费 retrieval 与 annotation WireFrame，并写入自己的 authoritative builder。Bridge builder 只负责把 LangGraph 事件转换为实时协议，不能作为 durable snapshot 的唯一来源。`text-delta.part_id` 与 `text-annotation-added.text_part_id` 必须一致，否则投影应拒绝 annotation 并记录结构校验失败。

## 4. 工具结果

工具 part 使用三层语义：`status` 表示工具调用是否抛异常，`outcome` 表示成功返回后的执行结果，`state` 是 UI、snapshot 和历史恢复的权威生命周期。详细状态机见 [工具生命周期与失败处理](../../engineering/agents/tool-lifecycle-and-failures.md)。

- 非终态：`running`、`approval_pending`。
- 终态：`succeeded`、`failed`、`timed_out`、`rejected`、`cancelled`。
- 调用错误：`status=error`，带脱敏错误和 `errorCategory`；超时映射为 `state=timed_out`。
- 进程正常返回：`status=success`，保留 `exit_code/timed_out/truncated/outcome`；非零退出为 `outcome=command_failed + state=failed`。
- 终态 Run 落库前统一 reconcile，禁止保存 `running/approval_pending` 工具。

## 5. 消息顺序与会话标题

`t_chat_session.next_message_sequence` 是会话内序号分配器，所有消息入口在短事务内锁定会话行并分配 `t_chat_message.message_sequence`。历史 API 只按该字段排序和分页；`created_at` 不参与因果顺序判定。同一 Run 的 user 和 assistant 连续分配，user 始终在前。

首轮 Run 创建时，如果会话仍为“新对话”，服务端在 user、assistant 骨架与 run 的同一事务内用首条非空用户文本设置标题。创建响应立即返回最终 `session_title`；手动标题不会被覆盖。

## 6. 停止、断连与 HITL

- 用户停止通过 `POST /api/chat/runs/{run_id}/stop` 取消，PersistSink 写 `partial`。
- 客户端断连只移除该 subscriber，不取消 producer 或其它 subscriber。
- HITL resume 必须继续同一 run/message 身份，禁止新建第二条 assistant。
- HITL resume 同时恢复 retrieval manifest 的 run salt、evidence namespace 和已登记结果；citation 按 `citation_id` 去重。
- HITL resume 或 snapshot 重放遇到相同 `tool_call_id` 时更新原工具块，禁止重复追加。
- 审批面板按 session/run 绑定；切换会话只显示当前 session 的 pending 审批，多会话互不覆盖。
- `[DONE]` 是整个 Run 的客户端传输结束标记，不是服务端落库条件；HITL 暂停只结束
  当前 LangGraph 执行分段，不得向 Run subscriber 投递 `[DONE]`。

## 7. 保活与部署

SSE 注释保活不推进 sequence，也不落库。反向代理 read timeout 必须大于保活间隔。连接写失败只结束当前订阅，不改写 run 终态。

## 8. 当前限制

- live run owner 在单个后端进程内；生产必须单实例运行，或为 run API 配置 owner sticky routing。
- 进程重启只把悬空 run 收口为 `interrupted/server_restart`，不会重放模型或工具。
- 网页 subscriber queue 与事件缓存有事件数、字节数双上限；慢连接通过 snapshot 恢复。
- 模型临时错误只在尚未输出正文、尚未开始工具/HITL 时自动 retry；每次重试递增 `attempt_id`，旧 attempt 迟到事件会被丢弃。Channel outbound 使用进程内有界队列，但不是 durable spool；进程退出时未完成投递记录为 lost，不会自动续发。
- 前后端必须同步发布；旧 `/api/chat/sessions/stream`、session stop/resume 接口已删除。
- 当前 `LcEventMapper` 仍通过 `LangGraphSseBridge` 生成 SSE 文本后再解析为 typed `RunEvent`。这段内部序列化往返增加了字段漂移和重复解析风险；后续应以独立变更改为 raw event 直接映射 typed event，SSE 仅保留在 Delivery 边界。在完成该变更前，不允许再增加另一套 Bridge 或 Mapper。

## 9. 代码入口

- Bridge：`backend/noesis_server/domain/chat/streaming/langgraph_sse.py`
- Run lifecycle：`backend/noesis_server/domain/chat/runs/`
- Run Service：`backend/noesis_server/services/run_service.py`
- Run API：`backend/noesis_server/api/chat_api.py`
- QA 编排：`backend/noesis_server/services/qa/`
- 前端解析：`frontend/src/views/chat/useSSEStream.ts`
- parts：`frontend/src/views/chat/messageParts.ts`
- Tool state：`backend/noesis_server/domain/chat/tool_state.py`
