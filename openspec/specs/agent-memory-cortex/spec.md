# agent-memory-cortex Specification

## Purpose
用户级自动记忆：md 文件记忆层——五类条目、水位增量抽取、AutoDream 整理与每 Run 选条注入。取代已删除的 Memory Cortex（PG 权威条目 + Qdrant 派生）方案。
## Requirements
### Requirement: 记忆 SHALL 以 md 文件为唯一真相并按情景/语义两层组织

记忆 SHALL 存放于用户数据目录 `memory/` 下：`MEMORY.md` 索引（一行一条）+ 分类条目文件（一条一文件，含正文、Why、适用条件、来源引用与更新时间）+ `journal/` 按日情景日志（只追加）。语义记忆类型集 SHALL 冻结为五类：`preference` 偏好（用户要什么输出/行为）、`goal` 目标（用户在做什么，时效最强）、`decision` 决策（定了什么及原因）、`experience` 经验（什么做法有效）、`gotcha` 注意事项（什么要避开）；目录即枚举，新增类型 SHALL 需要新的变更提案。`USER.md` SHALL 保持纯手写，引擎 SHALL NOT 修改。条目淘汰 SHALL 表现为索引移除，journal SHALL 永久保留原始记录（可搜、可重建条目）。用户直接编辑文件 SHALL 为最高权限；引擎写入前 SHALL 重读文件，SHALL NOT 覆盖用户改动。索引 SHALL 设行数与字节双上限，超预算 SHALL 触发整理压缩，SHALL NOT 静默截断。

#### Scenario: 用户直接编辑生效
- **WHEN** 用户编辑条目文件或索引并保存
- **THEN** 下次注入 SHALL 使用修改后内容
- **AND** 引擎后续写入 SHALL 基于修改后文件增量进行

#### Scenario: 淘汰不丢失
- **WHEN** 条目被整理任务淘汰
- **THEN** 索引行与条目文件 SHALL 移除
- **AND** journal 中的原始记录 SHALL 保留且可被检索工具搜到

#### Scenario: 索引损坏行容错
- **WHEN** 用户手动编辑导致索引行格式损坏
- **THEN** 注入 SHALL 跳过损坏行且 SHALL NOT 失败
- **AND** 索引 SHALL 可从条目目录重建

### Requirement: 记忆写入 SHALL 在会话结束后按水位增量抽取

会话 idle 超过阈值且最新合格消息序号超过水位（`memory_extracted_seq`）时，系统 SHALL 异步抽取该会话水位之后的新消息段，输入 SHALL 含新消息段（有界，超预算保段头与段尾）、水位前至多 2 条衔接背景消息、本轮注入清单与现有条目，自动写入条目与 journal，SHALL NOT 要求用户确认。同一用户的抽取任务 SHALL 串行执行。subagent 会话 SHALL NOT 抽取（结论经父会话终态通知回流）。抽取 SHALL 将条目归入冻结五类之一；不属于任何类型的内容 SHALL 只进 journal 情景日志、SHALL NOT 进入语义层。抽取 SHALL 排除「不该存」内容（文件或代码本身可得的信息、临时任务状态）并将相对日期改写为绝对日期。写入时 SHALL 做轻量合并（语义重复更新既有条目并追加来源，明显过时当场改写）。守卫 SHALL 包括：敏感内容拒收、本轮注入条目的复述不记录（防自强化，注入清单来自 run 的 memory_context）、无价值会话零写入、单段至多 3 条新条目（超出 SHALL 只进 journal）。水位 SHALL 仅在抽取成功后推进（失败保留原水位，下次 sweep 重试同段）；记忆关闭期间水位 SHALL 照常推进（不回溯）。抽取标记 SHALL NOT 改变会话列表排序（updated_at 语义 = 用户最后活动）。

#### Scenario: 复述不产生新条目
- **WHEN** 会话中 assistant 复述了本轮注入的记忆
- **THEN** 抽取 SHALL NOT 据此新增或更新该条目

#### Scenario: 修正注入条目即更新
- **WHEN** 会话中用户修正了本轮注入的记忆
- **THEN** 抽取 SHALL 更新该条目而非拒绝记录
- **AND** 该修正 SHALL NOT 被防自强化守卫拦截

#### Scenario: 重复经验合并
- **WHEN** 新会话经验与现有条目语义重复
- **THEN** 抽取 SHALL 更新该条目文件并追加来源，而非新建

#### Scenario: 续聊段增量抽取
- **WHEN** 已抽取过的会话再次续聊并转入 idle
- **THEN** 抽取 SHALL 只处理水位之后的新消息段
- **AND** 水位前至多 2 条消息 SHALL 作为衔接背景进入输入

#### Scenario: 抽取失败不推进水位
- **WHEN** 某续聊段抽取因 LLM 失败未完成
- **THEN** 水位 SHALL 保持原值
- **AND** 下次 sweep SHALL 重试同一段

#### Scenario: 抽取不扰动会话排序
- **WHEN** 抽取推进水位
- **THEN** 会话 updated_at SHALL NOT 被引擎内部状态改变

### Requirement: 整理 SHALL 由低频后台任务自动执行

系统 SHALL 提供低频整理任务（触发条件：条目数、索引大小或时间间隔），职责为全局去重、矛盾裁决、淘汰与索引压缩，自动执行无确认。整理质量 SHALL 由评测门禁与 journal 可重建性兜底。

#### Scenario: 矛盾裁决
- **WHEN** 整理发现新旧条目矛盾
- **THEN** SHALL 按时间与证据改写条目
- **AND** journal 中双方原始记录 SHALL 保留

#### Scenario: 目标完结检查
- **WHEN** goal 类条目对应的目标已完结或演进
- **THEN** 整理 SHALL 将其改写为结果性条目或淘汰
- **AND** journal 中原始记录 SHALL 保留

#### Scenario: 类型不匹配不入语义层
- **WHEN** 抽取内容不属于冻结五类中任何一类
- **THEN** 该内容 SHALL 只写入 journal
- **AND** SHALL NOT 创建语义条目文件

#### Scenario: 索引超预算走压缩
- **WHEN** 索引超出行数或字节上限
- **THEN** 整理 SHALL 通过去重、删除死指针与条目降级压缩索引
- **AND** SHALL NOT 静默截断最旧条目

### Requirement: 注入 SHALL 使用稳定前缀与每 Run 选条

系统 SHALL 在稳定前缀注入 `USER.md` 与 `MEMORY.md` 索引（会话内不变），每个新 Run SHALL 以廉价模型按当前问题从索引选条并注入选中条目正文（记忆量小于预算时全量）；同 Run 冻结（tool loop 与 HITL resume 期间内容不变）、subagent 不重复注入、上一 Run 已注入条目 SHALL NOT 重复注入、头部 SHALL 含「历史经验可能过时」声明且 SHALL 按条目年龄附 stale 警告、任一依赖失败 SHALL 零注入且 Run 继续。注入条目清单 SHALL 回写 run 的 memory_context。系统 SHALL NOT 截断单条条目。

#### Scenario: 小模型选条
- **WHEN** 记忆量超过全量预算
- **THEN** 系统 SHALL 以廉价模型按当前问题从索引选条并注入选中条目正文

#### Scenario: 陈旧条目附警告
- **WHEN** 注入条目的保存时间超过阈值
- **THEN** 注入 SHALL 附带陈旧提示引导模型验证后再使用

### Requirement: 会话中主动写入 SHALL 经 HITL 确认

会话进行中 Agent 提议写入记忆时 SHALL 经用户确认后写入条目文件与索引行，立即对下次会话生效。检索工具 SHALL 以 grep 与文件读取实现（类型目录过滤、预算限制沿用），本阶段 SHALL NOT 使用向量索引。

#### Scenario: 主动写入需确认
- **WHEN** Agent 会话中提议写入一条记忆
- **THEN** 系统 SHALL 请求用户确认后才写入条目文件与索引行
- **AND** 拒绝时 SHALL 不产生任何文件变更

#### Scenario: grep 检索
- **WHEN** 模型调用 search_memory
- **THEN** 工具 SHALL 以关键词在 memory 目录检索并返回条目原文