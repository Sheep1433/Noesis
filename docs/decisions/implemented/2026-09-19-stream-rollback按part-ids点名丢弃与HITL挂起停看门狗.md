# 决策：stream-rollback 按 part_ids 点名丢弃 + HITL 挂起停看门狗

状态：implemented
日期：2026-09-19

## 问题

代码审查实锤四个 P1，集中暴露两类结构性缺陷：

1. **重试回滚边界失准**（`langgraph_bridge._rollback_attempt_stream`）：回滚语义是「丢弃末尾连续 Text/Reasoning part」，三处独立实现（bridge builder / projection / 前端 reducer）各自手抄。但失败尝试的输出与「尾部连续文本 part」不是同一个集合——压缩分割线（`—— 以上对话已压缩摘要 ——`）与其前的正文同为 TextPart，零输出的失败重试会把它们连同弹掉，三方一致丢内容且不可恢复。
2. **HITL 挂起不取消 run 时长看门狗**：停看门狗 + 挂 HITL 专属超时的逻辑只在 `transition()` 里，而生产路径的状态迁移走 `apply_event`（`transition()` 的调用方只剩测试续跑）——审批等待计入 run 时长，super agent 1800s 被 `RUN_TIMEOUT` 误杀，用户还盯着审批卡片。

另有两个前端 P1：409 冲突路径在代际校验之前写共享状态（过期 409 可把停止按钮指向别的会话的 run）；`resumeHitl` 在 `beginStream` 之后 POST 失败无复位（`isLoading` 永久卡死，发送被防重守卫静默吞掉）。

## 决策

1. **回滚协议改为服务端单一事实源**：bridge 追踪「当前模型尝试（自上次 `on_chat_model_start` 起）铸造的 part id 集合」；`stream-rollback` 帧携带 `part_ids` 点名丢弃，零输出失败不发音。不变量：工具开始/收尾/HITL/压缩等 flush 点都意味着本次调用已成功，**失败尝试的输出恰好等于尝试期间铸造的 parts**——builder 侧无需弹回（尝试内容从未进入 builder，只在 ctx 缓冲），consumer（projection / 前端）按 id 丢弃。配套：`append_text_delta`/`append_reasoning_delta` 增加按 part_id 路由（无 part_id 保持旧的尾部合并）；前端 text part 继承 wire part id，redacted-thinking 拆分段用 `id~think` 派生 id，`dropStreamPartsById` 按 id 与 `~` 前缀匹配。
2. **HITL 配套收口进唯一入口**：抽 `_enter_hitl_pending_locked`（挂 HITL 专属超时 + 停看门狗），`transition()` 与 `apply_event` 的状态迁移共用；审批等待不再计入 run 时长，`resume()` 后看门狗重新计时。
3. **前端代际纪律**：409 冲突路径先做 `isCurrentStream` 校验再写 `currentRunId`/sessionStorage/排队消息；`resumeHitl` 的 POST 失败路径复位 `isLoading` 并上报 disconnected 后 rethrow（调用方 `submitHitlFromPanel` 拦截 rejection）。

## 备选方案

- **回滚按「parts 数量基线」弹回**（记 attempt 开始时的 parts 数，弹回该基线）：被否——consumer 的 parts 数与 builder 不一致（projection/前端即时应用 delta，builder 到 flush 点才落盘），基线无法跨三方传递；且前端会把跨模型调用的正文合并进同一 part，数量基线表达不了「丢弃半个 part」。
- **回滚帧携带完整权威 parts 快照**（consumer 整体替换）：被否——长消息每次重试全量下发数百 KB，重试虽罕见但帧语义过重。
- **HITL 修法在 apply_event 内联看门狗处理**：被否——状态迁移配套逻辑（信令、超时、看门狗）已有 `transition()` 一处，内联会造出第二份漂移点；抽共用入口顺手消除 `transition()` 孤儿化。

## 代价

- wire 协议变更：`stream-rollback` 新增 `part_ids` 字段（事件名不变，词表契约测试不红；§4.2b 文档已同步）。前后端同仓同部署，无兼容窗口。
- 前端 text/reasoning part id 从本地 `genPartId` 改为优先继承 wire part id——快照对比/测试如依赖 id 格式需注意（现有测试无此依赖）。
- `model_calls[].attempt` 恒差 1（middleware 发失败位次、bridge 按下一位次消费）为已知 P2 遗留，未在本决策内处理。

## 验证

- 后端：`tests/test_run_manager.py`（生产路径 HITL 红测试→绿）、`test_langgraph_sse_bridge_contract.py`（零输出不回滚 / part_ids 点名）、`test_run_state_model.py`（projection 按 id 丢弃）、`test_message_builder.py`（drop_parts）；相关面广域扫描 546 passed。
- 前端：`__tests__/messageParts.test.ts`（part_id 路由 + 按 id 丢弃 + redacted 派生 id）、`__tests__/useSSEStream.test.ts`（过期 409 不污染 / resumeHitl 失败复位，经临时回退验证红能力）；`pnpm test` 280 passed、`pnpm lint` 0 error。
