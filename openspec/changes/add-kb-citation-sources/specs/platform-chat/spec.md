## ADDED Requirements

### Requirement: 平台聊天 SHALL 以普通文本交付 citation

引用 SHALL 作为普通 Markdown text part 的内容，经现有 `text-start`、`text-delta`、`text-end` 和 `finish` 交付。平台 SHALL NOT 增加 citation structured response、`text-annotation-added` 或 citation 专用终态文本。

#### Scenario: 流式输出带 Markdown 引用的回答

- **WHEN** 模型逐 token 生成正文和 Markdown 链接
- **THEN** 客户端 SHALL 按原有 text delta 顺序展示
- **AND** 终态消息 SHALL 与流式正文完全一致

### Requirement: 平台 MAY 独立持久化 retrieval results

平台 MAY 使用独立 retrieval part 和 `retrieval-results-available` 交付工具来源，供恢复和折叠展示。retrieval part SHALL NOT 声称其中每条结果都被最终答案引用。

#### Scenario: 刷新恢复研究回答

- **WHEN** 带 Markdown 引用的回答在生成中刷新
- **THEN** 普通 text snapshot SHALL 恢复已经生成的引用文本
- **AND** retrieval part MAY 独立恢复
