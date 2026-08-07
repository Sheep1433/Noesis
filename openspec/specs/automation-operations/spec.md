# automation-operations Specification

## Purpose
TBD - created by archiving change expand-settings-control-plane. Update Purpose after archive.
## Requirements
### Requirement: 自动化设置 SHALL 支持完整任务编辑与调度预览
用户 SHALL 能创建和编辑任务名称、cron、时区、prompt、`qa_type`、会话绑定、启用状态和投递目标。系统 SHALL 校验表达式并返回人类可读日程摘要与下一次执行时间；无效表达式 SHALL NOT 保存。

#### Scenario: 预览合法日程
- **WHEN** 用户输入合法 cron 与时区
- **THEN** 设置页 SHALL 显示日程摘要和按该时区计算的下一次执行时间

### Requirement: 每次自动化执行 SHALL 产生不可变运行记录
系统 SHALL 为调度触发和手动触发分别创建用户隔离的运行记录，至少包含状态、触发来源、开始/结束时间、耗时、结果摘要、错误分类、投递结果和关联 session/run id。状态 SHALL 遵循 `queued → running → succeeded|failed|cancelled`。

#### Scenario: 查看失败运行
- **WHEN** 自动化 run 失败且用户打开该任务历史
- **THEN** 系统 SHALL 显示失败时间、可行动错误摘要和投递结果且不暴露内部堆栈或 secret

### Requirement: 自动化失败运行 SHALL 可幂等发起重试
用户 SHALL 能对允许重试的 failed/cancelled 运行发起重试；重试 SHALL 创建新运行记录并引用原记录，SHALL NOT 覆盖历史。重复提交同一重试命令 SHALL NOT 创建无法区分的重复执行。

#### Scenario: 重试失败运行
- **WHEN** 用户对可重试失败记录执行重试
- **THEN** 系统 SHALL 创建带 `retry_of` 关联的新记录并保留原失败记录

