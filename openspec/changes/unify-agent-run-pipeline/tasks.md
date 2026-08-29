# Tasks: unify-agent-run-pipeline

> 全程在 worktree `../noesis-unify-pipeline`（分支 `feat/unify-agent-run-pipeline`，自最新 `dev` 拉出）开发。每节对应 design.md 的一个迁移步骤，可独立回归、独立提交。

## 1. 特征测试先行（护栏）

- [ ] 1.1 主链路帧序列特征测试：合成 LangGraph/LangChain 事件序列（含 reasoning/text 交替、工具调用、审批、错误路径）喂 `RuntimeEventMapper`，断言 typed RunEvent/WireFrame 序列并固化为基准——不依赖真实 LLM，可重复（触点：`backend/tests/api_contract/`，复用既有 mock 基建）
- [ ] 1.2 executor 现行为特征测试：停止与部分输出、followup 链式衔接（含 followup_models 逐 turn 切模型）、审批 resume、冷恢复、进程重启对账；**步数口径**（同输入下 progress_count 换核前后一致）与**父统计含子**（父会话当轮 usage 含子 Agent 消耗）两条口径断言（触点：`backend/tests/test_bg_subagent_executor.py`、`test_bg_shell_tasks.py`）
- [ ] 1.3 子会话消息接口契约测试：`GET /api/chat/sessions/{id}/messages` 对子会话返回标准消息结构（步骤 2/3 后必须不变）
- [ ] 1.4 验证「checkpointing 与 stream mode 无关」假设：同配置下 values / astream_events 两种消费的 checkpoint 落点一致性实验（步骤 3 的前置依据）

## 2. 子会话 usage 落库（最小步，独立交付统计条）

- [ ] 2.1 定义 `TurnResult{content, usage, finish_reason, status}` 聚合：executor 侧先从 astream values 的 `usage_metadata` 聚合（过渡实现，接口按终态设计）
- [ ] 2.2 `SubagentSessionService.persist_projection` 扩展接收 `TurnResult`：终态把 usage 写入子 assistant 消息 `extra.usage`（与主链路同字段结构，sequence guard 保留）
- [ ] 2.3 前端子会话统计条：复用主会话 `rebuildSessionStatsFromHistory` + `formatStatsLine` 渲染（复用组件，不加新接口）

## 3. executor 接入统一管道（换核）

- [ ] 3.1 executor 流消费从 `astream(values)` 切换为 `astream_events` + `RuntimeEventMapper`（复用 `runtime/stream.py` 消费模式）；三个 values-diff 职责事件化，各自独立提交可回退：
  - 3.1a 进度计数：tool-call 边界事件计数取代 `_record_step_progress` 的消息去重
  - 3.1b 子会话投影：`AssistantMessageBuilder` 聚合 + 步骤边界投影取代 `_persist_child_projection` 的全量拼装（删除 `_child_projection_content`）
  - 3.1c 中断提取：复用主链路 HITL 路径（`runtime/hitl.py` + `HitlRequired` 事件）取代 `final.get("__interrupt__")`
- [ ] 3.2 删除 `_maybe_update_context_snapshot`：子会话圆环数据改由管道 usage 边界写 ContextMetricsRegistry（按 session_id 分键）
- [ ] 3.3 回归 1.1/1.2/1.3 全部特征测试 + HITL / 冷恢复 / 停止语义手动验证；`uv run pytest tests/ -q` 全绿

## 4. turn 参数统一与前端收敛

- [ ] 4.1 followup 队列参数化：`followup_models` → `followup_turn_params`（model_id + reasoning_effort），链式开新 turn 时逐条生效；`send_followup` / `send_message` / `POST /api/chat/sessions/{id}/subagent-followup` 全链路透传可选 `reasoning_effort`（旧客户端缺省 = 继承创建时档位）
- [ ] 4.2 新建 `frontend/src/views/chat/runEventReducer.ts`：领域事件 → 消息 content/usage/run 状态变更的纯 reducer（支持「快照重置 + 增量事件」双入口）。**后置可选步**：主链路 `useSSEStream.ts` 已被多窗口/重连/快照恢复反复打磨，将其改接 reducer 属高风险低收益——默认只让子会话消费走 reducer（4.3），主链路迁移作为本变更收尾后的独立小步，未做也不影响后端统一
- [ ] 4.3 `SubagentConversationView.applyEvent` 改为「run-event 解析 → 同一 reducer」，删除重复分支逻辑；终态事件更新当轮 usage 与统计条
- [ ] 4.4 子 Agent composer 接入 `ReasoningEffortSelector`（props 同主 Agent），followup 请求携带 `reasoning_effort`；测试补充：档位选择随消息入队绑定、逐 turn 生效（触点：`frontend/__tests__/childCatalogRealtime.test.ts`）
- [ ] 4.5 `pnpm lint` + `pnpm build` + `npx vitest run __tests__/` 全绿

## 5. 收尾

- [ ] 5.1 文档单文件演进：更新 `docs/architecture/platform/chat-streaming.md`（主/子统一管道数据流）与 `docs/architecture/subagent-sessions.md`（执行器接入统一管道后的运行时描述）；删除描述双轨实现的段落
- [ ] 5.2 同步重写 `openspec/changes/subagent-cooperative-stop/tasks.md`：静止边界协作停止改在统一管道上实现（该变更在本变更合入后开工）
- [ ] 5.3 全量回归：后端 `uv run pytest tests/ -q`、前端 lint/build/vitest、e2e 冒烟（多窗口 `multi-tab.spec.ts`）
- [ ] 5.4 合并 `feat/unify-agent-run-pipeline` → `dev`，合并前按仓库规则完成双向验证
