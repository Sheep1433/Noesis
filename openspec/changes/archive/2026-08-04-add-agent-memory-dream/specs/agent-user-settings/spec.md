## ADDED Requirements

### Requirement: 设置页 SHALL 提供记忆整理与检索入口

设置页 memory 区域 SHALL 允许用户选择日期手动触发整理、查看最近 L2 文件，并按关键词检索跨会话记忆。整理和检索过程中 SHALL 展示加载状态，失败时 SHALL 展示可理解的错误且不覆盖现有记忆。

#### Scenario: 手动触发整理
- **WHEN** 用户选择日期并点击“整理记忆”
- **THEN** 页面 SHALL 调用认证 API 并展示生成条目数和文件状态

#### Scenario: 展示记忆检索结果
- **WHEN** 用户输入关键词执行搜索
- **THEN** 页面 SHALL 展示日期、分类、摘要与来源标识，不直接展开全部原始聊天
