## Why

来源溯源机制在三个环节与主流产品设计脱节，且产生了已被实际观察到的体验缺陷：

1. **正文无上标、面板却分组「引用」**：引用归因含两条非标记通道（裸 URL canonical 兜底、残缺标记 host/标题宽容匹配），模型未按协议输出标记时，正文普通链接仍会把来源升格为「引用」，面板声称引用了、正文却没有任何引用痕迹，「正文上标序号与面板序号一一对应」的不变量在用户眼前断裂（2026-09-04 用户报告并经代码定位）。
2. **工具输出携带门禁噪音**：web 工具每条结果输出恒为 `citable: True` 的布尔字段、KB 工具输出 `citable` 门禁字段——引用的决定发生在模型输出阶段，模型从不需要该字段做决策（prompt 约定只要求逐字复制 url / citation_ref），该字段是对模型上下文与工具输出预算的纯噪音。主流对照：Perplexity `search_results` 每条仅 `id / url / title / date / snippet`，无任何资格标记字段。
3. **文件交付场景引用断号**：深度研究报告写入 workspace 文件，报告内 `[citation:...]` 标记在文件预览中降级为无编号「·」上标（渲染未传入弧引用索引），知识库徽章点击无响应——报告内上标与来源面板编号无法对应。

目标：引用判定收紧为只认模型输出的完整引用标记（对齐 Perplexity「标记 prompt-dependent、检索列表才是事实来源」的确定性哲学），工具供料去噪，文件预览编号对齐。保留两项既有增强（引用优先编号、「引用 M · 共检索 N」分组，2026-09-04 用户裁决保留）。

非目标：不引入平台侧 claim-to-source binding（既有决策不变）；不改 `[citation:标题](ref)` 协议语法与共享 prompt 约定；不做跨弧去重；不改消费端来源面板的平铺结构与分组标题。

## What Changes

- **检索工具供料去噪**：`web_search` / `web_fetch` 归一化结果与 `search_knowledge_base` 输出行不再携带 `citable` 字段；KB 无稳定版本身份（`identity_status != versioned`）的命中不再输出 `citation_ref`（模型侧依据既有 prompt 约定「工具结果没有提供来源时不添加引用」自然不引用）。
- **注册层门禁收敛**：`message_builder` 登记 retrieval part 时删除 `citable` 布尔过滤，可引用性准入完全由 `EvidenceEnvelope` 身份校验承担（KB 证据缺 collection/document/version/segment 任一即拒收）——该过滤与 envelope 校验本就冗余，删除后行为不变且拒收原因可观测（`invalid_evidence_envelope`）。
- **引用判定收紧（BREAKING 展示语义）**：研究弧引用子集只认完整 `[citation:标题](ref)` 标记 ref 精确命中（web 用 canonical URL、KB 用 kb ref）；删除裸 URL canonical 兜底与残缺标记宽容匹配两条通道。归因文本无完整标记时面板降级为仅「共检索 N」。裸 URL / 残缺标记命中的来源归入「其他检索来源」。
- **报告文件预览编号与 KB 点击**：`FilePreview` 支持可选 `citationIndex` 渲染环境与 KB 徽章点击路由；研究弧数据新增 write_file/edit_file 写入文件路径记录，chat 页按「预览文件 → 所属弧」传入弧引用优先编号索引，报告内上标与弧来源面板同号。
- **兼容性**：工具输出 JSON 字段减少（模型可见输出纯减噪，无消费方依赖 `citable`）；存量落库消息不受影响（retrieval part 从未持久化 citable）；引用数字普遍变小属预期语义修正（裸 URL 不再升格），非数据丢失。无 API/SSE 破坏性变更。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `platform-chat`: 研究弧来源聚合展示的引用判定收紧（只认完整标记精确命中）；新增报告文件预览的引用编号渲染与 KB 徽章点击路由要求。
- `knowledge-base`: KB 检索工具输出的引用供料要求（无稳定身份命中不输出 citation_ref，输出不含可引用性布尔门禁字段）。

## Impact

- 后端：`noesis/agents/tools/web_search_tool.py`（`_normalize_web_result` 去 citable）、`noesis/agents/tools/kb_search_tool.py`（`_format_hits` 去 citable、citation_ref 条件输出）、`noesis/chat/message_builder.py`（删除 citable 过滤行）。
- 前端：`views/chat/researchArcs.ts`（`collectCitationSignals` / `entryIsCited` 收紧、写入文件路径提取）、`views/chat/chat.vue`（文件 → 弧引用索引映射）、`views/chat/SessionContextPanel.vue`（citationIndex 透传）、`components/FilePreview/index.vue`（渲染环境与 KB 点击）。
- 测试：`backend/tests/test_web_search_tool.py`、`test_kb_search_tool.py`、`test_message_builder.py`；`frontend/__tests__/researchArcs.test.ts`、`filePreview.test.ts`。
- 文档：决策记录新增（含被否方案：保留裸 URL 兜底、服务端算法归因）。
