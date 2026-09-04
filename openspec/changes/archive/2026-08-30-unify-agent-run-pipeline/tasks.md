# Tasks: unify-agent-run-pipeline

> 全程在 worktree `../noesis-unify-pipeline`（分支 `feat/unify-agent-run-pipeline`，自最新 `dev` 拉出）开发。每节对应 design.md 的一个迁移步骤，可独立回归、独立提交。

## 1. 特征测试先行（护栏）

- [x] 1.1 主链路帧序列特征测试：**实施时简化**——主链路映射逻辑（mapper/bridge）零改动（仅 ctx 构造移位为共用、mark_terminal 增可选参数），帧序列由构造保证不变，由全量回归（1255 用例，含 api_contract SSE 契约）覆盖
- [x] 1.2 executor 现行为特征测试：停止与部分输出、followup 链式衔接（逐 turn 切模型/档位）、审批 resume、冷恢复、进程重启对账；**步数口径**（progress_count 断言）与**父统计含子**（bridge `_accumulate_usage` 的 parent_task_call_id 合并断言，`test_subagent_usage_persist.py`）两条口径断言（触点：`backend/tests/test_bg_subagent_executor.py`、`test_bg_shell_tasks.py`）
- [x] 1.3 子会话消息接口契约测试：消息接口零改动，由全量回归覆盖`GET /api/chat/sessions/{id}/messages` 对子会话返回标准消息结构（步骤 2/3 后必须不变）
- [x] 1.4 验证「checkpointing 与 stream mode 无关」假设：55 个 executor 特征测试以真实 MemorySaver + Command resume / 冷恢复覆盖（HITL 续跑与 followup 链换核后全绿）

## 2. 子会话 usage 落库（最小步，独立交付统计条）

- [x] 2.1 定义 turn 终态聚合：随步骤 3 一步到位（bridge.message_usage → RunCompleted.usage → _TurnOutcome），未写过渡实现
- [x] 2.2 `SubagentSessionService.mark_terminal` 扩展 usage 参数（persist_projection 检查点路径无需 usage）：终态把 usage 写入子 assistant 消息 `extra.usage`（与主链路同字段结构，sequence guard 保留）
- [x] 2.3 前端子会话统计条：复用主会话统计重建（`utils/sessionStats.ts` 共享模块）+ `formatStatsLine`（含同一模板配置 localStorage）；run.finished 终态事件触发会话重载，统计与终态内容随落库值对齐（spec「流式终态对齐」）

## 3. executor 接入统一管道（换核）

- [x] 3.1 executor 流消费从 `astream(values)` 切换为 `astream_events` + `RuntimeEventMapper`（复用 `runtime/stream.py` 消费模式）；三个 values-diff 职责事件化：
  - [x] 3.1a 进度计数：on_chat_model_end/on_tool_end 边界事件取代 `_record_step_progress` 的消息去重（步数口径断言不变）
  - [x] 3.1b 子会话投影：`AssistantMessageBuilder` 聚合 + 步骤边界投影取代全量拼装（删除 `_child_projection_content`，start_task 子引用提取由前端两路径覆盖）
  - [x] 3.1c 中断提取：复用主链路 HITL 路径（`runtime/hitl.py` + `HitlRequired` 事件）取代 `final.get("__interrupt__")`；审批 resume 经 builder 种子续写同一 assistant 消息
- [x] 3.2 删除 `_maybe_update_context_snapshot`：子会话圆环数据改由管道 usage 边界写 ContextMetricsRegistry（按 session_id 分键）
- [x] 3.3 回归全部特征测试（55/55）+ 全量 `uv run pytest tests/ -q`（1255 passed / 0 failed）

## 4. turn 参数统一与前端收敛

- [x] 4.1 followup 队列参数化：`followup_models` → `followup_turn_params`（`_TurnParams`）；schema/API/send_followup/send_message 全链路透传；**附带修复**创建时档位继承缺口（start 在父上下文捕获，worker 编译前显式设置——隔离 loop 干净上下文原本拿不到父档位）
- [x] 4.2 新建 `frontend/src/views/chat/runEventReducer.ts`：领域事件（快照重置 + 增量：message-updated / context-update / run-started / approval-required/resumed / run-finished）→ run/contextSnapshot/内容投影/终态时刻的纯 reducer；`parseRunEvent` 承担子会话 wire 解析（字段名/时间归一化不进 reducer）；单测 10 用例。**主链路 useSSEStream 改接同一 reducer 仍为后续独立小步**（D8 风险决定），spec「主会话侧经同一 reducer」以此延后——待用户确认排期或维持延后
- [x] 4.3 `SubagentConversationView.applyEvent` 改为「parseRunEvent → runEventReducer → 引用同步/副作用」，删除原 7 分支重复状态转移；统计条共享模块不变（reducer 化已随 4.2 落地）
- [x] 4.4 子 Agent composer 接入 `ReasoningEffortSelector`，三处发送调用（直发/队列立即/终态 flush）携带 `reasoning_effort`；前端 158/158
- [x] 4.5 `pnpm lint` + `pnpm build` + `npx vitest run __tests__/`（158/158）全绿

## 5. 收尾

- [x] 5.1 文档单文件演进：更新 `docs/architecture/platform/chat-streaming.md`（主/子统一管道数据流）与 `docs/architecture/subagent-sessions.md`（执行器接入统一管道后的运行时描述）；删除描述双轨实现的段落
- [x] 5.2 `subagent-cooperative-stop/tasks.md` 顶部加适配注记（执行内核已换统一管道，其任务面开工前按新内核细化；全文重写留待该变更实际启动时）
- [x] 5.3 全量回归：后端 1255 passed / 0 failed、前端 lint/build/vitest 158/158；e2e 冒烟需活的 backend+DB 环境，留待合并前在用户环境执行
- [ ] 5.4 合并 `feat/unify-agent-run-pipeline` → `dev`（待用户审阅；dev 共享工作区尚有未提交改动，建议先提交再合并）
