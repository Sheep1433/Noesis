## MODIFIED Requirements

### Requirement: COMMON_QA / GeneralQAAgent

`COMMON_QA` SHALL 使用 GeneralQAAgent：以知识库 RAG 工具为主（hybrid 检索链路见 `knowledge-base`），MAY 结合会话附件。系统 SHALL NOT 默认挂载完整 SuperAgent Skills/子 Agent 栈。

当 Harness/runtime 将知识库工具的稳定 evidence identity 登记为 run-local `evidence_id` 后，GeneralQAAgent SHALL 通过 typed answer segments 输出正文与 `cited_evidence_ids` 的结构化绑定。Agent SHALL NOT 在用户可见正文中输出 `[[source:...]]`、`[ID:n]`、文件名角标或其它自定义 citation marker，也 SHALL NOT 引用本轮 retrieval manifest 之外的 evidence id。

GeneralQAAgent system prompt SHALL 注入当前 run manifest 可用 evidence id 及稳定展示信息，明确要求 `segments[{text,cited_evidence_ids}]`、无依据时输出空 binding，并禁止在 `text` 中复述 evidence id 或 marker。Prompt 只负责引导；provider typed schema 与平台 membership 校验仍是权威约束。COMMON_QA citation 只有在当前 provider 通过固定样本 structured binding spike 后才能启用；否则 SHALL 降级为纯文本回答与 retrieved-only results。

#### Scenario: 路由到通用问答

- **WHEN** 流式请求 `qa_type=COMMON_QA`
- **THEN** 系统 SHALL 装配 GeneralQAAgent 而非 SuperAgent

#### Scenario: 结构化绑定本轮 evidence

- **WHEN** runtime manifest 只登记 `ev_a1` 与 `ev_b2` 两个 evidence id
- **THEN** typed answer segments 中的 `cited_evidence_ids` SHALL 仅包含 `ev_a1`、`ev_b2` 或为空
- **AND** 用户可见正文 SHALL 不包含内部 evidence id 或 citation marker

#### Scenario: Provider 无法生成 typed binding

- **WHEN** 当前 provider 不支持或未通过 structured answer schema 校验
- **THEN** 系统 MAY 交付纯文本回答与 retrieval results
- **AND** SHALL NOT 通过 prompt marker 或 Top-K 自动生成 cited annotation

### Requirement: SUPER_AGENT_QA 主 Agent SHALL 支持 typed citation binding

当 SuperAgent 主 Agent 直接调用 `web_search` 或 `web_fetch`，且当前 provider 已通过 structured binding 门禁时，系统 SHALL 使用与 COMMON_QA 相同的 `segments[{text,cited_evidence_ids}]` 契约生成正文引用。子 Agent 内部检索结果不在本要求范围内，除非其 evidence 已进入主 run retrieval manifest。系统 SHALL NOT 从工具调用顺序、Top-K 或正文 URL 猜测引用。

#### Scenario: 智能体直接检索后生成引用

- **WHEN** `qa_type=SUPER_AGENT_QA` 的主 Agent 直接检索网页并绑定本轮 evidence id
- **THEN** 最终正文 SHALL 产生 `url_citation` annotation
- **AND** provider 未通过门禁时 SHALL 保留纯文本与 retrieved-only results
