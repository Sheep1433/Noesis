# Delta：agent-memory-cortex

## MODIFIED Requirements

### Requirement: 记忆 SHALL 以 md 文件为唯一真相并按情景/语义两层组织

记忆 SHALL 存放于用户数据目录 `memory/` 下：`MEMORY.md` 索引（一行一条）+ 分类条目文件（一条一文件）+ `journal/` 按日情景日志（只追加）。语义记忆类型集 SHALL 冻结为五类：`preference` 偏好（用户要什么输出/行为）、`goal` 目标（用户在做什么，时效最强）、`decision` 决策（定了什么及原因）、`experience` 经验（什么做法有效）、`gotcha` 注意事项（什么要避开）；目录即枚举，新增类型 SHALL 需要新的变更提案。条目文件 SHALL 以 YAML frontmatter 承载结构化元数据，字段集 SHALL 冻结为：`type`（五类之一，与所在目录一致）、`label`（短标签）、`description`（一句话「是什么 + 何时调用」）、`tags`（可选；引擎抽取默认不生成，用户手写不受数量约束）、`created`（首次沉淀日期）、`updated`（最近改写日期）、`sources`（来源会话与日期，追加式）；新增字段 SHALL 需要新的变更提案。条目正文 SHALL 保留结论、Why 与适用条件散文节；`MEMORY.md` 索引行 SHALL 为 frontmatter 的投影（同一写路径生成与维护；用户对索引行的手工修改在对应条目下次写入时 SHALL 被覆盖，持久修改入口为条目 frontmatter）。`USER.md` SHALL 保持纯手写，引擎 SHALL NOT 修改。条目淘汰 SHALL 表现为索引移除与条目文件删除，journal SHALL 永久保留原始记录与整理快照（可搜、可重建条目含元数据）。用户直接编辑文件 SHALL 为最高权限；引擎写入前 SHALL 重读文件，SHALL NOT 覆盖用户改动。索引 SHALL 设行数与字节双上限，超预算 SHALL 触发整理压缩，SHALL NOT 静默截断。

#### Scenario: 用户直接编辑生效
- **WHEN** 用户编辑条目文件或索引并保存
- **THEN** 后续召回与引擎写入 SHALL 使用修改后内容
- **AND** 引擎后续写入 SHALL 基于修改后文件增量进行

#### Scenario: 淘汰不丢失
- **WHEN** 条目被整理任务淘汰
- **THEN** 索引行与条目文件 SHALL 移除
- **AND** journal 中 SHALL 保留该条目整理快照与原始记录，均可被检索工具搜到

#### Scenario: 索引损坏行容错
- **WHEN** 用户手动编辑导致索引行格式损坏
- **THEN** 稳定前缀索引读取 SHALL 跳过损坏行且 SHALL NOT 失败
- **AND** 索引 SHALL 可从条目目录重建（以 frontmatter 的 label 与 description 机械投影）

#### Scenario: frontmatter 损坏容错
- **WHEN** 条目文件 frontmatter YAML 解析失败（用户手改写坏）
- **THEN** 该条目 SHALL 退化为无元数据条目（正文照常可检索，description 回退正文截断，type 取自目录）
- **AND** 检索与索引重建 SHALL NOT 因此失败

#### Scenario: 存量条目惰性迁移
- **WHEN** 升级后首次对某用户运行抽取 sweeper 且该用户存在无 frontmatter 的存量条目
- **THEN** 系统 SHALL 按现有散文约定解析并补写 frontmatter（幂等，已迁移跳过）
- **AND** 解析失败的条目 SHALL 保持原样并走 frontmatter 容错路径，SHALL NOT 阻塞迁移

### Requirement: 记忆写入 SHALL 在会话结束后按水位增量抽取

会话 idle 超过阈值且最新合格消息序号超过水位（`memory_extracted_seq`）时，系统 SHALL 异步抽取该会话水位之后的新消息段，输入 SHALL 含新消息段（有界，超预算保段头与段尾）、水位前至多 2 条衔接背景消息、本轮召回清单（run.memory_context，经检索工具召回聚合）与现有条目，自动写入条目与 journal，SHALL NOT 要求用户确认。同一用户的抽取任务 SHALL 串行执行。subagent 会话 SHALL NOT 抽取（结论经父会话终态通知回流）。抽取 SHALL 将条目归入冻结五类之一；不属于任何类型的内容 SHALL 只进 journal 情景日志、SHALL NOT 进入语义层。带时效性或阶段性的内容 SHALL 只归入 `goal`，SHALL NOT 写入其余四类稳定类型。description SHALL 由抽取显式生成并遵循「是什么 + 何时调用」语义。抽取 SHALL 排除「不该存」内容（文件或代码本身可得的信息、临时任务状态）并将相对日期改写为绝对日期。写入时 SHALL 做轻量合并（语义重复更新既有条目并追加来源，明显过时当场改写）。每次产生写入或排除决策的抽取 SHALL 将决策摘要（新建、更新与排除内容及理由）随情景条目追加进当日 journal；无价值会话 SHALL 维持零写入。守卫 SHALL 包括：敏感内容拒收、本轮召回条目的复述不记录（防自强化，召回清单来自 run 的 memory_context）、无价值会话零写入、单段至多 3 条新条目（超出 SHALL 只进 journal）。水位 SHALL 仅在抽取成功后推进（失败保留原水位，下次 sweep 重试同段）；记忆关闭期间水位 SHALL 照常推进（不回溯）。抽取标记 SHALL NOT 改变会话列表排序（updated_at 语义 = 用户最后活动）。

#### Scenario: 复述不产生新条目
- **WHEN** 会话中 assistant 复述了本轮召回的记忆
- **THEN** 抽取 SHALL NOT 据此新增或更新该条目

#### Scenario: 修正召回条目即更新
- **WHEN** 会话中用户修正了本轮召回的记忆
- **THEN** 抽取 SHALL 更新该条目而非拒绝记录
- **AND** 该修正 SHALL NOT 被防自强化守卫拦截

#### Scenario: 重复经验合并
- **WHEN** 新会话经验与现有条目语义重复
- **THEN** 抽取 SHALL 更新该条目文件并追加来源，而非新建

#### Scenario: 时效性内容禁入稳定类型
- **WHEN** 抽取判定某内容带时效性或阶段性（如「正在做」「本季度要完成」）
- **THEN** 该内容 SHALL 只归入 goal
- **AND** SHALL NOT 写入 preference/decision/experience/gotcha

#### Scenario: 抽取决策落 journal
- **WHEN** 一次抽取产生写入或排除决策（含零新条目但有排除的情形）
- **THEN** 当日 journal SHALL 追加该次抽取的决策摘要（新建/更新条目及理由、排除内容及理由）
- **AND** 无价值会话 SHALL 维持零写入（不落决策块）

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

系统 SHALL 提供低频整理任务（触发条件：条目数、索引大小或时间间隔），职责为全局去重、矛盾裁决、淘汰与索引压缩，自动执行无确认。矛盾裁决 SHALL 按以下优先级取舍：用户显式修正 > 稳定类型条目 > 时间与证据；动态类型（goal）内容 SHALL NOT 静默改写稳定类型（preference/decision/experience/gotcha）条目。整理 rewrite/merge SHALL 同步维护条目 description（不提供新值时保留原值；merge 以保留方为准）。整理任务执行 merge/rewrite/remove 前 SHALL 将改前条目全文（含 frontmatter）快照追加进当日 journal；整理的情景信号输入 SHALL 排除这些快照块。整理任务 SHALL 机械检测 frontmatter `type` 与所在目录不一致的条目，以所在目录为准修正 frontmatter `type`（引擎写入路径自身保证一致，不一致源于用户挪动文件，位置即用户意图）。整理质量 SHALL 由评测门禁与 journal 可重建性兜底。

#### Scenario: 矛盾裁决
- **WHEN** 整理发现新旧条目矛盾
- **THEN** SHALL 按优先级（用户显式修正 > 稳定类型 > 时间与证据）改写条目
- **AND** journal 中双方原始记录与改前快照 SHALL 保留

#### Scenario: 动态内容不得静默改写稳定条目
- **WHEN** 整理发现新的 goal 类内容与既有 preference 条目表述冲突且无用户显式修正证据
- **THEN** 整理 SHALL 改写动态侧或产出新条目
- **AND** SHALL NOT 静默改写该 preference 条目

#### Scenario: 目标完结检查
- **WHEN** goal 类条目对应的目标已完结或演进
- **THEN** 整理 SHALL 将其改写为结果性条目或淘汰
- **AND** journal 中原始记录与改前快照 SHALL 保留

#### Scenario: 改写与淘汰前快照
- **WHEN** 整理任务对某条目执行 merge、rewrite 或 remove
- **THEN** 改前条目全文（含 frontmatter）SHALL 先追加进当日 journal
- **AND** 依据 journal 快照与原始记录 SHALL 可机械重建该条目（含元数据）

#### Scenario: 类型不匹配不入语义层
- **WHEN** 抽取内容不属于冻结五类中任何一类
- **THEN** 该内容 SHALL 只写入 journal
- **AND** SHALL NOT 创建语义条目文件

#### Scenario: frontmatter 类型与目录不一致归位
- **WHEN** 整理任务发现条目 frontmatter `type` 与所在目录不一致
- **THEN** SHALL 以所在目录为准修正 frontmatter `type`（不移动文件）
- **AND** 归位动作 SHALL 记录进 journal

#### Scenario: 索引超预算走压缩
- **WHEN** 索引超出行数或字节上限
- **THEN** 整理 SHALL 通过去重、删除死指针与条目降级压缩索引
- **AND** SHALL NOT 静默截断最旧条目

## REMOVED Requirements

### Requirement: 注入 SHALL 使用稳定前缀与每 Run 选条
**Reason**: 召回模式切换为 Agent 主动检索（Agentic recall）：被动注入由廉价模型在 Run 起点仅按最新一条用户消息盲选，检索时机与上下文均弱，且机制冗余（选条服务、注入中间件、alreadySurfaced 记账、Run 级冻结语义）。索引（含「是什么 + 何时调用」的 description）常驻稳定前缀作为路由面后，Agent 带全上下文按需检索在精度、上下文经济与机制复杂度上均占优。
**Migration**: 纯代码删除（`services/memory/selection.py`、`agents/middlewares/memory_entries_middleware.py` 及挂载点），文件格式、API 与数据均不变，回滚直接还原代码。`run.memory_context` 字段保留，写入方由注入中间件改为 `search_memory` 工具（root run 命中后合并写入）。stale 警告由注入时附加改为检索结果附加。

## ADDED Requirements

### Requirement: 记忆召回 SHALL 以稳定前缀索引与 Agent 主动检索实现

系统 SHALL 在稳定前缀注入 `USER.md` 全文与 `MEMORY.md` 索引（会话内不变）。条目正文 SHALL NOT 由引擎被动注入对话；Agent SHALL 经检索工具（`search_memory`）或文件读取按需召回条目原文。检索工具返回结果 SHALL 附条目年龄提示（超过阈值的条目附陈旧警告）。root Run 内经检索工具召回的条目清单 SHALL 合并写入 `run.memory_context`（作为抽取防自强化输入；通用文件读取不入清单，防自强化为尽力而为）。subagent SHALL 可使用检索工具但 SHALL NOT 写入 `run.memory_context`（结论经父会话终态回流）。检索工具执行失败 SHALL 返回错误信息且 SHALL NOT 中断 Run。系统 SHALL NOT 截断单条条目。记忆使用提示 SHALL 包含召回纪律（涉及用户偏好、历史决策、既往经验、当前目标时先检索再产出）。

#### Scenario: 稳定前缀注入索引
- **WHEN** 会话开始且记忆开启
- **THEN** USER.md 全文与 MEMORY.md 索引 SHALL 注入稳定前缀
- **AND** 会话内该前缀内容 SHALL 保持不变

#### Scenario: 无被动注入
- **WHEN** 新 Run 开始
- **THEN** 引擎 SHALL NOT 自动向对话注入任何条目正文

#### Scenario: 主动召回
- **WHEN** Agent 判断当前任务涉及记忆内容
- **THEN** Agent SHALL 经检索工具或文件读取获得条目原文
- **AND** 超过陈旧阈值的条目在检索结果中 SHALL 附年龄提示

#### Scenario: 召回清单回写
- **WHEN** root Run 内 Agent 经检索工具召回条目
- **THEN** run.memory_context SHALL 合并记录该召回清单（去重追加）
- **AND** subagent 的检索 SHALL NOT 写入 memory_context

#### Scenario: 检索失败不中断
- **WHEN** 检索工具执行失败
- **THEN** 工具 SHALL 返回错误信息
- **AND** Run SHALL 继续执行
