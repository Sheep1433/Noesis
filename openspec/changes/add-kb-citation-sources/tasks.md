## 0. 发布顺序（必读）

- [ ] 0.1 **前后端同 PR / 同发布**：后端落库 `citations` 不得早于前端 `normalizeParts` 解析
- [ ] 0.2 实现开始时同步 `docs/prd/platform/SSE流式数据设计.md` §2.2（`citations-available`，snake_case）

## 1. 引用域模型与收集器

- [ ] 1.1 新增 `citations/models.py`：`Citation` + UUID5 `citation_id`（golden 单测）
- [ ] 1.2 新增 `registry.py`：`CitationCollector`（登记上限 50、去重、`finalize`）
- [ ] 1.3 新增 `resolve.py`：剔除 fenced code、`\[(\d{1,3})\](?!\()`、仅顶层 text merged_text
- [ ] 1.4 新增 `context.py` + `qa_service` 内 `_finalize_citations`（顺序：flush → finalize → to_dict → stop 文案）
- [ ] 1.5 `AssistantMessageBuilder.merged_text_content()`：**仅** `type=text` 且无 `parent_task_call_id`
- [ ] 1.6 `test_citation_collector.py`、`test_citation_resolve.py`、`test_citation_id.py`

## 2. 知识库工具与检索字段

- [ ] 2.1 修复 `_doc_to_hit`：`point_id` → `VectorStorage.hash_to_uuid(content_hash)` → 空；新增 `chunk_index`
- [ ] 2.2 `_format_hits`：**主线程**登记 + `shard_id`/`chunk_index`/`citation_index`（禁止 worker 内登记）
- [ ] 2.3 `test_kb_search_tool.py` + **`test_kb_retrieval_hybrid_shard_id.py`（hybrid 集成，真实或 fixture Collection）**
- [ ] 2.4 `qa_service`：三路径调用 `_finalize_citations`（finish / stop_chat / disconnect）

## 3. 消息构建与 SSE 桥接

- [ ] 3.1 `message_builder.py`：`CitationsPart`、`append_citations`、`_part_from_dict`
- [ ] 3.2 `langgraph_sse.py`：**仅正常 finish** 发 `citations-available`（snake_case）；无 `text-end` 时序见 design §4
- [ ] 3.3 golden 测试与现网 `message_id` / `text_delta` 风格一致
- [ ] 3.4 `test_stop_chat_finalize.py`：stop 后 DB 含 `citations` part（不要求 SSE）
- [ ] 3.5 disconnect partial 单测含 citations part

## 4. COMMON_QA 提示词

- [ ] 4.1 `common_qa.py`：`[n]` + 禁止长串 file_name；说明 index 1–50
- [ ] 4.2 确认 collector 生命周期与 `common_react_agent` 无泄漏

## 5. 前端 parts 与 SSE（与 §3 同发布）

- [ ] 5.1 `messageParts.ts`：`CitationsUiPart`、`normalizeApiContent` 识别 `citations`
- [ ] 5.2 `useSSEStream.ts`：`citations-available`（snake_case）
- [ ] 5.3 `initChatHistory.ts` 历史回放

## 6. 前端引用 UI

- [ ] 6.1 `CitationList` + **`citation_fallback` 明示文案**（非模型逐条引用）
- [ ] 6.2 `CitationSourceDrawer`：`shard_id` 优先；null → document shards fallback
- [ ] 6.3 Markdown 角标：text part、fenced code 外 `[n]`
- [ ] 6.4 `chat.vue`、`groupAssistantParts.ts`

## 7. 文档与观测

- [ ] 7.1 **（阻塞）** 更新 `docs/prd/platform/SSE流式数据设计.md`
- [ ] 7.2 Langfuse：finalize cited 摘要 metadata
- [ ] 7.3 `docs/NOTES.md` 追加本 change 难度与边界摘要（与 design 对齐）

## 8. 验证（阻塞发布）

- [ ] 8.1 `uv run pytest`：citation + kb + SSE golden + stop/disconnect
- [ ] 8.2 `pnpm lint`
- [ ] 8.3 **手工**：`requirement_docs` 种子 → `[n]` + 列表 + 抽屉 → **`/stop` 后刷新仍有列表**
- [ ] 8.4 `test_citation_e2e_prompt.py`：固定含 `[1]` 文本断言 finalize（不依赖 live LLM）
- [ ] 8.5 **验收记录**：≥3 固定问题的 `citation_fallback` 占比写入 PR / 验收备注
