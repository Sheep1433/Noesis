## MODIFIED Requirements

### Requirement: COMMON_QA / GeneralQAAgent

`COMMON_QA` SHALL 使用 GeneralQAAgent：以知识库 RAG 工具为主（hybrid 检索链路见 `knowledge-base`），MAY 结合会话附件。**SHALL NOT** 默认挂载完整 SuperAgent Skills/子 Agent 栈。

当知识库 hit 提供 `source_ref` 时，GeneralQAAgent SHALL 仅使用工具返回的 `[[source:<source_ref>]]` 标记来源，SHALL NOT 自行编造 source_ref、文件名角标或跨工具调用的数字索引。没有足够依据时 Agent MAY 不引用，但 SHALL NOT 把未见过的来源写成已引用。

#### Scenario: 路由到通用问答

- **WHEN** 流式请求 `qa_type=COMMON_QA`
- **THEN** 系统 SHALL 装配 GeneralQAAgent 而非 SuperAgent

#### Scenario: 只引用工具返回 token

- **WHEN** 工具只返回 `kb_a1` 与 `kb_b2` 两个 source_ref
- **THEN** assistant 正文中的 source token SHALL 仅引用这两个值或不引用，SHALL NOT 生成其它 source_ref
