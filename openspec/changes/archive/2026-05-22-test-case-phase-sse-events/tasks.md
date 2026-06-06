## 1. 规格与契约测试先行

- [ ] 1.1 复核 `openspec/changes/test-case-phase-sse-events/specs/chat-sessions-and-streaming/spec.md`，与团队确认 `phaseId` 集合与必选 JSON 键（含 `phase-end` 的 `ok`）
- [ ] 1.2 在 `docs/test/test_tdd_design.md` 增补本变更的测试点摘要（仅要点，无冗长步骤）
- [ ] 1.3 扩展 `backend/tests/test_langgraph_sse_bridge_contract.py`（或约定的统一 SSE 格式化入口测试）：对 `phase-start` / `phase-end` / `phase-delta`（若实现）做 golden 解析断言

## 2. 后端：阶段事件贯通

- [ ] 2.1 在 `backend/agent/case_generate/case_coordinator.py`（及必要的 `case_graph` 钩点）按设计在 `parse_requirements`、`generate_test_points`、`await_user_confirm`、`parallel_generate_cases` 等节点发出成对 `phase-*`，`resume` 路径阶段序列与首次流一致
- [ ] 2.2 若事件经 `backend/utils/langgraph_sse_bridge.py`：为 `phase-start`/`phase-delta`/`phase-end` 增加与传统 dict 事件相同的 SSE 序列化分支，避免破坏现有 `type` 路由
- [ ] 2.3 在 `backend/services/qa_service.py`（或实际汇聚 SSE 的循环）确认仅 `TEST_CASE_QA` 与 `test-case/resume` 路径携带 `phase-*`，其它 `qa_type` 无副作用
- [ ] 2.4 与用户停止/异常路径对齐：在取消或错误时仍为当前 `phaseId` 发送 `phase-end` 且 `ok: false`（或与规格一致的等价字段）

## 3. 前端：可选消费与回归

- [ ] 3.1 在 `frontend/src/views/chat/useSSEStream.ts`（及类型定义若有）解析并向上层暴露 `phase-*`（或合并到现有消息 parts 模型），未知键忽略策略不变
- [ ] 3.2 在测试用例相关 UI（如 `chat.vue` 或测试用例专用面板）用阶段数据呈现「解析需求 → 生成测试点 → 待确认 → 并行生成」进度（最小可用：步骤文案 + 当前高亮）
- [ ] 3.3 对改动范围执行 `pnpm lint`；全量视情况 `pnpm build`

## 4. 收尾

- [ ] 4.1 `uv run app.py` 验证进程可拉起
- [ ] 4.2 变更完成后按 OpenSpec 流程归档并合并主规格 `openspec/specs/chat-sessions-and-streaming/spec.md`（使用项目既定 archive/apply 流程）
