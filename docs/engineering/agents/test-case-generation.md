# 测试用例生成工程设计

> 状态：Current
> OpenSpec：`agent-profiles`、`offline-evals`

## 1. 场景

TEST_CASE_QA 将需求文档和历史用例转换为结构化测试点、场景树与最终用例。该流程是独立 LangGraph workflow，不走普通 ReAct tool outcome 协议。

## 2. 运行流程

```text
requirements
  → requirement RAG
  → test-point generation
  → user review / HITL
  → historical-case RAG
  → case generation
  → export
```

`CaseCoordinator` 管理阶段状态与 resume。阶段事件使用 `phase-*`，不伪装成 `tool-output-available`。HITL 恢复必须继续原 graph/thread 状态。

## 3. 数据边界

- 用户上传文件进入会话/任务可访问的 staging。
- requirement RAG 与历史用例 RAG 使用显式 collection 和文档范围。
- 场景、测试点和用例使用结构化 schema，不以 Markdown 解析结果作为唯一数据源。
- 导出只读取已确认终态数据。

## 4. 评测

`backend/evals/case/` 分两阶段：

- 阶段 A：测试点/场景生成，关注 L0 合法性、coverage 和名称召回。
- 阶段 B：RAG 检索，关注 Recall@K、Hit@K 和 MRR@K。

数据集与指标变更必须有明确记录，默认单测不访问外部 LLM。

## 5. 代码入口

- Coordinator：`backend/packages/noesis-core/src/noesis/agents/case_generate/case_coordinator.py`
- Graph：`backend/packages/noesis-core/src/noesis/agents/case_generate/case_graph.py`
- RAG：`backend/packages/noesis-core/src/noesis/agents/case_generate/rag.py`
- Schema：`backend/packages/noesis-core/src/noesis/agents/case_generate/vo.py`
- 评测：`backend/evals/case/`
