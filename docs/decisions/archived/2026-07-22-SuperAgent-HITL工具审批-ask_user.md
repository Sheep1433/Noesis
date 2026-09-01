# 决策：SuperAgent HITL（工具审批 / ask_user）

状态：implemented
日期：2026-07-22
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **与测试用例 interrupt 区别**：测试用例是 `interrupt_before` 节点级暂停 + `Command(update=...)`；HITL 是 `HumanInTheLoopMiddleware` 工具级 `interrupt(HITLRequest)` + `Command(resume={"decisions":...})`。
- **SSE 形状**：对齐 `test-case/resume`——首段发 `hitl-required` 后以 `finish_reason=hitl_pending` + `[DONE]` 收尾，**不** completed 落库；resume 新开 SSE，续写同一 `assistant_message_id`。
- **关键模块**：`agent/hitl/`（policy / ask_user / session_grants / pending / timeout）、`create_noesis_agent(interrupt_on=...)`、`POST .../hitl/resume`。
- **配置**：`hitl.enabled`（默认 false）、`hitl.ask_timeout_seconds`（默认 300）。
