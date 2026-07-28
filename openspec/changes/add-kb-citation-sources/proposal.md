## Why

知识库问答当前只能展示工具输出，不能回答“这段答案具体依据哪份文档”。现有 change 预设了 `[[source:...]]`、SourcesPart 和前端 marker 解析，但这会把引用身份塞进自然语言，并重复实现 OpenAI Responses 已验证的 typed annotation 机制。

本变更忽略 Noesis 旧的引用预埋设计，以 `docs/research/kb-citation-source-tracing.md` 中的 OpenAI 形态为产品与协议基线：**答案引用与检索结果分层、引用作为 text annotation、流式文本与 annotation 分事件交付**。Noesis 只实现自建知识库无法由 OpenAI 托管能力代替的部分：证据身份、持久化、权限回查和校验。

## What Changes

- assistant 正文继续使用 `text` part，但新增 typed `annotations[]`；知识库引用使用 `type=kb_citation`，不在正文中插入 `[[source:...]]`、`[ID:n]` 等 marker。
- 新增独立 `retrieval` part，保存工具本轮返回的 retrieval manifest。`text.annotations` 是 cited 子集，`retrieval.results` 是 retrieved 集合，两者不得混用。
- GeneralQAAgent 以结构化回答片段绑定本轮局部 evidence id；平台校验后投影为正文 offset annotation。若当前模型无法稳定输出结构化绑定，则该轮不生成 cited annotation，禁止回退到 marker 或 Top-K 冒充引用。
- `evidence_id` 由 run 级 retrieval manifest 统一分配；KB 工具只提供稳定的 document/version/segment identity，避免多次或并行工具调用各自从 `S1` 开始造成冲突。
- SSE 对齐 OpenAI Responses 的事件分层：沿用 `text-delta`，新增 `text-annotation-added`；检索结果经独立 `retrieval-results-available` 事件交付。终态消息快照为权威来源。
- 引用定位采用 `document_id + document_version_id + segment_id`，并可附 page/char/bbox/header 等 locator；数组序号、chunk index 和 Qdrant point id 不承担长期公开身份。
- 新增 message-scoped citation resolve API，点击引用时重新校验 message ownership 与知识库权限，仅返回最小必要 excerpt/viewer 信息。
- UI 默认只显示 cited annotations；retrieved-only 结果仅在折叠的“本轮检索结果”中展示。
- 为 retrieval manifest、excerpt、locator 与消息快照建立可配置硬上限；超限时确定性截断并记录观测，不允许消息体无界增长。

## Capabilities

### New Capabilities

- `chat-kb-sources`: 规定 OpenAI-style text annotations、retrieval manifest、引用校验、回源和展示行为。

### Modified Capabilities

- `knowledge-base`: 检索结果 SHALL 提供稳定的文档、版本、分段身份与可解析 locator。
- `agent-profiles`: COMMON_QA SHALL 产出结构化 answer-to-evidence binding，不输出引用 marker。
- `platform-chat`: text part、消息持久化与 `/api/chat` SSE SHALL 支持 annotation patch 和独立 retrieval results。

## Impact

| 区域 | 影响 |
|------|------|
| Harness | KB tool 返回结构化 evidence；模型侧增加 typed answer binding 适配，不依赖平台消息模型 |
| 平台 | builder 校验 binding 并写入 text annotations；独立持久化 retrieval part；Delivery 投递 annotation/retrieval 事件 |
| KB | 补正式 document/version/segment identity 与受控 resolve 能力 |
| 前端 | 按 annotation offset 渲染可点击角标；retrieval results 作为独立折叠区 |
| 安全 | 回源时重新验证 message、session、collection/document 权限，不信任客户端 locator |
| 兼容 | 新字段与 SSE 事件均为增量扩展；旧客户端忽略 annotations/retrieval 后仍可显示纯文本 |

启用门禁：COMMON_QA citation 只有在目标 provider 通过固定样本 structured binding spike 后才能启用。未通过时保留纯文本回答与 retrieved-only results；这属于受控降级，不重新引入正文 marker。

## Non-Goals

- 不接入或依赖 OpenAI 托管 File Search；“OpenAI 形态”指输出对象与事件语义，不是复制其托管存储。
- 不保留旧 `source_ref`、`[[source:...]]`、SourcesPart 或数字 marker 兼容路线。
- 首版不承诺独立 LLM 语义 verifier、逐字审计级事实判定，也不为历史消息反向生成引用。
- 首版只覆盖 COMMON_QA 的知识库检索，不扩展 Web Search、附件和 SuperAgent 来源。
