## MODIFIED Requirements

### Requirement: COMMON_QA / GeneralQAAgent

`COMMON_QA` SHALL 使用 GeneralQAAgent，以知识库 RAG 工具为主，并保持普通 Markdown 文本输出和 token streaming。系统 SHALL NOT 为 citation 向 `create_agent` 传递 structured `response_format`，也 SHALL NOT 创建提交最终答案的虚拟 Tool。

当回答使用检索事实时，system prompt SHALL 要求：Web 来源以 `[标题](原始 URL)` 紧邻事实；KB 来源使用 `[n]` 并在文末 `### 参考资料` 列出对应文件、Collection 和可用定位。Agent SHALL NOT 输出内部 evidence/document/segment ID，也 SHALL NOT 编造工具未提供的来源。

#### Scenario: Web 检索后正常流式回答

- **WHEN** GeneralQAAgent 使用 Web 结果回答事实问题
- **THEN** 答案 SHALL 通过普通 `text-delta` 流式输出
- **AND** 使用的来源 SHOULD 以原始 URL Markdown 链接出现在相应事实附近
- **AND** 系统 SHALL NOT 等待 structured response 才交付正文

#### Scenario: KB 检索后编号引用

- **WHEN** GeneralQAAgent 使用知识库片段回答
- **THEN** 正文 SHOULD 使用简短编号引用
- **AND** 文末 SHOULD 提供编号一致的 `### 参考资料`

### Requirement: SUPER_AGENT_QA 使用同一 Prompt citation 规则

SuperAgent 主 Agent SHALL 使用与 COMMON_QA 相同的普通 Markdown citation 规则。子 Agent 返回给主 Agent 的研究小结 SHOULD 保留原始来源 URL。系统 SHALL NOT 为不同模型维护 citation provider allowlist。

#### Scenario: 主 Agent 汇总 Web 调研结果

- **WHEN** SuperAgent 使用主 Agent 或子 Agent 返回的 Web 来源生成最终报告
- **THEN** 最终回答 SHOULD 使用原始 URL 的 Markdown 引用
- **AND** SHALL 保持普通文本流式输出，不启用 citation structured response
