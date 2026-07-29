## 1. Evidence 身份与检索契约

- [x] 1.1 盘点现有 KB 文档/分块表与 Qdrant payload，确定 `document_id`、`document_version_id`、`segment_id` 的来源与迁移规则
- [x] 1.2 扩展 hybrid/BM25/vector/rerank hit，完整保留三层身份、locator 与内部 point id
- [x] 1.3 定义 versioned evidence envelope；由 run 级 manifest 使用 run salt + canonical identity 确定派生并原子去重 opaque `evidence_id`，覆盖碰撞、多次/并行调用、检查点恢复与确定重放 schema/golden tests
- [x] 1.4 扩展 `kb_search_tool` 输出 retrieval results；验证 harness 不反向 import 平台 domain
- [x] 1.5 增加重复命中、并行检索、rerank、旧 payload 缺字段和 locator 精确失败测试

## 2. Structured answer binding

- [x] 2.1 用固定中英文样本验证当前 DeepSeek/Qwen 端点的 JSON schema、长答案、无引用回答、多证据绑定和流式能力，记录选择逐段或终态 annotation 的结论；将通过标准设为 COMMON_QA citation 发布门禁
- [x] 2.2 在 GeneralQAAgent 定义 `segments[{text,cited_evidence_ids}]` typed schema 与 provider adapter
- [x] 2.3 更新 GeneralQAAgent system prompt：注入 manifest evidence、要求 typed segments/空 binding、禁止正文 marker，并补 prompt golden tests
- [x] 2.4 将 typed segment/binding 转为统一 harness runtime event；禁止向用户正文输出 `[[source:...]]`、`[ID:n]` 等 marker
- [x] 2.5 实现无 structured output 时的纯文本 + retrieval 降级，不添加 marker fallback 或 Top-K citation
- [x] 2.6 增加未知 evidence id、重复 id、空 segment、provider schema error 与多次工具调用测试

## 3. 消息模型、校验与持久化

- [x] 3.1 扩展 `TextPart.annotations[]` 与 `RetrievalPart` 的严格后端模型、反序列化兼容和前端 tolerant parser
- [x] 3.2 在 `AssistantMessageBuilder` 登记 retrieval manifest，按 segments 零分隔符原样拼接并投影 Unicode code point offset annotation；补中文、emoji、换行、Markdown golden tests
- [x] 3.3 实现 evidence membership、identity、locator、非空/越界 offset 的确定性结构校验与观测指标
- [x] 3.4 覆盖 completed、partial、disconnect、error、HITL pending/resume 的 snapshot 恢复及去重测试
- [x] 3.5 运行容量 spike，确定每次/run results、excerpt、locator 与 assistant JSON byte 硬上限；验证一轮 assistant 仍只落库一行且超限可确定截断

## 4. RunEvent 与 `/api/chat` SSE

- [x] 4.1 新增 `retrieval-results-available` RunEvent/Delivery 映射并锁定 snake_case payload golden
- [x] 4.2 新增 `text-annotation-added`，包含 `text_part_id` 与完整 annotation；确保范围对应文本已进入 builder
- [x] 4.3 分别验证 segment streaming 在 text-end 前登记、终态 provider 在 text-end 后/finish 前登记，以及断连和 stop 不影响 PersistSink 权威快照
- [x] 4.4 更新 `docs/architecture/platform/chat-streaming.md` 的现行事件与时序

## 5. Citation resolve API 与权限

- [x] 5.1 新增 `GET /api/chat/messages/{message_id}/citations/{citation_id}` schema/service/API
- [x] 5.2 从消息 annotation 解析 locator，拒绝客户端提供 collection/document/segment 覆盖值
- [x] 5.3 实现 message/session ownership 与 collection/document/version 权限二次校验
- [x] 5.4 实现精确 version/segment 重新实读与 forbidden/stale/missing 状态；落库 excerpt 仅作快照，不伪装为实读成功
- [x] 5.5 增加伪造 citation id、跨会话、权限撤销、版本删除和 locator 篡改测试

## 6. 前端引用体验

- [x] 6.1 按原始 text part 的 `start_index/end_index` 建立 Markdown annotation range，并渲染可点击角标
- [x] 6.2 点击角标仅调用 message-scoped citation API，展示 excerpt、文档标题与受控 viewer 定位
- [x] 6.3 默认只聚合 cited annotations；retrieved-only results 放入折叠的“本轮检索结果”
- [x] 6.4 覆盖多来源同范围、同来源多范围、Markdown/code/link、复制纯文本、旧消息和刷新回放测试

## 7. 验证与发布

- [x] 7.1 后端 KB、Agent、SSE、PersistSink、HITL 与权限测试通过
- [x] 7.2 前端单测、`pnpm lint` 和 `pnpm build` 通过
- [x] 7.3 用固定 requirement_docs 问题验收 cited/retrieved 分层、无 marker 泄漏、刷新、stop、断连和多次检索
- [x] 7.4 记录 structured binding 成功率、未知 evidence id、无 citation 降级率与 resolve 失败率，再决定是否引入语义 verifier
- [x] 7.5 运行 `openspec validate add-kb-citation-sources`

## 8. Web citation

- [x] 8.1 扩展 evidence manifest，支持以 canonical URL 去重的 Web evidence
- [x] 8.2 让 `web_search`、`web_fetch` 输出 evidence id，并由 SSE bridge 持久化 retrieval part
- [x] 8.3 将 typed binding 投影为 `url_citation`，resolve 返回消息快照与安全外链且不触发服务端重抓
- [x] 8.4 前端解析、展示 Web 引用和 retrieved-only 网页结果
- [x] 8.5 增加回归测试并运行 OpenSpec 校验
