# SSE 流式数据设计

> OpenSpec：`platform-chat`、`agent-run-delivery`、`agent-tool-failure-handling`

## 1. 边界

前端只消费 Noesis SSE，不直接处理 LangGraph 原始事件。Agent 事件由 harness 产生，平台在 `noesis_server.domain.chat.streaming` 转换为 RunEvent，再由 Delivery 分发到网页、持久化和消息通道。

```text
noesis.runtime.stream
  → LangGraphSseBridge / RunEvent
  → EventBus
      ├─ SseDelivery
      ├─ PersistSink
      └─ ChannelDelivery
```

浏览器连接不是消息落库的权威。客户端断开后，PersistSink 仍负责 assistant 终态。

## 2. 事件

当前网页流使用以下事件族：

- `reasoning-start` / `reasoning-delta` / `reasoning-end`
- `text-start` / `text-delta` / `text-end`
- `tool-call-start` / `tool-input-available` / `tool-output-available`
- `usage-update` / `context-update` / `token-details`
- `hitl-required`
- `error` / `finish-step` / `finish` / `[DONE]`

业务字段使用现行协议约定。新增或修改事件时，必须同时更新 Bridge、前端解析、golden tests 和 `platform-chat` spec。

## 3. assistant 持久化

同一轮 run 对应一行 assistant，`message_id` 等于 `assistant_message_id`：

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

- 用户停止通过统一 RunLifecycle 取消，PersistSink 写 `partial`。
- 客户端断连只终止该 Delivery，不应取消所有订阅者。
- HITL resume 必须继续同一 run/message 身份，禁止新建第二条 assistant。
- `[DONE]` 是客户端传输结束标记，不是服务端落库条件。

## 6. 保活与部署

SSE 注释保活不进入 RunEvent 总线，也不落库。反向代理 read timeout 必须大于保活间隔。连接写失败应记录可定位日志，不应改写为普通业务错误。

## 7. 代码入口

- Bridge：`backend/noesis_server/domain/chat/streaming/langgraph_sse.py`
- Run delivery：`backend/noesis_server/domain/chat/delivery/`
- QA 编排：`backend/noesis_server/services/qa/`
- 前端解析：`frontend/src/views/chat/useSSEStream.ts`
- parts：`frontend/src/views/chat/messageParts.ts`
