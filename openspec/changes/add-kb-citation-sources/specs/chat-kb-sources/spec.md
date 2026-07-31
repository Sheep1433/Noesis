## ADDED Requirements

### Requirement: Citation SHALL 由 Prompt 生成普通 Markdown

系统 SHALL 通过共享 system prompt 要求 Agent 在普通 Markdown 回答中生成来源引用。Web 来源 SHALL 使用工具返回的原始 HTTP(S) URL；KB 来源在没有安全 permalink 时 SHALL 使用编号和参考资料列表。系统 SHALL NOT 使用 typed answer segment、text annotation、虚拟 Tool 或正文后处理生成引用。

#### Scenario: 模型引用网页

- **WHEN** 回答使用 `web_search` 或 `web_fetch` 返回的事实
- **THEN** 模型 SHOULD 在事实附近输出 `[来源标题](原始 URL)`
- **AND** SHALL NOT 输出内部 evidence ID

#### Scenario: 工具没有提供来源

- **WHEN** 工具结果不包含可识别来源
- **THEN** 模型 SHALL NOT 编造引用
- **AND** MAY 明确说明依据不足

### Requirement: Retrieval results SHALL NOT 冒充 cited sources

平台 MAY 持久化并折叠展示工具返回的 retrieval results，但 SHALL NOT 将 Top-K、score 或全部检索结果自动称为“引用”或“答案依据”。平台 SHALL NOT 解析模型 Markdown 反推 claim-to-source binding。

#### Scenario: 检索后模型没有引用

- **WHEN** 工具返回检索结果但最终正文没有引用
- **THEN** 正文 SHALL 保持模型原始输出
- **AND** retrieval results MAY 继续作为“本轮检索结果”展示
