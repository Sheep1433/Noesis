## Why

知识库检索已返回 `file_name`、`header_path` 等元数据，但智能问答回答仅依赖 LLM 在正文中手写来源，用户需展开 `search_knowledge_base` 工具输出才能核对片段；刷新后也无法结构化回放「回答引用了哪些文档」。这与 Perplexity / ChatGPT 的「文内角标 + 文末来源列表 + 点击打开原文」体验差距明显，影响企业问答的可信度与审计需求。

**难度说明（详见 `design.md` §难度评估）**：工程层（SSE、落库、抽屉）难度中等；**真正难的是「回答句子 ↔ 具体分片」的语义绑定**——首版依赖 LLM 写 `[n]`，属中低置信体验，fallback 仅为妥协。首版交付目标是 **结构化来源展示 + 可点击原文 + 刷新可回放**，不承诺审计级逐句归因。

## What Changes

- 新增 **知识库引用（KB Citations）** 能力：登记 → 正文 `[n]` cited 子集 → SSE（**snake_case**）→ `citations` part 落库 → 来源列表 + 原文抽屉。
- 新增 SSE **`citations-available`**：在 **`finish` 之前**发出（有 `text-end` 则在其后，否则在末业务帧后）。
- **completed / partial / stop** 均调用共享 finalize 落库；**仅正常 finish** 保证 SSE `citations-available`（stop/断连后须**刷新**看来源列表）。
- 扩展工具 hit：`shard_id`（可 retrieve point id 或 null）、`chunk_index`、`citation_index`；**禁止**裸 `content_hash` 作 shard_id。
- `citation_id`：UUID5 确定性算法；角标解析仅 **text parts**、剔除 fenced code、index 1–999 写法但登记上限 50。
- 前后端 **同版本发布**；同步 `docs/prd/platform/SSE流式数据设计.md`。
- 验收：`requirement_docs` 种子 + **hybrid 集成测** + stop 后刷新；记录 `citation_fallback` 占比。

## Capabilities

### New Capabilities

- `chat-kb-citations`：引用模型、collector、partial 落库、cited 解析、UI 与抽屉 fallback。

### Modified Capabilities

- `platform-chat`：SSE `citations-available`（snake_case）、`CitationsPart`、partial/stop 落库、PRD 同步要求。
- `agent-common-qa`：shard_id/chunk_index 透出、collector 挂载、提示词 `[n]` 策略。

## Impact

| 区域 | 路径 |
|------|------|
| 引用域 | `backend/domain/chat/citations/` |
| 检索修复 | `backend/kb/retrieval/service.py`（`_doc_to_hit` point id） |
| 落库三路径 | `qa_service.py`（finish / stop_chat / disconnect） |
| SSE | `langgraph_sse.py`（snake_case） |
| 前端 | `useSSEStream.ts`、`messageParts.ts`（须与后端同 PR） |
| PRD | `docs/prd/platform/SSE流式数据设计.md` |
| 验收种子 | `backend/kb/seed/requirement_docs/` |
