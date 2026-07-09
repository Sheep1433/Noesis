## 1. Runtime 模块骨架

- [ ] 1.1 创建 `backend/noesis_runtime/` 包：`context.py`、`executor.py`、`run_service.py`、`client.py`、`profiles/registry.py`
- [ ] 1.2 定义 `AgentRuntimeContext` 与 `prepare_runtime_context()`（Harness：`services/agent_runtime_service.py`）
- [ ] 1.3 定义 `prepare_context_for_eval()` 供 `evals.agent` 无 DB 场景使用
- [ ] 1.4 添加 import 边界检查（`tests/test_runtime_import_boundary.py`：runtime 不得 import services/api/domain.chat）

## 2. 执行内核迁移

- [ ] 2.1 将 `BaseAgent._stream_agent_response` 逻辑迁入 `noesis_runtime/executor.py`（`AgentRunExecutor`）
- [ ] 2.2 实现 `AgentRunService`：start_run、cancel_run、与 `MemoryStreamBridge` 集成
- [ ] 2.3 实现 `NoesisRuntimeClient.stream()` / `run()` 嵌入式 API
- [ ] 2.4 `BaseAgent` 委托 `AgentRunExecutor`（过渡期兼容）

## 3. Factory 与 Backends 迁入

- [ ] 3.1 将 `agent/factory.py` 迁至 `noesis_runtime/factory.py`；原路径 re-export
- [ ] 3.2 将 `agent/middlewares/` 迁至 `noesis_runtime/middlewares/`；更新 import
- [ ] 3.3 将 `agent/backends/` 迁至 `noesis_runtime/backends/`；`services/sandbox_service` 仍在 Harness 预启动沙箱
- [ ] 3.4 确认 `create_noesis_agent` 行为与迁移前单测一致

## 4. Agent Profile 注册表

- [ ] 4.1 实现 `profiles/common_qa.py`（自 `GeneralQAAgent` 提取装配）
- [ ] 4.2 实现 `profiles/super_agent.py`（自 `SuperAgent` 提取）
- [ ] 4.3 实现 `profiles/fault_operation.py`（自 `FaultOperationAgent` 提取）
- [ ] 4.4 `profiles/registry.py`：`resolve_profile(qa_type)` + 未知类型错误

## 5. Harness 层接入

- [ ] 5.1 新增 `services/agent_run_service.py` 作为 `QaService` 与 Runtime 接缝
- [ ] 5.2 `QaService.exec_query`：`COMMON_QA` 先切至 `AgentRunService`（可用 `runtime.use_agent_run_service` feature flag）
- [ ] 5.3 接入 `SUPER_AGENT_QA`、`FAULT_OPERATION_QA`
- [ ] 5.4 验证 stop / disconnect / partial 落库与 SSE 契约不变

## 6. 评测路径统一

- [ ] 6.1 `evals/agent/_agent.py` 改接 `NoesisRuntimeClient`
- [ ] 6.2 `evals/agent/harbor/noesis_worker.py` 改接 Client，删除重复 factory 拼装
- [ ] 6.3 Harbor smoke：`./evals/agent/harbor/run.sh --n-tasks 1` 通过
- [ ] 6.4 BrowseComp smoke：`uv run python -m evals.agent.browsecomp --tag runtime-smoke --num-examples 1`

## 7. 测试与文档

- [ ] 7.1 新增 `tests/test_runtime_executor.py`（finish/abort/error 哨兵）
- [ ] 7.2 更新 `backend/AGENTS.md` 目录树与 Runtime/Harness 分层说明
- [ ] 7.3 更新 `backend/evals/README.md` 注明评测经 `noesis_runtime`
- [ ] 7.4 追加 `docs/NOTES.md` 知识卡片（架构变更记录）
- [ ] 7.5 `uv run pytest tests/ -q` 相关子集通过；`uv run app.py` 冒烟

## 8. 清理（可选，本 change 末尾）

- [ ] 8.1 `GeneralQAAgent` / `SuperAgent` / `FaultOperationAgent` 标记 deprecated，主体改为一行委托 Client
- [ ] 8.2 默认开启 `runtime.use_agent_run_service`，移除 feature flag（确认稳定后）
