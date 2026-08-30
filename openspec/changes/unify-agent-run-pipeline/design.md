# 设计：统一主/子 Agent 运行管道

## Context

当前两套链路（文件均为仓库实际路径）：

**主链路（请求作用域）**
- 入口：`backend/server/api/chat_api.py` → `QaService.exec_query`（`backend/packages/noesis-core/src/noesis/services/qa/service.py`）。
- 执行与映射：`backend/packages/noesis-core/src/noesis/chat/event_mapping/langgraph_bridge.py` —— LangGraph 流事件 → SSE 帧字符串（`_format_sse`），同时完成 usage 累计（`_accumulate_usage` → `message_usage`，终态随 `finish` payload 落库 `message.extra.usage`）、上下文快照（`ContextMetricsRegistry`）、HITL payload 提取。
- 消费方：前端 `frontend/src/views/chat/useSSEStream.ts` 解析 SSE 帧。

**子链路（脱离请求生命周期）**
- 执行：`backend/packages/noesis-core/src/noesis/agents/subagents/executor.py` —— 自建 `_TASKS` 注册表 + 独立事件循环（`_submit_isolated`），`agent.astream(source, _config(entry), stream_mode="values")` 逐 chunk diff。
- 落库：`_persist_child_projection` → `SubagentSessionService.persist_projection`（`subagent_session_service.py`，只写 content+status，**无 usage**）。
- 事件：`_publish_task_event` 自有发布；快照：`_maybe_update_context_snapshot` 自有提取；followup：`followups`/`followup_message_ids`/`followup_models` 专用队列。
- 消费方：前端 `frontend/src/components/SubagentConversationView/index.vue` 自写 `applyEvent`。

数据层已统一（子会话即标准 `TChatMessage`/`TAgentRun`）；分叉全部在运行时服务层与前端消费层。

## Goals / Non-Goals

**Goals:**
1. RunPipeline 成为唯一执行管道：事件映射、usage 累计、终态 payload、上下文快照只有一份实现。
2. executor 只保留生命周期差异（注册表、独立 loop、watchdog、followup 队列、HITL 调度）。
3. turn 统一建模：主 Agent 新 run 与子 Agent followup 是同一种「session 上的 turn」，turn 参数含 model_id 与 reasoning_effort。
4. 子会话获得 usage 落库与统计条；前端事件消费收敛为一个领域事件 reducer。
5. SSE 对外帧协议与现有 API 形态不变。

**Non-Goals:**
- 不改调度/优先级/watchdog 策略；不引入分布式 pubsub；不做前端视觉改版；不动 LangGraph checkpoint 机制。

## Decisions

### D1 复用既有 typed RunEvent 管道，不新建概念

仓库已存在领域/传输分离（`enable-distributed-sse-pubsub` 变更已落地的部分）：

- `noesis/chat/event_mapping/mapper.py` — `RuntimeEventMapper`：LangGraph/LangChain raw event → typed `RunEvent` 的唯一映射入口（无状态）。
- `noesis/chat/delivery/events.py` — 内部事件语言：`WireFrame`（与 SSE 帧一一对应）、`RunCompleted`/`RunAborted`/`RunError`/`RunPaused`/`HitlRequired`。
- `noesis/chat/delivery/sse.py` — SSE 字符串编码只在 Delivery 边界发生。

**本变更不新建 RunPipeline 概念**。主链路已经是「`runtime/stream.py` astream_events 消费 → RuntimeEventMapper → typed RunEvent → delivery」。统一的全部工作是：**executor 弃用自建 `astream(values)` diff 管道，接入同一条 typed RunEvent 管道**，并在两侧共享的终态 payload 上对齐落库（D2）。新增代码仅限：executor 侧的 RunEvent → 子会话投影/事件发布适配层，以及 `TurnResult` 聚合（usage/finish_reason 终态形状）。

> 审查修正记录：初版 design 提议新建 `run_pipeline.py` 与一套领域事件 dataclass——与既有 mapper/delivery 重复造概念，已废弃该方案。

### D2 落库与终态对齐

- 主链路落库：现状路径不变（projection 消费 finish payload → `message.extra.usage`）。
- 子链路落库：executor 从 typed RunEvent（`RunCompleted`/`RunAborted` 携带 usage）聚合 `TurnResult{content, usage, finish_reason, status}`；`persist_projection` 扩展签名接收之，终态把 usage 写入子 assistant 消息 `extra.usage`（与主链路同结构：steps/llm_ms/input_tokens/output_tokens/cache_read_tokens/cache_write_tokens）。sequence guard 保留。

### D3 流消费模式统一为 astream_events（最高风险项）

主链路消费 `agent.astream_events(...)`（`runtime/stream.py`），executor 现消费 `agent.astream(source, _config(entry), stream_mode="values")`（全量快照 diff）。统一管道要求 executor 换消费模式，values-diff 承担的三个职责必须改为事件推导：

| values-diff 职责 | 现实现 | 事件化替代 |
|---|---|---|
| 进度计数 | `_record_step_progress` 对 values chunk 消息去重计数 | 由 typed RunEvent 的 tool-call 边界事件计数 |
| 子会话投影 | `_persist_child_projection(messages[projection_offset:])` 全量拼装 | `AssistantMessageBuilder` 聚合 + 步骤边界投影（与主链路同构） |
| 中断提取 | `final.get("__interrupt__")` | 主链路 HITL 路径（`runtime/hitl.py` + `HitlRequired` 事件）复用 |

LangGraph checkpointing 与 stream mode 无关（checkpointer 配置驱动），冷恢复语义不受影响——但此假设须在特征测试中显式验证。

补充两点事件化约束：

- **步数口径兼容**：`progress_count` 的产品口径是「步数 = 工具调用数」（任务目录与父会话统计条可见）。事件化计数 SHALL 保持该口径不变，特征测试须断言换核前后同输入步数一致。
- **delta 事件轻处理**：executor 场景无 SSE 逐 token 消费者；其对 ReasoningDelta/TextDelta 类高频事件 MAY 只做 builder 累积、不做逐事件发布（run-event 仍按步骤边界出快照），控制独立事件循环上的事件处理开销。父会话「主+子合并」usage 依赖子 usage 随 tool 结果回传父流——特征测试须覆盖「父统计含子」不回退。

### D4 usage 口径（兼容现有两条口径，显式命名）

- `message_usage`（主+子合并）：父会话当轮 assistant 消息聚合——子 Agent usage 经 tool 输出携带 `usage_metadata` 流入父流，父侧 mapper 计入（现状机制保留，不动）。
- `session_usage`（本会话自身）：子会话 assistant 消息各自落库（本变更新增）——executor 的 RunCompleted.usage 落 `persist_projection`。

### D5 上下文快照单一来源

删除 executor 的 `_maybe_update_context_snapshot`；快照由 typed RunEvent 管道的 usage 边界写 `ContextMetricsRegistry`（按 session_id 分键，主子隔离）。

### D6 turn 参数与推理档位

- followup 队列的 `followup_models: list[str|None]` 升级为 `followup_turn_params: list[TurnParams|None]`，`TurnParams = {model_id, reasoning_effort}`。
- `send_followup` / `subagent-followup` API / executor `send_message` 透传 `reasoning_effort`；档位 ContextVar 在 executor 的 followup turn 入口设置（主链路设置点现状不动）。
- 创建时继承语义不变：子 Agent 首轮继承父 run 档位。

### D7 前端共享 reducer

- 新建 `frontend/src/views/chat/runEventReducer.ts`：消费统一形状的领域事件，输出消息 content/usage/run 状态变更。
- `SubagentConversationView`：run-event 解析（parseRunEvent）→ 领域事件 → reducer（已落地）。
- `useSSEStream` 的帧解析层产出同一事件 vocabulary 接入 reducer：**后续独立小步**（见 D8 风险决定），未在本变更实施。
- 已知成本：子链路 `run-snapshot` 是全量快照而非增量事件——reducer 需同时支持「快照重置」与「增量事件」两种入口（传输层形态适配，不追求 wire 层统一）。
- 子会话统计：复用主会话 `rebuildSessionStatsFromHistory` + `formatStatsLine`。

### D8 迁移策略：四步各自可回归

1. **特征测试先行**：合成 LangGraph/LangChain 事件序列喂 mapper，固化 typed RunEvent/帧序列基准（api_contract 已有 mock 基建，不依赖真实 LLM 可重复）；executor 现行为特征测试（停止/HITL/冷恢复/followup 链）。
2. **子会话 usage 落库**（最小步）：`persist_projection` 终态写 usage——executor 侧先从 astream 的 `usage_metadata` 聚合（接口按 D2 的 `TurnResult` 设计），子会话统计条即刻可用，独立验收。
3. **executor 换核**：接入 astream_events + RuntimeEventMapper（D3 三项职责事件化）；删除 `_maybe_update_context_snapshot`、`_child_projection_content`。
4. **turn 参数统一 + 前端 reducer**：`followup_models` → `followup_turn_params`；API 加 `reasoning_effort`；前端两处消费换 reducer，子 Agent composer 接 `ReasoningEffortSelector`。

回滚：每步独立提交于 `feat/unify-agent-run-pipeline` 分支（worktree 开发），任一步回归失败可单独 revert。

## 与并行变更的顺序依赖

- **enable-distributed-sse-pubsub（进行中，9/42）**：本变更依赖其已落地的 mapper/delivery 事件语言；若其后续任务改动 RunEvent 形态（Redis 广播/sequence），本变更的第 3 步须与之同步。建议同一开发者在两个变更间按「pubsub 优先完成事件面，本变更接 executor」协调。
- **subagent-cooperative-stop（未开工，0/19）**：与 executor 执行循环强耦合。建议顺序：本变更先行换核（执行循环收口到统一管道），cooperative-stop 在统一后的管道上实现静止边界协作停止——其 tasks.md 须基于新内核重写后再开工；若反过来先做，其执行循环改造将被本变更推翻重做。

## Risks / Trade-offs

- **executor 换流消费模式（最高风险）**：values-diff → astream_events 的三项职责事件化（进度计数/投影拼装/中断提取）中，中断提取与投影边界最容易引入回归；「checkpointing 与 stream mode 无关」的假设须显式验证。缓解：步骤 1 特征测试覆盖停止/HITL/冷恢复；步骤 3 内部再分三个可独立回退的小步。
- **SSE 帧兼容风险**：帧序列基准以合成事件测试固化（完整帧清单以现网 bridge 实际产出为准——含 message-start、text/reasoning start-end 边界、tool-input-*、error/abort、hitl-required、run-status 等，不靠文档枚举）。
- **与 enable-distributed-sse-pubsub 的耦合**：RunEvent 形态若被其后续任务调整，本变更第 3 步需同步；两个变更不要并行开发 executor/delivery 同一文件。
- **两套事件形态对齐成本**：子链路 run-snapshot 全量快照 vs 领域事件增量——reducer 需支持「快照重置 + 增量」双入口。
- **不建议一次性大爆炸**：四步必须分提交序列，每步可独立合并回 dev；中途暂停已完成步骤仍有独立价值。
