# agent-session-history Specification

## Purpose

Agent 侧会话历史检索：对数据库消息原文的确定性全文检索（pg_trgm，无 LLM 参与），含单会话工具 `search_history`（压缩遮蔽区过滤与按序号滚动深读）与跨会话发现工具 `search_sessions`，默认挂载于 SuperAgent、以闭包绑定当前用户身份；工具描述内建 SOURCE-FIRST 行为约束（会话历史只证明「曾经说过」，不构成外部事实的证据）。配套消息正文 trigram GIN 索引与会话压缩边界列 `compaction_cutoff_seq`。压缩中间件本身的行为见 `agent-runtime`；记忆蒸馏层检索（`search_memory`）见 `agent-memory`。
## Requirements
### Requirement: 单会话历史检索工具

系统 SHALL 提供 `search_history` 工具：对指定会话（省略 session_id 时为当前会话）的消息原文做确定性全文检索（pg_trgm），返回 top-k 命中消息（含角色、序号、时间），按序号升序排列。指定 session_id 时 SHALL 校验会话归属当前用户。工具 SHALL 支持压缩遮蔽区过滤——启用时只检索最近一次压缩边界之前的消息。检索 SHALL NOT 调用 LLM，结果 SHALL 为数据库原文。返回 SHALL 有长度上限：单条命中截断（并标注省略），总返回不超过配置上限——检索 SHALL NOT 成为读回全量历史的通道。

#### Scenario: 找回被压缩的细节

- **WHEN** 会话已发生压缩，Agent 调用 `search_history` 启用压缩区过滤并给出具体关键词
- **THEN** 系统 SHALL 仅在压缩边界之前的消息中匹配，返回命中原文片段
- **AND** 返回结果 SHALL 包含消息序号，足以定位其在原会话中的位置

#### Scenario: 跨会话发现后的定点跟进

- **WHEN** Agent 以 `search_sessions` 返回的 session_id 调用 `search_history`
- **THEN** 系统 SHALL 校验该会话归属当前用户后执行检索
- **AND** 归属不符时 SHALL 拒绝且不泄露该会话存在性

#### Scenario: 超长命中截断

- **WHEN** 命中消息为超长内容（含嵌在消息 parts 中的工具轨迹）
- **THEN** 单条返回 SHALL 截断至配置上限并标注省略
- **AND** 渲染 SHALL 提取 parts 中的文本内容，SHALL NOT 返回原始 JSON 结构

#### Scenario: 滚动深读（截断后的完整上下文出口）

- **WHEN** 检索形态返回的命中被截断，Agent 以该消息序号调用 `search_history` 滚动形态
- **THEN** 系统 SHALL 返回目标消息前后各至多 window 条的原文（仍受单条与总量上限约束）
- **AND** 窗口参数 SHALL 有服务端上限，SHALL NOT 成为读回全量历史的通道

#### Scenario: 压缩边界缺失时的降级

- **WHEN** 当前会话从未压缩或边界值缺失
- **THEN** 压缩区过滤 SHALL 退化为检索全历史并标注"边界未知"
- **AND** SHALL NOT 因边界缺失而拒绝检索

#### Scenario: 检索确定性

- **WHEN** 同一会话以相同参数重复调用 `search_history`
- **THEN** 返回结果 SHALL 完全一致（无 LLM、无随机性）

### Requirement: 跨会话发现工具

系统 SHALL 提供 `search_sessions` 工具：按关键词检索当前用户的历史会话，按会话分组返回（会话标识、标题或首条消息摘要、时间与最强匹配片段），并 SHALL 排除当前会话。检索范围 SHALL 强制限定为当前用户的会话，工具 SHALL NOT 接受模型提供的用户标识。

#### Scenario: 发现先前的讨论

- **WHEN** Agent 以关键词调用 `search_sessions`
- **THEN** 系统 SHALL 返回当前用户历史会话的匹配分组，含最强匹配片段、会话标识、标题与会话类型（root/subagent 及父会话标识）
- **AND** 当前会话 SHALL NOT 出现在结果中

#### Scenario: 用户隔离

- **WHEN** 其他用户存在包含相同关键词的会话
- **THEN** 该会话 SHALL NOT 出现在检索结果中

### Requirement: SOURCE-FIRST 行为约束

两个检索工具的描述 SHALL 内建来源优先规则：工具检索的是对话历史，只证明"曾经说过"，不构成外部事实的证据；当用户给出 URL、文件路径、账号等直接来源时，Agent 应先查原来源。工具描述 SHALL 同时说明与记忆检索工具的分工（原文细节用会话检索，偏好与结论用记忆检索）。

#### Scenario: 工具描述包含约束

- **WHEN** 工具注册进 Agent 的工具列表
- **THEN** 其 description SHALL 包含来源优先规则与两层检索分工说明

### Requirement: SuperAgent 默认挂载

`search_history` 与 `search_sessions` SHALL 默认挂载于 SuperAgent 工具集（与记忆检索工具并列）；GeneralQAAgent SHALL NOT 默认挂载。工具构造 SHALL 以闭包绑定当前用户身份，运行期 SHALL NOT 可被模型参数覆盖。

#### Scenario: 挂载面

- **WHEN** SuperAgent 会话创建
- **THEN** 两个会话检索工具 SHALL 可用
- **WHEN** GeneralQAAgent 会话创建
- **THEN** 会话检索工具 SHALL NOT 出现在工具列表

#### Scenario: 身份绑定

- **WHEN** 模型尝试通过工具参数指定其他用户或会话范围
- **THEN** 系统 SHALL 忽略或拒绝该参数，检索范围仍为闭包绑定的当前用户

### Requirement: 检索索引与压缩边界列

系统 SHALL 为 `t_chat_message` 的消息正文建立 trigram GIN 索引（幂等迁移），并为 `t_chat_session` 增加 `compaction_cutoff_seq` 列。压缩中间件完成压缩时 SHALL 更新该列为压缩完成时刻、排除活跃 run streaming 骨架行后的最大已终态消息序号（保守上界：被压缩遮蔽的消息必然 ≤ 该值；对齐 `memory_extracted_seq` 列先例），供压缩区过滤使用。

#### Scenario: 索引就位

- **WHEN** 迁移执行后对消息正文做关键词检索
- **THEN** 查询 SHALL 走索引且中文关键词可命中

#### Scenario: 新压缩携带边界

- **WHEN** 会话发生一次压缩
- **THEN** 会话行的 `compaction_cutoff_seq` SHALL 更新为不小于被压缩前缀最大消息序号的边界值（排除当前活跃 run 的 streaming 骨架行）
