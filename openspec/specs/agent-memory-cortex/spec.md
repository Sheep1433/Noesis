# agent-memory-cortex Specification

## Purpose
TBD - created by archiving change add-run-aware-memory-cortex. Update Purpose after archive.
## Requirements
### Requirement: 系统 SHALL 为每个有稳定证据的终态主 Run 创建一次 capture job

当主 Agent Run 权威终态为 `completed|partial|error|interrupted`、用户经验记忆 `enabled=true`、Run 不是内部整理/subagent Run，且存在持久化 assistant 结论、终态 ToolPart、产物/文件变更、验证结果或用户纠正中的至少一项时，系统 SHALL 按 `run_id` 幂等创建一次 capture job。资格 SHALL NOT 依赖工具是否失败、是否发生工具调用或最终是否成功。`hitl_pending` SHALL NOT 创建；没有产生任何稳定工作证据的用户取消 interrupted Run SHALL NOT 创建；HITL resume SHALL 沿用原 `run_id`。

#### Scenario: 无工具失败的成功 Run 仍被 capture
- **WHEN** 启用经验记忆的用户完成一个包含决策和验证但没有工具失败的主 Run
- **THEN** 系统 SHALL 创建该 Run 的 capture job

#### Scenario: partial/error Run 保留失败经验
- **WHEN** 启用经验记忆的主 Run 以 partial 或 error 终态结束且已持久化工具 outcome、用户纠正或产物/验证证据
- **THEN** 系统 SHALL 创建 capture job
- **AND** extraction MAY 产生 experience 或 gotcha

#### Scenario: 无有效工作的用户取消不 capture
- **WHEN** 用户在 assistant 结论、终态 ToolPart、产物或纠正产生前取消 Run
- **THEN** 系统 SHALL NOT 创建 capture job

#### Scenario: 同一终态 Run 幂等
- **WHEN** 同一 `run_id` 的权威终态因重试被处理多次
- **THEN** 系统 SHALL 只保留一条该 Run 的 capture job

#### Scenario: HITL 恢复只在最终完成后 capture
- **WHEN** Run 进入 `hitl_pending`，随后以同一 `run_id` 恢复并 completed
- **THEN** pending 阶段 SHALL NOT 创建 capture job
- **AND** completed 后 SHALL 创建一次 job

#### Scenario: 内部整理 Run 不递归产生记忆
- **WHEN** memory extraction、consolidation 或 query controller 自己完成内部 Run
- **THEN** 系统 SHALL NOT 为该内部 Run 创建新的自动 capture job

### Requirement: Run snapshot SHALL 是稳定且可寻址的提取输入

系统 SHALL 为 eligible Run 生成唯一 Run snapshot，记录 `user_id`、`session_id`、`run_id`、scope、schema version、source watermark、content digest、token estimate、chunk metadata 和安全的 evidence payload/路径。snapshot SHALL 在 extraction 重试期间保持稳定，不因后续记忆变化、索引变化或聊天列表刷新而改变。snapshot SHALL NOT 自动注入模型上下文。

#### Scenario: extraction 重试读取同一 snapshot
- **WHEN** extraction 在部分 chunk 完成后重试
- **THEN** worker SHALL 使用相同 source watermark 和 content digest 的 snapshot
- **AND** SHALL NOT 从变化后的 UI 投影重建另一份输入

#### Scenario: 来源软删除
- **WHEN** snapshot 对应的消息在记忆生成后被软删除
- **THEN** memory item MAY 保留
- **AND** 来源接口 SHALL 返回来源不可用而非其它用户内容

#### Scenario: 账户删除清理 snapshot
- **WHEN** 用户账户被删除
- **THEN** 系统 SHALL 按用户数据清理规则删除其 snapshot、item、evidence 和派生视图

### Requirement: Capture SHALL 排除脚手架与 recall-loop 内容

capture SHALL 保存用户目标/纠正、assistant 可见结论、结构化 ToolPart outcome、产物摘要、验证结果和 compaction 覆盖信息；SHALL 排除系统脚手架、reasoning、重复流式片段、既有 memory bootstrap、memory 搜索结果和内部整理提示。每个保留片段 SHALL 标记 `user|assistant_derived|tool_internal|tool_external` 之一作为 provenance；`system` 与 `memory_recall` 内容 SHALL NOT 保留为 span 或作为新 memory statement 的证据。

#### Scenario: 已召回记忆不被再次提取
- **WHEN** 当前 Run 的 assistant 内容包含由自动 Bulletin 或 `search_memory` 返回的旧记忆
- **THEN** capture SHALL 将该内容标记为 `memory_recall`
- **AND** extraction SHALL NOT 把它作为新候选证据

#### Scenario: 外部工具内容保留为低信任证据
- **WHEN** ToolPart 内容来自网页或远程外部工具
- **THEN** snapshot SHALL 标记 `tool_external`
- **AND** 该来源单独 SHALL NOT 激活命令式 workflow 或 gotcha

#### Scenario: assistant 转述不提升外部来源信任
- **WHEN** assistant statement 由一个或多个 `tool_external` span 推导
- **THEN** candidate effective provenance SHALL 继承 supporting evidence 中最低信任等级
- **AND** SHALL NOT 仅因内容出现在 assistant 文本中升级为可自动注入来源

### Requirement: 长 Run SHALL 按结构边界分块且不得静默丢失

系统 SHALL 在 extraction 前估算 token，并按用户目标/纠正、assistant 结论、tool call+outcome、artifact+validation 和 compaction span 的完整边界分块。每个 chunk SHALL 有稳定 `chunk_id`、token 上限和 source spans。超大工具输出 SHALL 只保留安全摘录、结构化 outcome、digest 和来源指针。单个 chunk 失败 SHALL 记录 coverage gap；系统 SHALL NOT 将部分成功报告为完整成功。

#### Scenario: 超限 Run 被分块
- **WHEN** normalized snapshot 超过 extraction 模型输入预算
- **THEN** 系统 SHALL 生成多个边界完整的 chunk
- **AND** SHALL NOT 只做固定首尾截断

#### Scenario: 单 chunk 失败
- **WHEN** 多 chunk extraction 中一个 chunk 超时且其它 chunk 成功
- **THEN** job SHALL 标记 partial 或进入可重试状态
- **AND** coverage 指标 SHALL 记录未处理 chunk

#### Scenario: 无价值 Run 正常空结果
- **WHEN** 所有 chunk 成功处理但没有值得跨 Session 保留的内容
- **THEN** job SHALL 以 `succeeded_no_output` 完成
- **AND** SHALL NOT 创建占位 memory item

### Requirement: Extractor SHALL 只生成四类带证据候选

Extractor SHALL 只输出 `decision|experience|workflow|gotcha`，并为每个 candidate 返回受限的 `subject`、`statement`、`applicability`、`evidence_refs`、`confidence_reason` 和可选关系建议。记忆维度同时覆盖任务经验与用户上下文：用户陈述的持久个人目标、兴趣、背景或输出偏好 SHALL 作为 decision candidate 提取（用户证据即可，无需任务产物），瞬时情绪、一次性好奇与单会话细节 SHALL NOT 提取。所有 evidence ref SHALL 属于当前 snapshot；代码 SHALL 计算 canonical subject key、校验类型/长度/角色标记/敏感信息并二次脱敏。模型 SHALL NOT 直接指定 user、scope、状态、有效期、数据库 id 或索引操作。

#### Scenario: 决策候选引用确认来源
- **WHEN** Extractor 输出 decision candidate
- **THEN** candidate SHALL 引用用户确认或完成产物/验证证据
- **AND** 无确认的 assistant 建议 SHALL 保持 candidate 或被拒绝

#### Scenario: 工作流候选包含验证和 stop rule
- **WHEN** Extractor 输出 workflow candidate
- **THEN** statement SHALL 包含适用条件、关键步骤和验证/停止条件
- **AND** SHALL 引用对应 Run span

#### Scenario: 伪造 evidence id
- **WHEN** 模型返回不属于当前 snapshot 的 evidence ref
- **THEN** 系统 SHALL 丢弃该 candidate 并记录 schema violation

### Requirement: Memory identity SHALL 包含用户、scope、类型与 subject

canonical identity SHALL 为 `(user_id, scope_key, memory_type, subject_key)`。scope SHALL 至少区分 agent profile 和 project key，并 MAY 包含 tool provider/environment。project key SHALL 由受控规则派生：会话工作区内带 `origin` 的 Git 仓库使用规范化 remote identity；其余情况（含会话沙箱内无 origin 的 Git 仓库与非 Git Run）一律为 `global`——Agent 工作区为每会话沙箱，无 origin 的沙箱仓库若按路径 digest 派生 key 将形成其他会话不可复现的死胡同 scope。模型和客户端不得指定其它用户或未授权 scope。跨项目 item 默认 SHALL NOT 自动注入，但 MAY 被当前用户显式搜索。

#### Scenario: 同主题跨项目隔离
- **WHEN** 两个带 origin 的仓库（项目）产生相同工具错误但修复方式不同
- **THEN** 系统 SHALL 保留不同 scope 的 memory item
- **AND** 一个项目的新 Run SHALL NOT 自动注入另一个项目条目

#### Scenario: 沙箱内无 origin 的 Git 仓库归入 global
- **WHEN** 会话沙箱内 `git init` 但未配置 origin 时自动提取记忆
- **THEN** project key SHALL 为 `global`，产生的 item SHALL 保持 candidate
- **AND** SHALL NOT 按会话沙箱路径派生其他会话无法复现的 scope

#### Scenario: 客户端伪造 scope
- **WHEN** 客户端或模型在 memory 请求中提交其它用户/project scope
- **THEN** Service SHALL 使用当前认证用户和 Runtime scope 重新约束或拒绝请求

#### Scenario: 非 Git 全局经验默认只可显式读取
- **WHEN** 非 Git Run 自动产生 project_key=`global` 的 candidate
- **THEN** 该 item SHALL 保持 candidate 或 pull-only
- **AND** 只有用户明确确认适用于全部非项目任务后才 MAY active/自动注入

### Requirement: Consolidation SHALL 执行有证据的确定性状态迁移

系统 SHALL 在 canonical identity 的事务锁内，从当前 item、当前 snapshot candidates 和有界近邻中执行 `ADD|REINFORCE|UPDATE|SUPERSEDE|CONTRADICT|NOOP`。向量相似度 SHALL 只产生候选集，不得单独决定 UPDATE/SUPERSEDE。模型裁决 SHALL 只能引用代码提供的 candidate ids/operation enum/evidence refs。item 状态 SHALL 为 `candidate|active|superseded|disabled|invalidated|needs_review`；自动任务 SHALL NOT 复活 disabled 或 invalidated item。

#### Scenario: 重复证据强化当前项
- **WHEN** 新 candidate 与 active item 同 subject、同结论且来自独立 Run
- **THEN** consolidation SHALL REINFORCE 当前 item 并追加 evidence
- **AND** SHALL NOT 新建重复 current row

#### Scenario: 用户纠正旧决策
- **WHEN** 新 Run 中用户明确撤销同 subject 的旧 active decision
- **THEN** consolidation SHALL SUPERSEDE 旧 item 并创建新的 current item
- **AND** SHALL 保留旧版本和来源

#### Scenario: 冲突证据不足
- **WHEN** 两个可信来源对同 subject 冲突且没有明确时间/用户纠正可裁决
- **THEN** 系统 SHALL 标记 needs_review/contradicts
- **AND** SHALL NOT 自动注入任一命令式结论

#### Scenario: disabled 不被自动复活
- **WHEN** disabled item 后续再次出现相同候选
- **THEN** 系统 MAY 追加 evidence
- **AND** SHALL 保持 disabled

### Requirement: Memory jobs SHALL 支持阶段恢复、fencing 与可见失败

后台 job SHALL 明确区分 `capture|extract|consolidate` 阶段和 `pending|claimed|succeeded|succeeded_no_output|partial|failed|dead|skipped_disabled` 结果；`workspace_sync|index_sync` SHALL 作为带 claim/fencing 的 outbox 目标执行，复用同一结果与可靠性约束。claim SHALL 使用短事务、`SKIP LOCKED`、attempts-on-claim、lease 和唯一 claim token；完成、失败、续租和阶段提交 SHALL 校验 token。持久化的上阶段结果 SHALL 在重试中复用，不得重复调用模型。达到最大 attempts 的过期 claim SHALL 转为 dead，并能在用户健康界面中计数。

#### Scenario: 旧 worker fencing
- **WHEN** worker A lease 过期且 worker B 以新 token 接管
- **THEN** worker A 的阶段提交 SHALL 影响零行

#### Scenario: extract 结果已提交
- **WHEN** chunk/candidate 结果已持久化后 consolidation 失败重试
- **THEN** 系统 SHALL 复用已提交结果
- **AND** SHALL NOT 重复调用 Extractor

#### Scenario: 关闭开关停止 claimed job
- **WHEN** 用户在 job 处理期间关闭经验记忆
- **THEN** worker SHALL 在下一阶段边界标记 `skipped_disabled`
- **AND** SHALL NOT 创建新 item 或自动注入

### Requirement: PostgreSQL SHALL 是唯一权威事实源

item、relation、evidence、snapshot、job、preference 和 desired-state outbox SHALL 保存在 PostgreSQL。文件 workspace 与 Qdrant SHALL 仅从 PostgreSQL 当前状态生成，并支持全量重建。状态变化与对应 outbox SHALL 在同一事务提交；派生 worker SHALL 每次重读 PostgreSQL current state，active upsert，其它状态/不存在 delete。

#### Scenario: 删除后迟到同步事件
- **WHEN** item 删除后较早的 workspace/index 事件才执行
- **THEN** worker SHALL 读取到 item 不存在并执行删除
- **AND** SHALL NOT 恢复旧内容

#### Scenario: 派生视图可重建
- **WHEN** memory workspace 或 Qdrant collection 丢失
- **THEN** 系统 SHALL 从 PostgreSQL active/current items 重建

### Requirement: 文件 workspace SHALL 提供安全的 manifest 与证据导航

系统 SHALL 在服务端管理、按 user/scope 隔离的目录生成 `manifest.json`、`memory_summary.md`、四类 memory 文档和 Run summary。workspace SHALL 只包含安全摘要、检索 handles、memory ids 和 source span 引用，不得包含密钥、大工具输出、内部 provider 地址或跨用户路径。写入 SHALL 使用结构验证和 atomic replace；用户通过 API 修改后 SHALL 由 desired-state 同步更新。

#### Scenario: manifest 缩小候选范围
- **WHEN** deep query 需要搜索大量 Run
- **THEN** query service SHALL 能先读取当前 scope manifest/summary
- **AND** 无需扫描所有原始消息

#### Scenario: 用户直接编辑派生文件
- **WHEN** 派生 workspace 文件被外部修改
- **THEN** 系统 SHALL NOT 将该修改自动写回 PostgreSQL
- **AND** 下一次 desired-state 同步 MAY 覆盖该修改

### Requirement: Fast Bulletin SHALL 经过 scope、状态、来源、有效期和相关性门控

新 Run fast path SHALL 对 lexical/manifest 与 semantic candidates 做有界合并，再由 PostgreSQL 过滤当前用户、当前 project/profile scope、active、有效、来源合格且有可追溯 evidence 的 item。系统 SHALL 用结构化字段确定性渲染短 Bulletin，并设置总 item/token 预算。candidate、needs_review、superseded、disabled、invalidated、跨项目、外部来源命令和 raw evidence SHALL NOT 自动注入。任一依赖失败 SHALL 零注入且 Run 继续。

#### Scenario: 低相关性零注入
- **WHEN** 所有候选低于冻结阈值或不满足权威过滤
- **THEN** 系统 SHALL 不注入 Memory Bulletin

#### Scenario: stale index 被拒绝
- **WHEN** Qdrant 返回已 superseded/disabled/invalidated item
- **THEN** PostgreSQL 权威过滤 SHALL 排除该 item

#### Scenario: 外部命令不自动注入
- **WHEN** workflow 仅由 `tool_external` 内容支持且没有用户确认/受控验证
- **THEN** 该条目 SHALL NOT 进入自动 Bulletin

#### Scenario: 依赖故障降级
- **WHEN** embedding、workspace、Qdrant 或 PostgreSQL memory 查询失败
- **THEN** Runtime SHALL 零注入并继续当前 Run

### Requirement: Deep query SHALL 是只读、有界且证据优先的检索

当用户明确请求历史、当前问题需要多跳/时间/工作流证据，或 Agent 显式调用 `search_memory` 时，系统 MAY 运行受限 MemoryQueryService。该服务 SHALL 只拥有当前用户/scope 的 manifest、item、Run span 和 artifact summary 读取能力；SHALL 禁止网络、业务写工具、外部 MCP、shell 写入和跨用户路径；SHALL 限制 steps、timeout、token、并发和 returned spans。输出 SHALL 包含 bulletin、memory ids、source spans 和 evidence status；证据不足时 SHALL abstain。

#### Scenario: 精确回到 Run span
- **WHEN** query service 找到相关 workflow
- **THEN** 返回结果 SHALL 包含支持结论的 memory id 和 Run source span
- **AND** SHALL NOT 返回整条 Run 原文

#### Scenario: 深度查询超时
- **WHEN** query service 达到 timeout 或 step budget
- **THEN** `search_memory` SHALL 返回已验证的部分结果或明确超时状态
- **AND** SHALL NOT 降级为跨 scope/raw 全量注入

#### Scenario: 来源存在未裁决冲突
- **WHEN** 与查询相关的来源证据处于 `needs_review`
- **THEN** query service SHALL 将 evidence status 标记为 `contradicts`
- **AND** SHALL NOT 把待裁决结论作为已验证事实返回

### Requirement: 自动 Bulletin SHALL 在同一 Run 内保持稳定

Runtime SHALL 将 `run_id`、自动 Bulletin、`bulletin_hash`、memory ids 和 source snapshot 保存为 LangGraph private state。模型可见 Bulletin SHALL 只包含稳定 statement、applicability、verification label 和 memory id；当前 run id、source run/span、时间、evidence count、last verified 与随机值 SHALL 只存在于 private metadata。相同 Run 的多次模型调用、HITL resume 和跨进程 checkpoint 恢复 SHALL 复用逐字节相同的自动块；新 `run_id` SHALL 重新检索，但相同可见内容 SHALL 产生相同 hash/text。private state SHALL NOT 自动传给 subagent。显式 deep query 结果 SHALL 作为工具输出返回，不得改写已冻结自动块。

#### Scenario: HITL 跨进程恢复
- **WHEN** Run 注入后进入 HITL pending 并由另一进程恢复
- **THEN** Runtime SHALL 复用 checkpoint 中的原 Bulletin

#### Scenario: 新 Run 刷新
- **WHEN** 同一 session 开始新的 `run_id`
- **THEN** Runtime SHALL 忽略上一 Run 的自动块并重新检索

### Requirement: 用户 SHALL 能治理 memory item 和来源

用户 SHALL 能查看自己的四类 item、状态、scope、版本、独立 Run evidence 数、最后验证时间和处理健康；并能编辑安全展示字段、disable、enable、invalidate、delete 和查看来源。所有操作 SHALL 校验 user/scope 并经过 Service/状态机。用户编辑 SHALL 形成可审计 revision；delete SHALL 清理 item/evidence/relation 并同步派生视图，但 SHALL 明示未来相似 Run 可能重新生成。

#### Scenario: 用户禁用 active item
- **WHEN** 用户 disable 自己的 active item
- **THEN** 该 item SHALL 停止自动注入
- **AND** 自动 consolidation SHALL NOT 使其复活

#### Scenario: 用户编辑结论
- **WHEN** 用户修改 active item 的结论或适用范围
- **THEN** 系统 SHALL 保存用户 revision 和旧版本
- **AND** 新版本 SHALL 使用 `user` provenance

#### Scenario: 越权治理
- **WHEN** 用户请求其它用户的 memory id、snapshot 或来源
- **THEN** 系统 SHALL 返回不存在或无权限
- **AND** SHALL NOT 泄露内容、scope、路径或处理状态

### Requirement: 新实现开始前 SHALL 删除旧机器经验行为路径

实现 SHALL 在新增 Run snapshot、四类 Extractor、Consolidation、workspace、Bulletin 或 Deep Query 业务代码前，先删除旧 RecoveryAdapter、failure identity/resolution、experience-only Extractor/Revision/Retriever、raw action-card middleware、旧 worker/runtime/API/UI 装配和仅验证旧语义的测试；同时删除 `MemoryDreamService`、Dream scheduler/自动补写、按日整理 prompt、`memory/YYYY-MM-DD.md` 数据/API/UI/index/search 分支和测试。删除后 SHALL 建立应用可编译、用户显式 `USER.md` / `AGENTS.md` 上下文可运行、但机器经验自动 capture/注入暂不可用的空白基线。系统 SHALL NOT 通过兼容 flag、legacy module、双 worker、双 middleware、旧 L2 读取或版本选择保留旧行为。

允许保留的代码 SHALL 限于独立的单一用户 preference 和现有通用 user/scope 鉴权。旧 item/evidence/job/outbox 表定义、运行数据和可靠性实现 SHALL 被删除；功能未上线，SHALL NOT 提供旧数据迁移、兼容读取或旧运行装配。新版 job/outbox SHALL 按新版状态机从零实现。

#### Scenario: 删除门禁通过后才写新实现
- **WHEN** 开发准备开始新增四类 memory pipeline
- **THEN** 静态扫描和 removal baseline 测试 SHALL 已证明旧 adapter、action-card、failure-only job hook 和 legacy runtime wiring 不可达/不存在
- **AND** 新实现任务 SHALL NOT 在该门禁前开始

#### Scenario: 新版可靠性基础从空模型实现
- **WHEN** 新版实现进入通用数据模型与后台可靠性阶段
- **THEN** 系统 SHALL 按新版 phase/result/fencing 约束重新实现 lease、claim token 和 outbox
- **AND** SHALL NOT import、包装或兼容旧 experience-only job/outbox 实现

#### Scenario: 未上线旧数据不迁移
- **WHEN** 新版 migration 创建 memory 表
- **THEN** migration SHALL NOT 读取或转换旧 experience item/evidence/job/outbox
- **AND** 旧 worker、middleware 和 API SHALL 不存在

#### Scenario: 旧 Dream 和按日数据被删除
- **WHEN** removal baseline 完成
- **THEN** `MemoryDreamService`、Dream scheduler、自动补写、按日文件 API/UI/search 与 `memory/YYYY-MM-DD.md` 运行时数据 SHALL 不存在
- **AND** `USER.md` / `AGENTS.md` 显式编辑 SHALL 继续可用

### Requirement: 临时数据、错误和派生文件 SHALL 有界保留

系统 SHALL 为 raw snapshot payload、chunk 结果、job result、dead job/outbox、query trace 和派生 Run summaries 配置独立保留期。错误和 trace SHALL 脱敏并截断；cleanup SHALL 不依赖 Qdrant 或 workspace worker 是否运行。健康页面 SHALL 在这些记录的配置保留期内展示非敏感状态和时间；保留期届满后 MAY 删除历史明细与对应计数，不得伪装成永久累计指标。

#### Scenario: 大 snapshot 到期清理
- **WHEN** snapshot 超过配置保留期且不再被 active item evidence 引用
- **THEN** cleanup SHALL 删除大 payload/派生 Run 文件
- **AND** MAY 保留 digest、状态、时间和计数

#### Scenario: 索引 worker 停止
- **WHEN** Qdrant 同步 worker 不运行
- **THEN** job/snapshot/query trace cleanup SHALL 继续执行

