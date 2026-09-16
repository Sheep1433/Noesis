## Context

来源溯源链路（research-source-provenance，已归档）建立了「工具供料 → 模型写标记 → 前端确定性归因 → 弧聚合面板」的架构。竞品调研（2026-09-04，Perplexity Agent API 官方文档 / Gemini grounding 文档 / Anthropic Citations API，见 `docs/research/` 引用溯源研究）确认两条主流流派：模型写标记派（Perplexity）与平台结构化注解派（OpenAI/Gemini）。Noesis 属于前者，但保留了三条归因通道中的两条非标记通道（裸 URL 兜底、残缺标记宽容匹配），产生了「面板声称引用、正文无上标」的脱节；工具输出携带 `citable` 门禁字段则偏离了模型写标记派的供料形态。

关键代码事实：

- 工具输出 JSON 是双 duty 工件：既进模型上下文，又被 `register_tool_retrieval` 解析为 retrieval part。因此工具输出字段 = 模型可见内容 = 注册层数据源。
- `EvidenceEnvelope.validate_identity` 已强制 KB 证据必须有 collection/document/version/segment 四元身份、web 证据必须有 url；`message_builder.py` 的 `raw.get("citable", True)` 过滤与该校验冗余（`citable=False` 的命中必然缺身份、必然被 envelope 拒收）。
- `RetrievalResult.citable`（`identity_status == "versioned"` 且三元 id 齐全）与 envelope 校验的判定集合完全等价（`identity_status` 只有 `versioned` / `legacy_unversioned` 两值，后者至少缺一个 id）。
- 前端与落库 retrieval part 从未持久化 `citable`（envelope `extra="forbid"` 且无该字段），删字段对存量数据零影响。
- `write_file` / `edit_file` 工具 part 的 `input.file_path` 可定位被写入文件，是「文件 → 弧」映射的依据。

## Goals / Non-Goals

**Goals:**

- 引用判定只认完整 `[citation:标题](ref)` 标记 ref 精确命中，消灭「面板引用无正文痕迹」的脱节。
- 工具模型可见输出不含 `citable` 门禁字段；KB 无稳定身份命中不供 `citation_ref`。
- 报告文件预览的上标与所属弧的来源面板同号；KB 徽章在文件预览中可点击回源。

**Non-Goals:**

- 不引入平台侧 claim-to-source binding 或服务端正文解析归因（既有决策：`SHALL NOT` 解析模型 Markdown 反推绑定）。
- 不改 `[citation:标题](ref)` 协议语法、`CITATION_EXTENSION` prompt 约定、引用优先编号与「引用 M · 共检索 N」分组（两项增强经用户裁决保留）。
- 不做跨弧去重、不迁移存量消息、不改消费端面板平铺结构。

## Decisions

### D1 引用判定收敛为「完整标记 ref 精确命中」单通道

`collectCitationSignals` 只保留 `CITATION_REF_RE`（完整 `[citation:标题](ref)`）解析出的 exactKeys；删除 `canonicalUrlsInText` 裸 URL 兜底与 `BARE_CITATION_RE` 残缺标记宽容匹配，`entryIsCited` 退化为 exactKeys 精确查找。

- 为什么不是「保留残缺标记通道」：残缺标记按 host/标题宽容匹配存在已知误命中（决策记录「仅作线索层」），且它依然绕开 ref 精确性——与「标记 ref 必须逐字复制工具结果」的协议背道而驰。完整标记通道已覆盖模型守协议的全部情形；不守协议的输出（漏 ref、只贴 URL）在主流对照（Perplexity）中同样不计为引用。
- 被否方案：保留裸 URL 兜底并把前端渲染层升级为「URL 出现处插入上标」——触碰「不改写模型正文」边界且引入渲染不确定性，否。
- 归因文本范围不变：交付消息顶层正文 + 弧内写入文件内容；`attributionUnavailable` 语义改为「无完整标记」。

### D2 `citable` 字段删除，注册门禁由 envelope 校验独立承担

- web：`_normalize_web_result` 不再注入 `citable: True`（URL 非法结果已在工具层整条丢弃，注册层 envelope 校验 url 必填）。
- KB：`_format_hits` 不再输出 `citable`；`citation_ref` 仅在 `hit.citable`（= identity versioned）时输出。模型对无 citation_ref 的命中依据既有 prompt 约定自然不引用，且此类命中因缺身份必被 envelope 拒收、不入面板——供料与登记两侧对「可引用」的定义收敛为同一个（稳定版本身份）。
- `message_builder` 删除 `citable` 过滤行（保留 `isinstance` 防御），拒收路径统一走 `invalid_evidence_envelope`（可观测计数）。
- 被否方案：保留字段仅从模型侧「隐藏」——工具输出是单工件双消费，无独立通道，强行拆分属过度设计；保留布尔、前端消费——前端零消费是既成事实，不成立。

### D3 文件预览编号：文件路径 → 弧引用索引映射

- `researchArcs.ts` 的 `ArcPanelData` 新增 `writtenFilePaths: string[]`（弧内全部 write_file/edit_file 的 `input.file_path`，纯函数从消息 parts 提取）。
- chat.vue 构建「归一化路径 → 弧 CitationIndex」映射（路径匹配前去掉前导 `/`），SessionContextPanel 以可选 prop 透传给 FilePreview，FilePreview 渲染时传入 `MarkdownInstance.render` 的 env `citationIndex`。
- 归属规则：一个文件被多个弧写过时取**最近写入的弧**（后写覆盖），与「报告以最后一次写入为准」的直觉一致；无归属弧的文件不传索引，保持现行无编号渲染（`unnumberedBadge`）。
- KB 徽章点击：FilePreview 复用 MarkdownPreview 的 `onContentClick` 逻辑（`data-kb-ref` → `KnowledgeBaseDetail` 路由），独立实现不反向依赖 MarkdownPreview 组件。

## Risks / Trade-offs

- [引用数字普遍变小（裸 URL 不再升格）] → 预期语义修正而非数据丢失；面板「共检索 N」不受影响；决策记录明示取舍。
- [模型写非协议引用形式（`[1]` 编号、裸 URL）时引用数低估] → 与 Perplexity 同代价（其官方文档承认 markers prompt-dependent）；prompt 约定已要求 `[citation:...]` 语法，低估优于误报。
- [文件被弧外工具（MCP、手动编辑）写入引用标记时不参与归因] → 现状即如此（归因只认弧内 write_file/edit_file），本变更不扩大也不缩小该范围。
- [KB 未版本化旧文档命中（legacy_unversioned）失去 citation_ref] → 此类命中本就无法稳定回源，不供 ref 符合「不可溯源不引用」原则；模型仍可基于其内容回答，只是不引用。

## Migration Plan

单提交合并至 dev：后端三处小改 + 前端三处组件/视图改 + 测试同步。无数据迁移、无 API/SSE 变更；回滚即 revert 提交。前端展示语义变化（引用数变小、文件预览出现编号）随部署即时生效，无需灰度。

## Open Questions

（无——引用优先编号与分组保留、判定收紧口径均经用户 2026-09-04 裁决。）
