## ADDED Requirements

### Requirement: 用户画像 SHALL 同时支持结构化与原文编辑
设置页 SHALL 展示常用画像字段并允许编辑，同时保留 `USER.md` 原文模式；两种模式 SHALL 写入同一用户权威文件，并在无法无损解析时阻止结构化覆盖、引导用户使用原文模式。

#### Scenario: 结构化保存画像
- **WHEN** 用户修改称呼和时区并保存
- **THEN** `USER.md` SHALL 更新且后续 Agent `/memory/USER.md` 读取到相同内容

### Requirement: 用户 SHALL 浏览和搜索 L2 日记记忆
系统 SHALL 提供当前用户 L2 日记列表、日期、大小、修改时间与限量全文搜索；L2 SHALL NOT 默认注入每次 Agent run。搜索 SHALL NOT 跨用户读取文件。

#### Scenario: 搜索日记
- **WHEN** 用户搜索只存在于某日日记的关键词
- **THEN** 系统 SHALL 返回对应日期、脱敏片段和定位信息

### Requirement: 设置页 SHALL 提供真实的上下文注入预览
系统 SHALL 复用运行时 resolver/compiler 生成只读预览，展示来源、优先级、是否注入、字符或 Token 估算和最终编译内容。生成预览 SHALL NOT 调用模型、创建 checkpoint 或修改记忆。

#### Scenario: 预览下一次 Agent 上下文
- **WHEN** 用户选择一个 Agent profile 请求上下文预览
- **THEN** 系统 SHALL 返回该 profile 实际解析规则下的分段来源与最终只读内容
