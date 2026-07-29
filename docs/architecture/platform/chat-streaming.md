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

PersistSink、每个 SseDelivery 和 ChannelDelivery 都使用独立的有界 subscriber queue。RunManager 发布事件时不等待各 Delivery 完成；某个 handler 写入失败只注销该 subscriber 并记录 `delivery_failures`，不会取消 producer、其它 subscriber 或改写 run 终态。PersistSink 在 producer 最终持久化前完成已入队事件的消费，SSE 断连只释放当前浏览器队列。

## 2. 事件

当前网页流除 `run-snapshot`、`run-status` 外，还使用以下事件族：

- `reasoning-start` / `reasoning-delta` / `reasoning-end`
- `text-start` / `text-delta` / `text-end`
- `tool-call-start` / `tool-input-available` / `tool-output-available`
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
- 由已批准 OpenSpec change 新增的其它 versioned part

## 4. 工具结果

`status` 表示工具调用是否抛异常；`outcome` 表示成功返回后的执行结果。详细分类见 `agent-tool-failure-handling`。

- 调用错误：`status=error`，带脱敏错误和 `errorCategory`，不带 `outcome`。
- 正常返回：`status=success`，按工具类型解析 `ok`、`empty`、`command_failed`、`timed_out`。

## 5. 停止、断连与 HITL

- 用户停止通过 `POST /api/chat/runs/{run_id}/stop` 取消，PersistSink 写 `partial`。
- 客户端断连只移除该 subscriber，不取消 producer 或其它 subscriber。
- HITL resume 必须继续同一 run/message 身份，禁止新建第二条 assistant。
- HITL resume 或 snapshot 重放遇到相同 `tool_call_id` 时更新原工具块，禁止重复追加。
- 审批面板按 session/run 绑定；切换会话只显示当前 session 的 pending 审批，多会话互不覆盖。
- `[DONE]` 是整个 Run 的客户端传输结束标记，不是服务端落库条件；HITL 暂停只结束
  当前 LangGraph 执行分段，不得向 Run subscriber 投递 `[DONE]`。

## 6. 保活与部署

SSE 注释保活不推进 sequence，也不落库。反向代理 read timeout 必须大于保活间隔。连接写失败只结束当前订阅，不改写 run 终态。

## 7. 当前限制

- live run owner 在单个后端进程内；生产必须单实例运行，或为 run API 配置 owner sticky routing。
- 进程重启只把悬空 run 收口为 `interrupted/server_restart`，不会重放模型或工具。
- 网页 subscriber queue 与事件缓存有事件数、字节数双上限；慢连接通过 snapshot 恢复。
- 模型临时错误只在尚未输出正文、尚未开始工具/HITL 时自动 retry；每次重试递增 `attempt_id`，旧 attempt 迟到事件会被丢弃。Channel outbound 使用进程内有界队列，但不是 durable spool；进程退出时未完成投递记录为 lost，不会自动续发。
- 前后端必须同步发布；旧 `/api/chat/sessions/stream`、session stop/resume 接口已删除。

## 8. 代码入口

- Bridge：`backend/noesis_server/domain/chat/streaming/langgraph_sse.py`
- Run lifecycle：`backend/noesis_server/domain/chat/runs/`
- Run Service：`backend/noesis_server/services/run_service.py`
- Run API：`backend/noesis_server/api/chat_api.py`
- QA 编排：`backend/noesis_server/services/qa/`
- 前端解析：`frontend/src/views/chat/useSSEStream.ts`
- parts：`frontend/src/views/chat/messageParts.ts`
