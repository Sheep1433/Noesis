## MODIFIED Requirements

### Requirement: SuperAgent SHALL 提供用户记忆检索工具

SuperAgent SHALL 获得 `search_memory` 工具，支持查询词、可选时间范围、memory type、来源类型、状态、project scope、top_k 和是否展开证据。工具 SHALL 在运行时绑定当前 user_id 和授权 scope，模型不得指定或覆盖用户标识。结果 SHALL 只返回新的结构化机器经验，并显式标注 `source`、type、status、score、memory id、evidence status 和来源标识；默认 SHALL NOT 返回 candidate、needs_review、disabled、invalidated 或 superseded，只有用户明确要求查看未验证/历史记录时才可返回并标注状态。工具 SHALL NOT 搜索旧按日文件，也 SHALL NOT 返回整篇文件、整条 Run、reasoning、大工具输出、内部 provider provenance 或服务端路径。

当简单检索无法满足明确的历史、多跳、时间、workflow 或错误前提问题时，工具 MAY 调用受限 MemoryQueryService。该服务 SHALL 受当前用户/scope、只读工具、timeout、step、token、并发和 source-span 预算约束；SHALL 返回带 memory/source 引用的 Bulletin 或明确 abstain。

#### Scenario: Agent 跨会话检索机器经验
- **WHEN** 用户问题需要回忆其它会话、项目决策或历史任务经验且 Agent 调用 `search_memory`
- **THEN** 工具 SHALL 只返回当前用户和授权 scope 的精简结果、状态、分数和来源标识

#### Scenario: 默认排除未验证与历史状态
- **WHEN** Agent 未显式请求非 active 状态
- **THEN** 结果 SHALL 排除 candidate、needs_review、superseded、disabled 和 invalidated

#### Scenario: 深度检索返回证据包
- **WHEN** 问题需要多条 Run evidence 且 MemoryQueryService 成功
- **THEN** 结果 SHALL 包含短 Bulletin、memory ids、source spans 和 evidence status
- **AND** SHALL NOT 返回无界原始 trajectory

#### Scenario: 用户隔离
- **WHEN** 模型尝试通过查询参数访问其它用户或未授权 project scope
- **THEN** 工具 SHALL 忽略或拒绝该标识
- **AND** SHALL 只使用 Runtime 绑定的当前用户和 scope

### Requirement: SuperAgent SHALL 提供记忆来源读取工具

SuperAgent SHALL 获得 `get_memory_source` 工具，并在数据库/文件服务层校验来源、snapshot、session、message、tool、artifact 和 memory item 均属于当前用户与授权 scope。工具 SHALL 只返回被 item 引用的安全 Run spans、角色、结构化 outcome、产物/验证摘要和 source digest。内部 provider provenance、敏感参数、服务端路径、其它用户内容和未脱敏错误 SHALL NOT 返回；旧按日文件 SHALL NOT 作为来源类型继续兼容。

#### Scenario: Agent 追溯机器经验来源
- **WHEN** Agent 使用 memory/evidence id 请求来源
- **THEN** 工具 SHALL 返回支持该结论的有限 Run spans
- **AND** SHALL 明确区分用户输入、assistant 结论、工具 outcome、产物和验证

#### Scenario: 来源已删除或到期
- **WHEN** evidence 指向的消息/snapshot 已删除或大 payload 已按保留期清理
- **THEN** 工具 SHALL 返回来源不可用或仅剩 digest/metadata
- **AND** SHALL NOT 转换为未预期 500

#### Scenario: 越权来源请求
- **WHEN** 请求的来源不属于当前用户或授权 scope
- **THEN** 工具 SHALL 返回不存在或无权限且不得泄露来源内容

## REMOVED Requirements

### Requirement: L2 记忆查询 SHALL 保持用户路径隔离

**Reason**: L2 按日记忆与新的 Run snapshot/item/source-span 查询重复，旧文件能力未上线且不再保留。

**Migration**: 删除 L2 目录查询、日期路径参数、按日索引和对应工具/API 分支；用户显式上下文继续通过 `USER.md` / `AGENTS.md` API 管理，机器经验通过结构化 Service 查询。

## ADDED Requirements

### Requirement: Agent Runtime SHALL 按 Run 注入稳定的 Memory Bulletin

启用经验记忆的主 Agent profile SHALL 在新 `run_id` 的 `before_agent` 边界执行 fast memory retrieval，并将有界 Bulletin、memory ids、source snapshot 和 `run_id` 写入 LangGraph private state。RunService SHALL 将 `run_id`、user id、agent profile 和 project key 显式传递至 agent factory/middleware；middleware SHALL NOT 从 session id 或模型文本推断。相同 Run 的模型调用和 HITL resume SHALL 复用逐字节相同的自动块，新 Run SHALL 刷新；该 private state SHALL NOT 自动复制给 subagent。

Bulletin SHALL 只由当前用户/scope 的 active、有效、有合格 provenance/source evidence 且达到冻结相关性阈值的 item 构成。fast path SHALL 不额外调用生成模型；任一依赖失败 SHALL 零注入并继续 Run，不得用 raw、candidate、needs_review、过期或跨 scope 内容兜底。

#### Scenario: 同一 Run 自动块稳定
- **WHEN** 同一 Run 内发生多次模型调用
- **THEN** 每次请求中的自动 Memory Bulletin SHALL 逐字节相同

#### Scenario: HITL 跨进程恢复
- **WHEN** Run 进入 HITL pending 并从 checkpoint 在另一进程恢复
- **THEN** middleware SHALL 复用 checkpoint private state 中的原 Bulletin

#### Scenario: subagent 不继承自动块
- **WHEN** 主 Agent 创建 subagent
- **THEN** Memory Bulletin private state SHALL NOT 因通用状态复制而自动传入 subagent

#### Scenario: 记忆依赖降级
- **WHEN** embedding、workspace、Qdrant 或 PostgreSQL memory 查询不可用
- **THEN** Runtime SHALL 零注入并继续 Agent Run
- **AND** SHALL NOT 放宽状态、scope 或 provenance 门槛

#### Scenario: 用户关闭开关
- **WHEN** 用户关闭经验记忆并开始新 Run
- **THEN** Runtime SHALL 不执行自动检索或注入
- **AND** `search_memory`、`get_memory_source` 和治理操作 SHALL 保持可用

### Requirement: MemoryQueryService SHALL 使用独立只读运行边界

MemoryQueryService SHALL 与主 Agent 的业务工具分离，只装配 manifest/item/Run span/artifact summary 只读工具，并显式禁止外部网络、远程 MCP、业务写工具、shell 写入、跨用户路径和递归 memory capture。Service SHALL 对每次查询记录 duration、step count、token usage、returned spans、evidence status 和失败分类；trace SHALL 脱敏并按保留期清理。

#### Scenario: 查询 Controller 尝试调用业务工具
- **WHEN** MemoryQueryService 模型请求未注册的业务写工具或外部工具
- **THEN** Runtime SHALL 拒绝该工具调用
- **AND** SHALL NOT扩大当前授权或继续以该输出作为证据

#### Scenario: 查询无证据
- **WHEN** 只读检索在预算内未找到支持当前问题的来源
- **THEN** Service SHALL 返回 `insufficient`/abstain
- **AND** SHALL NOT 根据相似摘要编造结论

### Requirement: Runtime SHALL 保护稳定 Prompt 前缀的上下文缓存

PromptAssembler SHALL 保持稳定 system/developer instructions、工具 schema 和可缓存历史前缀位于自动 Memory Bulletin 之前，并将 Bulletin 作为单一 late context segment 放在稳定前缀之后、当前用户输入/本轮新增内容之前。Bulletin SHALL 使用 canonical serialization：稳定排序、固定字段顺序/空白/转义，且模型可见文本 SHALL NOT 包含当前时间、当前 `run_id`、source run/span、evidence count、last verified time 或随机值。相同 `bulletin_hash` SHALL 生成逐字节相同文本；真实 memory 内容变化时 SHALL 生成新 hash，不得为了 cache 保留 stale 内容。

`run_id`、source snapshot/span 和动态治理字段 SHALL 只保存在 private metadata，通过来源工具展开。显式 Deep Query SHALL 作为后续 tool result 追加，不改写稳定 system prompt 或冻结 Bulletin。Runtime SHALL 保持上述稳定前缀以支持 provider 的自动 prefix cache；系统 SHALL NOT 注入 provider 专用 cache breakpoint 标记（当前无显式缓存 provider 诉求），也不得因此增加用户开关。Runtime SHALL 记录 provider 可得的 cache-read、cache-write、uncached input 和 TTFT；provider 不返回时 SHALL 记录 unknown 而非 0。

#### Scenario: 同 Run 后续调用复用 Bulletin
- **WHEN** 同一 Run 的后续模型调用使用相同 `bulletin_hash`
- **THEN** 模型可见 Bulletin SHALL 逐字节相同
- **AND** Runtime SHALL 保留稳定 prefix 以允许 provider cache reuse

#### Scenario: 新 Run 内容未变化
- **WHEN** 新 Run 检索出与上一 Run 相同的 memory items 和可见字段
- **THEN** canonical serializer SHALL 产生相同 `bulletin_hash` 和文本
- **AND** SHALL NOT 因新 `run_id` 或当前时间制造 cache miss

#### Scenario: Memory 内容真实变化
- **WHEN** active statement、applicability 或 verification label 发生变化
- **THEN** 系统 SHALL 生成新的 Bulletin/hash
- **AND** SHALL 优先保证内容正确而非复用旧 cache

#### Scenario: Provider 不返回 cache 指标
- **WHEN** 模型响应没有 cache token details
- **THEN** telemetry SHALL 标记 cache metrics unavailable
- **AND** SHALL NOT 把缺失值计为零缓存命中

#### Scenario: Provider 要求显式 cache breakpoint
- **WHEN** 当前模型适配器声明支持且要求显式 prompt cache breakpoint
- **THEN** Runtime SHALL 在稳定前缀末端设置 provider-native breakpoint
- **AND** 动态 Bulletin、当前用户输入和后续 tool result SHALL 位于 breakpoint 之后
