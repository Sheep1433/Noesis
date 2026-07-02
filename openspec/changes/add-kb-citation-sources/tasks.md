## 0. 发布顺序（必读）

- [ ] 0.1 **前后端同 PR / 同发布**：后端 `3.x` 落库 `citations` part 不得早于前端 `5.x` 解析（或前端先合入 tolerant `normalizeParts`）
- [ ] 0.2 实现开始时同步 `docs/prd/platform/SSE流式数据设计.md` §2.2 增加 `citations-available`（snake_case 字段表）

## 1. 引用域模型与收集器

- [ ] 1.1 新增 `backend/domain/chat/citations/models.py`：`Citation` + UUID5 `citation_id`（golden 单测锁定样例）
- [ ] 1.2 新增 `registry.py`：`CitationCollector`（登记、去重、index 上限 50、`finalize`）
- [ ] 1.3 新增 `resolve.py`：剔除 fenced code、`\[(\d{1,3})\](?!\()`、仅保留已登记 index
- [ ] 1.4 新增 `context.py`：contextvar；新增 `finalize_citations.py` 或 `qa_service` 内共享 `_finalize_citations(builder, collector)`
- [ ] 1.5 `AssistantMessageBuilder.merged_text_content()`：仅合并 text parts
- [ ] 1.6 新增 `test_citation_collector.py`、`test_citation_resolve.py`、`test_citation_id.py`

## 2. 知识库工具与检索字段

- [ ] 2.1 修复 `_doc_to_hit`：`id` = `point_id` → `hash_to_uuid(content_hash)` → 空；新增 `chunk_index` 字段到 `KbSearchHit`
- [ ] 2.2 `kb_search_tool._format_hits`：`shard_id`（可 null）、`chunk_index`、`citation_index`；禁止裸 content_hash
- [ ] 2.3 更新 `test_kb_search_tool.py`、`test_kb_retrieval_service.py`（point_id / chunk_index）
- [ ] 2.4 `qa_service` COMMON_QA：创建/清理 collector；**三路径**调用 `_finalize_citations`（finish、stop_chat、disconnect）

## 3. 消息构建与 SSE 桥接

- [ ] 3.1 `message_builder.py`：`CitationsPart`、`append_citations`、`_part_from_dict`
- [ ] 3.2 `langgraph_sse.py`：finish 前发 `citations-available`（**snake_case**）；处理无 `text-end` 时序
- [ ] 3.3 **不引入 camelCase**；SSE 与落库同形
- [ ] 3.4 `test_langgraph_sse_bridge_contract.py` golden（snake_case `message_id`、`citation_fallback`）
- [ ] 3.5 `test_stop_chat_finalize.py` / disconnect 单测：partial 消息含 `citations` part

## 4. COMMON_QA 提示词

- [ ] 4.1 更新 `common_qa.py`：`[n]` 策略、index 1–50 说明
- [ ] 4.2 `common_react_agent.py` / `qa_service` 确认 collector 透传

## 5. 前端 parts 与 SSE（与 §3 同发布）

- [ ] 5.1 `messageParts.ts`：`CitationsUiPart`、`applyCitationsPart`、`normalizeApiContent` 识别 `citations`
- [ ] 5.2 `useSSEStream.ts`：`citations-available`（读 snake_case，与 `text_delta` 一致）
- [ ] 5.3 `initChatHistory.ts`：历史加载 citations part

## 6. 前端引用 UI

- [ ] 6.1 `CitationList/index.vue`（含 `citation_fallback` 文案）
- [ ] 6.2 `CitationSourceDrawer/index.vue`：`shard_id` 优先；null 时 document shards fallback + `chunk_index`/`header_path`
- [ ] 6.3 Markdown 角标：text part、跳过 fenced code 内的 `[n]`
- [ ] 6.4 `chat.vue` 渲染；`groupAssistantParts.ts` citations 顶层

## 7. 文档与观测

- [ ] 7.1 **（P0）** 更新 `docs/prd/platform/SSE流式数据设计.md` §2.2 `citations-available` 事件表与示例 JSON
- [ ] 7.2 Langfuse：finalize cited 摘要 metadata

## 8. 验证与 eval

- [ ] 8.1 `uv run pytest tests/test_citation_*.py tests/test_kb_search_tool.py tests/test_langgraph_sse_bridge_contract.py tests/test_stop_chat_finalize.py -q`
- [ ] 8.2 `pnpm lint`
- [ ] 8.3 **手工验收**：将 `backend/kb/seed/requirement_docs/` 入库测试 Collection → COMMON_QA 提问 → `[n]` + 列表 + 抽屉 → `/stop` 后刷新仍有 citations
- [ ] 8.4 **（非阻塞）** 新增 `test_citation_marker_integration.py` 或 `evals/` 样例：mock 含 `[1]` 的 assistant 文本，断言 finalize 产出 expected items
