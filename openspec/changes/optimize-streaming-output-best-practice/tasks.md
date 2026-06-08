## 1. 测试设计与基础设施

- [ ] 1.1 在 `docs/test/test_tdd_design.md` 补充流式事件中介、SSE 注释保活、Langfuse ContextVar 回归、`TEST_CASE_QA` resume 的测试点
- [ ] 1.2 新增 `backend/utils/stream_bridge.py`，定义 `StreamEvent`、`HEARTBEAT_SENTINEL`、`END_SENTINEL` 与进程内 `MemoryStreamBridge`
- [ ] 1.3 新增 `backend/tests/test_stream_bridge.py`，覆盖 publish/subscribe、订阅超时心跳、publish_end、publish_error 与 cleanup

## 2. QaService 流式生产者/消费者重构

- [ ] 2.1 在 `backend/services/qa_service.py` 新增生产者 helper：在独立 Task 内消费 Agent async generator，并向 `MemoryStreamBridge` 发布业务 item、异常与结束哨兵
- [ ] 2.2 将 Langfuse `langfuse_workflow_context` 移入生产者 Task，确保上游 generator 的进入、迭代、退出在同一 Task 内完成
- [ ] 2.3 新增 SSE consumer helper：订阅 `MemoryStreamBridge`，将 `HEARTBEAT_SENTINEL` 转换为 `: keepalive\n\n`，将业务 item 继续交给 `LangGraphSseBridge.process_item`
- [ ] 2.4 保留现有 assistant 骨架行、流式检查点落库、异常落库、`bridge.finalize()` 与 `[DONE]` 收尾语义
- [ ] 2.5 在 SSE consumer 的取消/断开路径中取消 producer Task，并调用既有 Agent/Coordinator 清理逻辑，避免后台任务泄漏

## 3. TEST_CASE_QA 路径迁移

- [ ] 3.1 将 `POST /api/chat/sessions/stream` 中 `qa_type=TEST_CASE_QA` 的首轮流切换到事件中介消费方式
- [ ] 3.2 将 `POST /api/chat/sessions/{session_id}/test-case/resume` 切换到事件中介消费方式
- [ ] 3.3 确认 `CaseCoordinator.run_agent()` / `resume_agent()` 不包含 SSE 保活职责，不再被 `_iter_agent_items_with_keepalive` 跨 Task 驱动
- [ ] 3.4 确认 `phase-*`、`testpoints-confirm-required`、`scene-cases`、`error`、`finish` 输出与改造前兼容

## 4. 通用流式路径迁移与清理

- [ ] 4.1 将 `COMMON_QA`、`FAULT_OPERATION_QA`、`DEEP_RESEARCH_QA` 的 `exec_query` 上游消费统一切换到事件中介方式
- [ ] 4.2 删除或停用旧的 `_iter_agent_items_with_keepalive` generator 驱动式保活实现，避免两套保活长期并存
- [ ] 4.3 检查 `LangGraphSseBridge` 无需新增业务事件类型，前端 `useSSEStream` 对注释心跳仍保持忽略

## 5. 回归测试与验证

- [ ] 5.1 新增或更新 `backend/tests/test_sse_keepalive_iter.py`，验证心跳来自 bridge 订阅超时且不取消上游生产者
- [ ] 5.2 新增 Langfuse 开启场景回归：模拟上游长时间无输出并产生心跳，不出现 `ContextVar` 跨 Task reset 异常
- [ ] 5.3 新增 `TEST_CASE_QA` 首轮与 resume 的 SSE golden/契约测试，断言阶段帧、心跳注释、错误与 `[DONE]` 收尾兼容
- [ ] 5.4 运行受影响后端 pytest，至少覆盖 stream bridge、SSE keepalive、Langfuse tracing、case resume flow
- [ ] 5.5 运行 `uv run app.py` 验证后端可正常拉起，并在验证完成后停止进程