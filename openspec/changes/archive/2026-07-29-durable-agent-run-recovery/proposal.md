## Why

当前网页 SSE 连接仍拥有 Agent producer 生命周期：刷新、关闭页面或短暂断网会取消 run，并把 assistant 收口为 `partial`。这与既有「浏览器连接不是执行权威」的设计目标不一致，也使模型重试、事件丢帧、后端重启等场景缺少稳定、可解释的用户状态。

本变更将 run、Delivery 与浏览器连接彻底分离，先保证同一后端进程内的断线续看和多订阅，并为错误重试、重启收口及后续多平台投递建立统一契约。

## What Changes

- 新增可查询、可订阅、可取消的 Agent run 生命周期；每轮使用稳定 `run_id` 与 `assistant_message_id`。
- 将 Agent producer 从 `POST /api/chat/sessions/stream` 的请求 generator 中分离；SSE 客户端断开只移除该订阅，不取消 run。
- 为 RunEvent 增加单调递增 `sequence`，支持原子「当前快照 + 后续订阅」，客户端检测事件缺口后从权威快照重同步。
- 调整网页发送流程：刷新/关闭页面不再隐式调用 stop；只有用户明确停止才取消 run。
- 增加 run 查询与重新订阅 API，并移除旧 `/api/chat/sessions/stream`；浏览器继续使用 SSE，不切换 WebSocket。
- 为创建请求增加幂等键与事务边界，响应丢失后的客户端重试返回原 run，不重复插入消息或启动 producer。
- 将临时模型/传输异常建模为 `retrying` / `will_retry=true`，与最终 `failed` 分离；前端显示重试过程与最终原因。
- 规定模型流重试的 attempt 投影边界，避免把不同 attempt 的正文或工具调用无条件拼接。
- 规定 PersistSink、SSE 与 ChannelDelivery 的队列背压、慢消费者隔离和终态可靠写入规则。
- 补齐工具 timeout、取消确认、迟到结果和结果未知语义，以及 run/event/HITL 的资源上限与回收策略。
- 后端启动时将无活跃执行所有者的悬空 run 收口为 `interrupted`，不得自动重放已可能执行工具副作用的 run。
- 统一网页、PersistSink 与 ChannelDelivery 对同一 run 的订阅和终态观察；通道不依赖浏览器连接。
- **BREAKING**：移除“浏览器 `beforeunload` 等同用户停止”的产品语义；刷新将不再终止生成。

### Non-Goals

- 本变更不承诺后端进程崩溃后从任意 LangGraph 节点自动续跑。
- 本变更不自动重放包含外部副作用、工具调用、HITL 或子 Agent 的中断 run。
- 本变更不以 WebSocket 替换 SSE，也不引入 Redis、Celery、Temporal 等分布式执行依赖。
- 本变更不重做 Telegram durable ingress/outbound spool；仅保证通道订阅不受网页连接影响。

## Capabilities

### New Capabilities

- `agent-run-recovery`: 规定 run 身份、状态机、sequence、原子快照订阅、同进程断线恢复、重启后悬空 run 收口与安全边界。

### Modified Capabilities

- `agent-delivery`: RunEvent 总线从请求内 Fan-out 调整为 run 生命周期拥有的多订阅投递，明确 Delivery 断开不得取消 producer，并增加临时错误事件语义。
- `platform-chat`: 调整 `/api/chat` 网页发送、停止、run 查询与 SSE 重订阅行为；刷新不再 stop，UI 按权威 run 状态恢复。

## Impact

- 后端：`backend/noesis_server/domain/chat/delivery/`、`backend/noesis_server/services/qa/`、`backend/noesis_server/api/chat_api.py`、聊天消息/run 持久化模型与 Alembic migration。
- 前端：`frontend/src/views/chat/useSSEStream.ts`、`frontend/src/views/chat.vue`、聊天 API/Store 与历史初始化逻辑。
- API：在 `/api/chat` 下新增 run 创建、查询、订阅与取消端点，并删除 `POST /api/chat/sessions/stream`。
- 数据：新增 run 状态或等价持久化记录；消息 `streaming/completed/partial/error` 历史语义需要兼容，并新增 `interrupted` 或等价结束原因。
- 测试：覆盖刷新/断线、多订阅、sequence gap、原子快照订阅、临时重试、明确停止、后端重启悬空 run、HITL 和通道无网页订阅等场景。
- 运维：增加 run 数量、队列溢出、检查点失败、重连频率、取消延迟和资源回收指标；P0 部署限制为单 active backend 或可保证回到 owner 的 sticky routing。
