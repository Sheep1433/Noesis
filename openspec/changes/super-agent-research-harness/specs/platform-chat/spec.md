## ADDED Requirements

### Requirement: Research report SHALL project only used citations by default

网页聊天 SHALL 在 `SUPER_AGENT_QA` research run 完成后展示结构化报告和实际使用的 Sources used。候选、重复、淘汰和工具失败详情 SHALL 默认属于 research trace/debug 数据，SHALL NOT 直接展开为聊天正文。

#### Scenario: Report with used sources

- **WHEN** 报告中的 citation 已绑定到 Evidence
- **THEN** 来源区域 SHALL 展示对应 source identity、标题、链接和必要摘要，并 SHALL 能从正文引用定位到来源

#### Scenario: Unused candidates

- **WHEN** trace 中存在未被报告引用的候选来源
- **THEN** 默认聊天消息 SHALL NOT 将其作为最终参考文献展示

### Requirement: Citation completeness SHALL be visible without blocking partial output

系统 SHALL 对无法绑定 evidence 的引用、未验证来源和 research gap 记录可见状态。报告 MAY 以 partial 状态返回，但 SHALL 不把缺失证据伪装为已验证引用。

#### Scenario: Partial research report

- **WHEN** 子任务失败或必要来源无法抓取，但主 Agent 仍生成部分报告
- **THEN** 用户 SHALL 能看到 partial/gap 语义和缺失原因
- **AND** 已验证引用 SHALL 继续可点击和可追溯
