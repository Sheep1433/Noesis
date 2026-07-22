## 1. 配置与工厂

- [x] 1.1 在 `config.yaml` / `config/env.py` 增加 `hitl.enabled`、`hitl.ask_timeout_seconds`，并更新 `config.example.yaml`
- [x] 1.2 `create_noesis_agent` 增加 `interrupt_on` 参数，条件挂载 `HumanInTheLoopMiddleware`（位于 `extra_middleware` 之后、`build_noesis_runtime_middleware` / `ToolErrorHandlingMiddleware` 之前）
- [x] 1.3 单测：`hitl.enabled=false` 时不挂载中间件；`true` 时栈顺序正确

## 2. HITL 策略与 ask_user 工具

- [x] 2.1 新建 `agent/hitl/policy.py`：实现 memory 路径、`execute` 危险命令（删除/网络/pipe）`when` 谓词
- [x] 2.2 新建 `agent/hitl/tools.py`：`ask_user` StructuredTool + `build_interrupt_on()` 工厂
- [x] 2.3 新建 `agent/hitl/session_grants.py`：进程内 session 级网络命令 grant 集（memory 写入不可 grant）
- [x] 2.4 单测：策略表边界（workspace write 放行、memory write 拦截、curl/rm -rf 拦截、pytest 放行）

## 3. SuperAgent 接入

- [x] 3.1 `SuperAgent` 在 `hitl.enabled=true` 时注册 `ask_user` 与 `interrupt_on`
- [x] 3.2 `task-worker` SubAgent 继承相同 `interrupt_on`
- [x] 3.3 更新 `agent/prompts/super_agent.py`：明确「入口纯文本澄清」vs「中途 ask_user」边界

## 4. 流式桥接与 resume 编排

- [x] 4.1 `LangGraphSseBridge` 或 `QaService` 适配：检测 interrupt → 发 SSE `hitl-required`（含 `kind`、`action_requests`、`expires_at`）
- [x] 4.2 本段流以 `finish_reason=hitl_pending` + `[DONE]` 收尾；**禁止**此时 `_finalize` 为 `completed`
- [x] 4.3 实现超时任务：超时按 reject 处理并终态落库（可无活跃 SSE）
- [x] 4.4 `schemas/qa_vo.py`：`HitlResumeRequest`（成功路径为 SSE，无需成功 JSON Response VO）
- [x] 4.5 `POST /api/chat/sessions/{session_id}/hitl/resume`：归属校验 + CSRF + `Command(resume=...)`，响应为新 SSE（对齐 test-case/resume）
- [x] 4.6 `QaService.exec_hitl_resume`：新开流式生成器，续写同一 `assistant_message_id`
- [x] 4.7 集成测试：mock interrupt → resume approve → 工具输出可见

## 5. 落库与 parts 扩展

- [x] 5.1 assistant `content.parts` 扩展 `hitl` 字段（pending/approved/rejected/answered）
- [x] 5.2 拒绝的工具仍产出 `tool-output-available`（`status=error`），与 `agent-tool-failure-handling` 一致
- [x] 5.3 单测：HITL 全流程仍单行 assistant 落库

## 6. 前端

- [x] 6.1 `useSSEStream.ts` 解析 `hitl-required`；`finish_reason=hitl_pending` 时保持卡片可交互（不按普通完成收尾）
- [x] 6.2 `HitlApprovalCard`：允许一次 / 本会话允许（仅网络类 execute）/ 拒绝
- [x] 6.3 `HitlClarificationCard`：`ask_user` 问题 + 自由文本或 options（单选）
- [x] 6.4 嵌入 chat 时间线，绑定 `tool_call_id`，调用 `hitl/resume` 并消费返回的新 SSE（对齐 `resumeTestCase`）
- [x] 6.5 等待态 UI：展示「等待确认」，`isLoading` 在 `hitl_pending` 后可降为 false，提交 resume 后再升为 true

## 7. 验证与文档

- [x] 7.1 `uv run pytest tests/ -q` 覆盖新增 hitl 单测与集成测
- [x] 7.2 `pnpm lint`（前端改动范围）
- [ ] 7.3 手动验收：SuperAgent 触发 memory 写入审批、ask_user 澄清、拒绝 execute
- [x] 7.4 `docs/NOTES.md` 追加 HITL 架构笔记（interrupt/resume 与测试用例 interrupt 区别）
