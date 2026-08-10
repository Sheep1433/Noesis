## ADDED Requirements

### Requirement: 平台聊天 SHALL 以普通文本交付 citation

引用编号 SHALL 作为普通 Markdown text part 的内容，经现有 `text-start`、`text-delta`、`text-end` 和 `finish` 交付。平台 SHALL NOT 增加 citation structured response、citation 专用终态文本或第二份 citation annotation。

#### Scenario: 流式输出带 Markdown 引用的回答

- **WHEN** 模型逐 token 生成正文编号和参考资料列表
- **THEN** 客户端 SHALL 按原有 text delta 顺序展示
- **AND** 终态消息 SHALL 与流式正文完全一致

### Requirement: 平台 MAY 独立持久化 retrieval results

平台 MAY 使用独立 retrieval part 和 `retrieval-results-available` 交付工具来源，供恢复及来源抽屉展示。retrieval part SHALL NOT 声称其中每条结果都被最终答案引用。

#### Scenario: 刷新恢复研究回答

- **WHEN** 带 Markdown 引用的回答在生成中刷新
- **THEN** 普通 text snapshot SHALL 恢复已经生成的引用文本
- **AND** retrieval part SHALL 独立恢复

### Requirement: Citation 上标 SHALL 可确定性恢复

平台 SHALL 在同一 assistant message 中保存原始 Markdown text 和 retrieval parts。客户端 SHALL 使用这两类权威数据确定性重建同一编号、source type 和跳转目标；不持久化第二份答案或依赖流式时内存 annotation。

#### Scenario: 刷新带引用的已完成回答

- **WHEN** 客户端重新加载已完成的 assistant message
- **THEN** `[n]` SHALL 继续显示为一个可点击上标
- **AND** 点击目标 SHALL 与首次流式生成完成时一致
