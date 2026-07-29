## Why

超级智能体评测已经使用 Noesis Harness，但 BrowseComp 与 Harbor 仍各自维护事件收集、模型覆盖和运行时初始化逻辑；现有 RAG 评测只验证平台检索 Service，没有覆盖 GeneralQAAgent 经 Harness Tool/Port 调用知识库的真实链路。需要在保留纯检索指标的同时，补齐 Agentic RAG 评测并收敛公共评测运行能力。

## What Changes

- 为 BrowseComp、Harbor 和 Agentic RAG 提供共享的 Agent 事件收集与最小运行结果模型。
- 新增经 `GeneralQAAgent → kb_search_tool → noesis.runtime.deps → KbRetrievalService` 执行的 Agentic RAG 评测入口；保留 `evals.kb` 作为纯检索质量评测。
- 为评测提供公开的临时 checkpointer、KB dependency binding 和 LLM 构建入口，删除对 Harness 私有全局变量和私有模型函数的依赖。
- 修复 Case RAG 演示评测的旧模块 patch，保证暂时保留的展示流程可运行；不继续扩展 Case Agent 评测能力。
- 统一 Agent 评测的最小 run manifest，并更新评测入口文档和回归测试。
- 不修改 `/api/chat`、SSE 协议、线上 Agent 行为或前端展示，不产生破坏兼容的 API 变化。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `offline-evals`：增加 Harness Agentic RAG 评测要求，并约束 Agent benchmark 共用公共运行结果与依赖覆盖能力。

## Impact

- Harness：`backend/packages/harness/noesis/config/checkpointer.py`、`runtime/deps.py`、`llm/` 的公开入口。
- 评测：`backend/evals/agent/`、`backend/evals/bootstrap.py`、Case RAG provider、结果与入口文档。
- 测试：Harness 包边界、评测事件收集、Agentic RAG scoring、Case RAG 配置覆盖。
- 外部依赖不变；Agentic RAG 集成运行仍需要 PostgreSQL、Qdrant 和真实 LLM，默认单测不访问外部服务。
