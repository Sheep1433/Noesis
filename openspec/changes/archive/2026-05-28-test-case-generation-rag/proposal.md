## Why

测试用例生成在**阶段 B（按测试场景并行、为场景内各测试点生成用例）**依赖 RAG 为 LLM 提供上下文。当前实现按**每个测试点**召回且依赖 LLM 输出的 `chunk_indexes`，与预期不符：同场景测试点应共享一次场景级 RAG。本变更改为**按测试场景**做双路 hybrid 召回（`requirement_docs` 需求文档 + `test_case_docs` 历史用例），再在该场景内并行生成各测试点用例；**不再**要求测试点携带 `chunk_indexes`。

同时需明确领域模型：**测试点**是测试内容的标题级描述；**测试用例**是对测试点的展开，含前置条件、测试步骤、预期结果等。

## What Changes

- 新增能力 **`test-case-generation-rag`**：规定阶段 B 多知识库召回通道、检索模式（向量 + BM25 + RRF）、prompt 拼接顺序与测试点/用例语义边界。
- 扩展 `case_graph` 用例生成路径：在 `_generate_cases_parallel` 中按通道召回并拼接 `## 参考文档片段` 等区块；配置项声明各 collection 名称。
- **不**在本变更重做阶段 A 的「从全库选文档」流程（由用户上传 + 入库承担）。
- **非 BREAKING**：不改变 `test-case/resume`、SSE 事件与 `TestCaseState` 对外字段语义；仅增强召回内容与 prompt。

## Capabilities

### New Capabilities

- `test-case-generation-rag`：测试用例生成阶段的多知识库 RAG 与 prompt 组装。

### Modified Capabilities

- （无）`test-assistant-mindmap-workflow` 前端状态机不变；与 `knowledge-base` 入库契约复用既有 collection 配置。

## Impact

- **后端**：`backend/agent/case_generate/case_graph.py`、`rag_retriever.py`（或抽取 `CaseRagContextBuilder`）、`config/env.py`。
- **知识库**：`requirement_docs`（种子文档与用户上传）、`test_case_docs`。
- **评测**：依赖 `test-case-agent-eval` 中阶段 B 的 `rag_hit_at_k` 指标验收本变更。
- **文档**：`docs/prd/agent-test-case/测试用例生成设计.md` §4 RAG 与实现对齐。
