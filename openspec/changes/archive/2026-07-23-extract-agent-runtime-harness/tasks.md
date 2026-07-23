## 0. 状态

- [x] 0.1 **SUPERSEDED（2026-07-23）**：整包搬家搁置；目标由 `unify-run-delivery` 承接。下列任务不再执行。

## 1. Runtime 模块骨架

- [ ] 1.1 ~~创建 `backend/noesis_runtime/` 包~~（取消）
- [ ] 1.2 ~~定义 `AgentRuntimeContext`~~（取消）
- [ ] 1.3 ~~定义 `prepare_context_for_eval()`~~（取消）
- [ ] 1.4 ~~添加 import 边界检查~~（取消）

## 2. 执行内核迁移

- [ ] 2.1 ~~迁入 `AgentRunExecutor`~~（取消）
- [ ] 2.2 ~~实现 `AgentRunService`~~（取消；若远期需要见 slim change）
- [ ] 2.3 ~~实现 `NoesisRuntimeClient`~~（取消）
- [ ] 2.4 ~~`BaseAgent` 委托~~（取消）

## 3. Factory 与 Backends 迁入

- [ ] 3.1 ~~迁 factory~~（取消）
- [ ] 3.2 ~~迁 middlewares~~（取消）
- [ ] 3.3 ~~迁 backends~~（取消）
- [ ] 3.4 ~~确认 create_noesis_agent~~（取消）

## 4. Agent Profile 注册表

- [ ] 4.1 ~~profiles/common_qa~~（取消）
- [ ] 4.2 ~~profiles/super_agent~~（取消）
- [ ] 4.3 ~~profiles/fault_operation~~（取消）
- [ ] 4.4 ~~registry~~（取消）

## 5. Harness 层接入

- [ ] 5.1 ~~services/agent_run_service~~（取消）
- [ ] 5.2 ~~QaService 切 AgentRunService~~（取消；改由 unify-run-delivery Fan-out）
- [ ] 5.3 ~~接入 SUPER/FAULT~~（取消）
- [ ] 5.4 ~~stop/disconnect 回归~~（取消；在 unify-run-delivery 做）

## 6. 评测路径统一

- [ ] 6.1 ~~evals 改接 Client~~（取消；远期可选）
- [ ] 6.2 ~~Harbor worker~~（取消）
- [ ] 6.3 ~~Harbor smoke~~（取消）
- [ ] 6.4 ~~BrowseComp smoke~~（取消）

## 7. 测试与文档

- [ ] 7.1 ~~test_runtime_executor~~（取消）
- [ ] 7.2 ~~更新 AGENTS.md~~（取消）
- [ ] 7.3 ~~evals README~~（取消）
- [x] 7.4 `docs/NOTES.md` 追加搁置说明（随本 supersede）
- [ ] 7.5 ~~pytest/冒烟~~（取消）

## 8. 清理

- [ ] 8.1 ~~deprecated Agent 类~~（取消）
- [ ] 8.2 ~~feature flag~~（取消）
