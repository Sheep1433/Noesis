## ADDED Requirements

### Requirement: 系统 SHALL 按自然日整理跨会话记忆

系统 SHALL 按当前用户和指定时区查询目标自然日内未删除会话的已完成 user/assistant 文本消息，并将可用信息整理到 `memory/YYYY-MM-DD.md`。系统 MUST 排除 reasoning、工具原始输出、错误、流式中和已删除消息。

#### Scenario: 手动整理一天的消息
- **WHEN** 已认证用户触发 2026-07-27 的记忆整理
- **THEN** 系统 SHALL 汇总该用户当天所有符合条件会话并写入 `memory/2026-07-27.md`
- **AND** 其他用户和其他日期的消息 SHALL NOT 出现在文件中

#### Scenario: 当天没有可整理消息
- **WHEN** 目标日期没有符合条件的消息
- **THEN** 系统 SHALL 返回成功的空结果且不得生成虚假记忆条目

### Requirement: 每日记忆 SHALL 幂等且可追溯

每条记忆 SHALL 包含稳定条目标识、分类、摘要、关键词和至少一个来源 session_id/message_id。重复整理同一日期 SHALL 重建同一目标文件，不得重复追加相同条目；写入失败 SHALL 保留旧文件。

#### Scenario: 重复整理同一天
- **WHEN** 输入消息未变化且同一天连续整理两次
- **THEN** 两次得到的条目标识和内容 SHALL 一致且文件中无重复条目

#### Scenario: 读取记忆来源
- **WHEN** 用户使用合法 session_id/message_id 请求来源上下文
- **THEN** 系统 SHALL 仅返回该用户拥有的来源消息及有限相邻消息

### Requirement: 系统 SHALL 提供受限的跨会话记忆检索

系统 SHALL 支持按查询词、日期范围、分类和数量限制搜索当前用户的 L2 条目，并返回摘要、匹配分数、日期及来源标识。空查询、非法日期范围和超限参数 SHALL 返回可理解的校验错误。

#### Scenario: 搜索历史记忆
- **WHEN** 用户搜索与多个历史会话相关的关键词
- **THEN** 系统 SHALL 返回按相关度和日期排序的有限条目，而非整篇每日文件

#### Scenario: 用户隔离
- **WHEN** 用户执行记忆搜索
- **THEN** 搜索范围 SHALL 只包含该用户的 memory 目录

### Requirement: 系统 SHALL 自动补写上一日记忆

应用内记忆调度器 SHALL 周期性检查用户上一自然日是否已成功整理；未成功时触发一次整理，失败后保留重试能力且不得阻止聊天服务启动。

#### Scenario: 自动整理失败
- **WHEN** 某用户上一日整理发生临时错误
- **THEN** 系统 SHALL 记录失败并在后续周期重试
- **AND** 其他用户与聊天请求 SHALL 继续运行
