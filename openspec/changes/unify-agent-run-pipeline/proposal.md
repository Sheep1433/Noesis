# 提案：统一主/子 Agent 运行管道（unify-agent-run-pipeline）

## Why

主 Agent 与后台子 Agent 目前是两套并行实现：主链路为请求作用域的 HTTP/SSE（`langgraph_bridge` 同时承担 LangGraph 事件映射、usage 累计、终态落库与 SSE 帧序列化），子链路为 executor 自建的 `astream(values)` diff 消费循环 + `persist_projection` 落库 + `_publish_task_event` 事件发布。两套管道对同一概念（run、turn、projection、事件、快照）各有实现，导致：

1. **能力单侧生长**：推理预算（ContextVar）只在主链路生效、usage 只在主链路累计落库、子会话历史回放拿不到 token/缓存统计、上下文快照两处各写一份。
2. **前端双轨消费**：`useSSEStream` 与 `SubagentConversationView.applyEvent` 各自解析同一组领域事件的两种传输形态；composer/统计条长期不同步（近期已手工对齐单按钮与待发队列，属补丁式收敛）。
3. **维护成本翻倍**：每个 run 级新能力（usage、快照、续跑、停止语义）都要评估「两条链路各写一遍还是只写一边」，事实上大多只落在主链路。

参照 dsh（deepseek-harness）的架构验证：子 Agent 经 `ctx.agents.create/resume` 与主 Agent 共用同一 agent-loop 与 session 机制，usage/事件/持久化按 session 天然归属，subagent 包只做委托特化（深度预算、lineage、策略 pin）。本变更将 Noesis 收敛到同构形态。

## What Changes

### 后端

- **executor 接入既有 typed RunEvent 管道**：仓库已有领域/传输分离（`RuntimeEventMapper`：raw event → typed RunEvent；`delivery/events.py`：WireFrame 与终态事件类型；`delivery/sse.py`：编码只在 Delivery 边界——`enable-distributed-sse-pubsub` 已落地部分）。主链路已走该管道；本变更让 executor 弃用自建 `astream(values)` diff 管道，切换为同一条 `astream_events` + mapper 管道。**不新建并行概念**。
- **executor 退化为生命周期包装**：保留任务注册表、独立事件循环、watchdog、followup 队列、HITL 调度；values-diff 的三项职责（进度计数、子会话投影、中断提取）改为事件推导；`_maybe_update_context_snapshot` 删除（由管道统一写 `ContextMetricsRegistry`，按 session_id 分键主子隔离）。
- **turn 统一建模**：主 Agent 新开 run 与子 Agent followup turn 均建模为 session 上的 turn；followup 的 `followup_models` 专用结构升级为 turn 参数（model_id + reasoning_effort），推理预算随 turn 传递（补齐子 Agent followup 的档位能力）。
- **子会话 usage 落库**：`persist_projection` 终态写 `message.extra.usage`（与主链路同字段结构），子会话历史回放可重建统计。

### 前端

- **领域事件 reducer 共享**：`useSSEStream` 与 `SubagentConversationView` 消费同一 reducer（SSE 帧与 run-event 是同一组领域事件的两种传输）。
- **子会话统计条**：复用主会话 `rebuildSessionStatsFromHistory` + `formatStatsLine`，子 Agent 抽屉展示自身 turns/steps/LLM 耗时/输入输出/缓存命中。
- **子 Agent followup 推理预算选择**：composer 接入现有 `ReasoningEffortSelector`，随 followup 请求传递。

### 移除

- executor 内自建的事件 diff/投影拼装（`_child_projection_content` 及伴随逻辑，由管道终态 payload 取代）
- executor 内上下文快照提取（`_maybe_update_context_snapshot`）
- 前端两套事件消费中重复的分支逻辑

## Capabilities

### Modified Capabilities

- `agent-runtime`: 新增「Run 管道 SHALL 为主/子 Agent 唯一执行内核」条款——领域事件集合、usage 双口径累计、终态 payload、上下文快照单一来源、SSE 帧兼容。
- `agent-background-tasks`: 「Followup 续话」增加逐 turn 覆盖 model_id 与 reasoning_effort；新增「子会话用量统计」条款（usage 落库子会话消息、统计条与主会话同口径）。
- `platform-chat`: 「子 Agent（task）展示」扩展——详情抽屉复用主会话统计条，composer 与主 Agent 同构（含推理档位选择）。

## 非目标

- 不改 SSE 帧协议与 API 路径的对外形态（前端/多窗口协议兼容）。
- 不引入消息队列或跨进程 pubsub（`enable-distributed-sse-pubsub` 变更另行处理）。
- 不重构 executor 的任务调度/优先级/watchdog 策略本身，只换其 run 执行内核。
- 不做前端聊天页视觉改版。

## 依赖与顺序

- **依赖 `enable-distributed-sse-pubsub` 已落地的事件语言**（mapper/delivery，9/42 已完成）：本变更复用而非重建；其后续任务若调整 RunEvent 形态须同步。
- **先于 `subagent-cooperative-stop`（0/19 未开工）实施**：两者均改 executor 执行循环，先换核再在其上实现协作停止，避免该变更的执行循环改造被推翻重做（合入后须重写其 tasks.md）。

## 兼容性说明

- `/api/chat` SSE 帧：无破坏。
- `subagent-followup`：请求体新增可选 `reasoning_effort`，旧客户端不传即维持现状（继承创建时档位），无破坏。
- DB：`TChatMessage.extra.usage` 子会话新增写入（主链路已有同结构），无需 schema 迁移；`TAgentRun.snapshot` 结构不变。
