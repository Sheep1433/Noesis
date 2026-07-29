## Context

`evals.agent.browsecomp` 通过 `SuperAgent` 运行，Harbor worker 通过 `noesis.factory.create_noesis_agent` 和 `noesis.runtime.stream.stream_agent_events` 运行，两者共用 Harness 内核，但分别解析同一批 LangChain/LangGraph 事件。Harbor 还直接调用 `noesis.llm.factory._build_chat_model`，`evals.bootstrap` 直接替换 `noesis.config.checkpointer._saver`。

`evals.kb` 直接评测 `noesis_server.kb.retrieval.KbRetrievalService`，适合 Recall@K 等确定性指标，但没有覆盖 `GeneralQAAgent → kb_search_tool → runtime deps`。Case RAG provider 仍 patch 已删除的 `agent.case_generate` 路径。

## Goals / Non-Goals

**Goals:**

- 保持 BrowseComp 和 Harbor 的被测执行都经过 Harness，同时收敛公共事件结果。
- 保留纯 Retrieval Eval，新增独立 Agentic RAG Eval，覆盖真实 Harness Tool/Port 链路。
- 评测只依赖 Harness 公开 API，不修改 `_saver` 或调用 `_build_chat_model`。
- Case 展示流程维持可运行，不继续建设其完整 Agent benchmark。

**Non-Goals:**

- 不把 KB Recall@K 强制放进完整 Agent 执行。
- 不统一 promptfoo、Harbor、Locust 的全部报告格式或替换这些框架。
- 不修改线上 `/api/chat`、SSE 事件和 Agent Profile 行为。
- 不为测试用例生成增加新的数据集、指标或完整 workflow benchmark。

## Decisions

### 1. 公共评测运行能力位于 `evals.agent`，Harness 只提供稳定执行 API

新增 `evals.agent.runtime`，定义公共 `AgentEventCollector` 和 `AgentRunResult`。BrowseComp 直接使用它；Harbor 在公共结果之外保留 benchmark 要求的 trajectory adapter。

不把评分、数据集和 Harbor trajectory 放入 `noesis`，避免 Harness 依赖具体 benchmark。

### 2. RAG 分为 Retrieval Eval 与 Agentic RAG Eval

`evals.kb` 继续直接调用 `KbRetrievalService`，评价召回与排序。新增 `evals.agent.rag`，通过 `GeneralQAAgent` 运行以下链路：

```text
dataset query
  → GeneralQAAgent
  → create_noesis_agent / stream_agent_events
  → search_knowledge_base tool
  → noesis.runtime.deps KB binding
  → KbRetrievalService
  → tool/source/final-answer metrics
```

Agentic RAG 最小指标包括完成状态、是否调用 KB Tool、期望来源命中率和最终回答。数据集使用 JSONL，并允许样本提供 `collection_names`、`expected_sources`。

### 3. 临时运行依赖使用公开、可恢复的上下文管理器

`noesis.config.checkpointer` 使用 `ContextVar` 提供 `temporary_checkpointer()`；`get_checkpointer()` 优先读取当前 context override。`noesis.runtime.deps` 提供 `temporary_kb_runtime()`，评测结束后恢复此前绑定。

这样不导入 `noesis_server.services.harness_wiring`，Agentic RAG bootstrap 只从平台模块注入 KB 所需的具体 adapter。

### 4. LLM Provider 构建成为公开 API

将低层模型构建能力公开为 `noesis.llm.build_chat_model()`。`get_llm()` 仍负责正常 catalog/runtime snapshot 选择；Harbor 仅在 benchmark 显式传入 provider/model 时调用公开 builder。

### 5. 最小 manifest 统一，不强制统一所有制品

公共结果包含 `run_id`、`suite`、`subject`、`model`、完成状态、耗时、文本、工具统计、usage、错误和 artifacts。BrowseComp/Harbor 可继续生成各自 viewer 所需文件。

## Risks / Trade-offs

- [Agentic RAG 受 LLM 随机性影响] → 与 `evals.kb` 分层报告，不用 Agentic 指标替代 Recall@K。
- [真实集成评测依赖 PostgreSQL、Qdrant 和模型] → 默认 pytest 使用 fake event/service；真实 CLI 由显式命令运行。
- [ContextVar 只隔离当前 async context] → 平台全局 saver 保持原生命周期，评测 override 不跨线程共享。
- [Harbor trajectory 格式特殊] → 只提取公共事件摘要，保留专用 trajectory adapter。
- [旧 Case 能力将废弃] → 只修复阻断演示的路径和回归测试，不迁移到公共 Agent runner。

## Migration Plan

1. 增加 Harness 公开 runtime/LLM API并补单测。
2. 新增公共 Agent collector，迁移 BrowseComp 和 Harbor。
3. 增加 Agentic RAG CLI、fixture 示例和 mock 单测。
4. 修复 Case RAG patch，更新 `backend/evals/README.md` 与入口索引。
5. 运行相关测试和 Harness wheel/import 边界测试。回滚时可整体撤销新评测入口；线上运行不受影响。

## Open Questions

暂无。真实 Agentic RAG 数据集规模和 LLM Judge 可在取得稳定基线后另行扩展。
