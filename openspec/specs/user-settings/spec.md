# user-settings Specification

## Purpose

本能力规定 Noesis **用户设置控制面**：设置壳与可搜索 section 注册表、一致交互与危险操作保护、设置导入导出与脱敏审计、用户画像（L0）与记忆（L1/L2）原文编辑、每日记忆整理与跨会话检索、上下文注入预览、定时任务与自动化运行记录、通讯通道配置面、模型目录只读、通知偏好与平台依赖健康诊断。设置 API 的鉴权、隔离、secret 语义与 Provider 凭据托管禁令见 `user-platform`；通道运行时见 `agent-delivery`；记忆磁盘布局与 `/memory/` 路由见 `agent-runtime`。
## Requirements
### Requirement: 系统 SHALL 提供个人与 Agent 设置壳

系统 SHALL 提供需登录的设置界面（路由 `/settings`），以 URL 查询参数 `s=` 切换 section。侧栏用户头像（或等价入口）SHALL 可进入该设置壳。

设置壳 SHALL 至少包含下列 section，且 **SHALL NOT** 提供 slash 命令配置类 section：

| Section id | 职责 |
|------------|------|
| `overview` | 配置健康与常用入口 |
| `models` | 平台可用模型目录（只读，见下文） |
| `profile` | 用户画像（L0，`USER.md` 原文编辑） |
| `memory` | 记忆与偏好（L1 `AGENTS.md`，及 L2 入口） |
| `capabilities` | 深链至 Skills / MCP / 知识库管理页 |
| `automation` | 定时任务（cron）列表与编辑 |
| `channels` | 通讯通道配置 |
| `diagnostics` | 通知、健康检查与设置迁移 |
| `account` | 退出登录等账号操作 |

#### Scenario: 从侧栏进入设置

- **WHEN** 已登录用户点击侧栏头像并选择进入设置（或直接打开设置入口）
- **THEN** 系统 SHALL 导航至设置壳且默认 section 为 `overview` 或上次合法 `s` 值

#### Scenario: 切换 section

- **WHEN** 用户选择 `memory` section
- **THEN** URL SHALL 反映该 section，且页面展示记忆编辑面而非整页刷新丢失壳布局

#### Scenario: 无 slash 配置

- **WHEN** 用户打开设置壳并遍历导航
- **THEN** UI **SHALL NOT** 出现 slash 命令注册、绑定或编辑入口

### Requirement: 设置壳 SHALL 使用可搜索的统一 section 注册表

系统 SHALL 以单一注册表声明设置 section 的 id、标题、关键词、深链参数和可用状态；`/settings?s=<id>` SHALL 可直接打开合法 section，搜索 SHALL 匹配标题与关键词，未知 id SHALL 安全回退到概览。即便某通道运行时仍为 stub，导航亦 SHALL 展示可理解的占位或「即将推出」状态，**SHALL NOT** 省略导航项（除非产品配置显式关闭整个通道能力）。

#### Scenario: 搜索并深链设置项

- **WHEN** 用户搜索“模型”并选择模型设置
- **THEN** 系统 SHALL 打开对应 section 且 URL 包含稳定 section id

#### Scenario: 非法 section

- **WHEN** 用户访问不存在的 `/settings?s=unknown`
- **THEN** 系统 SHALL 回退到概览并保持设置壳可用

### Requirement: 设置交互 SHALL 提供一致状态与危险操作保护

所有设置 section SHALL 使用一致的加载、空态、保存中、保存成功、字段错误和请求失败语义；删除、清空、恢复默认和覆盖导入 SHALL 在执行前展示具体影响并要求确认。存在未保存修改时离开 section SHALL 提示用户。

#### Scenario: 未保存修改离开

- **WHEN** 用户修改表单但尚未保存并切换 section
- **THEN** 系统 SHALL 提示保存、放弃或留在当前 section

**画像与记忆**

### Requirement: 设置页为用户记忆主编辑入口

`profile` section SHALL 编辑 `users/{user_id}/USER.md`，`memory` section SHALL 编辑 `users/{user_id}/AGENTS.md`，且两者 SHALL 仅提供 Markdown 原文编辑。系统 SHALL 提供不依赖 `session_id` 的用户级读写 API（`GET/PUT /api/user/memory/USER.md` 与 `GET/PUT /api/user/memory/AGENTS.md`），写入结果 SHALL 与 Agent `/memory/` 为同一磁盘文件。

系统 SHALL NOT 为 `USER.md` 维护固定“常用字段”区块或第二套结构化字段保存接口；`memory` section SHALL 展示文件最近修改时间（或等价元数据），便于用户发现显式修改。会话上下文面板编辑 `USER.md` / `AGENTS.md` SHALL 作为兼容路径（见 `chat-composer`）。机器经验 SHALL 在独立区域通过结构化 API 治理，SHALL NOT 自动写入或修改 `USER.md` / `AGENTS.md`。设置页 SHALL NOT 再展示 Dream、按日记忆、日期整理、自动补写或 `memory/YYYY-MM-DD.md` 文件入口。

#### Scenario: 设置页保存画像
- **WHEN** 用户在 `profile` 编辑画像并保存成功
- **THEN** `users/{user_id}/USER.md` SHALL 更新，且后续显式上下文加载可见新内容

#### Scenario: 设置页保存偏好记忆
- **WHEN** 用户在 `memory` 编辑 `AGENTS.md` 并保存
- **THEN** 磁盘文件 SHALL 更新且预览/编辑区展示最新内容

#### Scenario: 不展示固定画像字段
- **WHEN** 用户打开 `profile` section
- **THEN** 页面 SHALL 直接展示原文编辑器且不展示称呼、时区、语言、角色固定表单

#### Scenario: 不展示旧按日记忆入口
- **WHEN** 用户打开设置页的 Memory 区域
- **THEN** 页面 SHALL NOT 展示日期整理、Dream 状态、按日文件、自动补写或旧 L2 搜索入口

#### Scenario: 未登录拒绝
- **WHEN** 未认证客户端请求用户级 memory API
- **THEN** SHALL 返回 HTTP 401

### Requirement: 设置页 SHALL 提供真实的上下文注入预览

系统 SHALL 复用运行时 resolver/compiler 生成只读预览，展示来源、优先级、是否注入、字符或 Token 估算和最终编译内容。生成预览 SHALL NOT 调用模型、创建 checkpoint 或修改记忆。

#### Scenario: 预览下一次 Agent 上下文

- **WHEN** 用户选择一个 Agent profile 请求上下文预览
- **THEN** 系统 SHALL 返回该 profile 实际解析规则下的分段来源与最终只读内容

**自动化与定时任务**

### Requirement: 系统 SHALL 持久化用户级定时任务

系统 SHALL 为每个用户持久化定时任务记录（推荐 PostgreSQL 表），字段至少包括：唯一 `id`、`user_id`、`name`、cron 表达式、`timezone`、`enabled`、目标 `qa_type`、执行 `prompt`、会话绑定策略、投递策略、`last_run_at`、`next_run_at`、`last_status`。用户 SHALL 能创建和编辑任务名称、cron、时区、prompt、`qa_type`、会话绑定、启用状态和投递目标；系统 SHALL 校验表达式并返回人类可读日程摘要与下一次执行时间，无效表达式 SHALL NOT 保存。

任务定义（表达式、启停、prompt、绑定）SHALL 仅能由已认证用户经设置 UI 或 `/api/user/scheduled-tasks` API 变更；Agent 工具链 **SHALL NOT** 直接修改任务定义表。

#### Scenario: 创建任务

- **WHEN** 用户提交合法 cron 表达式、`qa_type` 与 prompt 的创建请求
- **THEN** 系统 SHALL 持久化任务、`enabled` 默认可为 true，并计算 `next_run_at`

#### Scenario: 非法 cron 拒绝

- **WHEN** 用户提交无法解析的 cron 表达式
- **THEN** SHALL 返回 HTTP 400 且不创建记录

#### Scenario: 预览合法日程

- **WHEN** 用户输入合法 cron 与时区
- **THEN** 设置页 SHALL 显示日程摘要和按该时区计算的下一次执行时间

### Requirement: 系统 SHALL 提供定时任务 CRUD 与启停 API

系统 SHALL 提供前缀 `/api/user/scheduled-tasks` 的认证 API（Cookie Session + 非安全方法 CSRF，与 `user-platform` 一致），支持：列表、获取、创建、更新、删除、启用/停用、手动触发一次（可选）。系统 **SHALL NOT** 以 Authorization Bearer JWT 作为本 API 的身份凭据。列表与变更 **SHALL** 仅作用于当前 `user_id`；越权 id **SHALL** 返回 404。设置壳 `automation` section SHALL 展示当前用户任务列表（名称、日程摘要、启用状态、最近运行状态），并支持创建/编辑/删除/启停。

#### Scenario: 列表仅本人任务

- **WHEN** 用户 A 调用列表 API
- **THEN** 返回集合中每条记录的 `user_id` SHALL 等于 A，且不含用户 B 的任务

#### Scenario: 停用任务

- **WHEN** 用户将任务 `enabled` 设为 false
- **THEN** 调度器 SHALL 不再触发该任务，直到重新启用

#### Scenario: 在设置中停用任务

- **WHEN** 用户在 `automation` 关闭某任务开关并保存成功
- **THEN** 该任务在 API 中 `enabled` SHALL 为 false

### Requirement: 调度执行 SHALL 按绑定策略运行 Agent

当任务到期且 `enabled` 为 true 时，系统 SHALL 使用任务指定的 `qa_type` 与 `prompt` 触发一轮 Agent 执行。会话绑定：`none`（默认）在与用户主聊天时间线隔离的执行上下文中运行；`session:{session_id}` 仅当该会话仍存在且属于该用户时运行，否则系统 SHALL 将任务自动 `enabled=false` 并记录原因。投递策略 `delivery` MAY 包含 `none`、站内通知、或绑定已配置通讯通道（见 `agent-delivery`）；首期至少 SHALL 支持 `none`（仅记运行状态）。

#### Scenario: 到期触发 isolated 任务

- **WHEN** 已启用且绑定为 `none` 的任务到达 `next_run_at`
- **THEN** 系统 SHALL 执行一轮对应 `qa_type` 的 Agent，并更新 `last_run_at` 与 `last_status`，且 **SHALL NOT** 把该例行跑次伪装为用户在主会话手动发送的消息（除非产品显式选择投递到会话）

#### Scenario: 绑定会话已删除则停用

- **WHEN** 任务绑定 `session:{id}` 且该会话已被删除
- **THEN** 系统 SHALL 停用该任务且不再调度

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

### Requirement: 删除用户 SHALL 级联清理定时任务

当用户账号数据被删除时，系统 SHALL 删除或使其不可调度该用户全部定时任务。

#### Scenario: 用户删除后无残留调度

- **WHEN** 用户 U 被删除且曾有启用中的定时任务
- **THEN** 调度器 SHALL 不再执行这些任务

**通讯通道配置**

### Requirement: 系统 SHALL 提供可扩展的通讯通道配置模型

系统 SHALL 为每个用户持久化零个或多个通讯通道配置，每条至少包括：`channel_id`、`type`、`enabled`、`display_name`、通道特定连接参数。首期 `type` SHALL 包含 `telegram`；模型 SHALL 允许后续增加其它 `type`（如飞书）而不改变设置壳导航结构。

通道密钥与 token **SHALL** 仅经由已认证用户的设置 UI 或 `/api/user/channels`（或等价）API 写入；**SHALL NOT** 存入 `USER.md` / `AGENTS.md` / 日记文件；Agent 工具 **SHALL NOT** 读取明文 token 或修改通道密钥字段。secret read model SHALL 只返回 configured、可选后缀和更新时间。

#### Scenario: 保存 Telegram 通道

- **WHEN** 用户提交 `type=telegram` 与合法 bot token 的创建/更新请求
- **THEN** 系统 SHALL 持久化通道且 `enabled` 可按请求设置；后续 GET **SHALL** 对 token 脱敏（例如仅后缀）

#### Scenario: Agent 不可改通道密钥

- **WHEN** Agent 尝试通过文件工具或其它工具写入通道配置或 token
- **THEN** 系统 SHALL 拒绝且 SHALL NOT 持久化该变更

### Requirement: 通道设置 SHALL 展示权威健康与最近活动

系统 SHALL 为当前用户每条通道展示配置启用状态、adapter 运行状态、最近检查时间、最近入站/出站结果和脱敏错误摘要。健康数据 SHALL 来源于 Delivery/adapter read model，设置层 SHALL NOT 维护第二套运行状态。

#### Scenario: Adapter 离线

- **WHEN** 已启用通道的 adapter 当前不可用
- **THEN** 设置页 SHALL 显示 degraded/unavailable 状态、检查时间和可行动提示

### Requirement: 用户 SHALL 测试通道连接与投递

用户 SHALL 能执行不产生聊天消息的连接测试，以及发送固定产品内容的测试投递。测试 SHALL 受当前用户通道作用域、速率限制和超时约束，并记录脱敏结果与审计。

#### Scenario: 测试 Telegram 投递

- **WHEN** 已配对用户对启用的 Telegram 通道发送测试消息
- **THEN** adapter SHALL 向绑定目标发送固定测试内容并在设置页返回投递结果

### Requirement: 通道默认路由 SHALL 受用户与能力边界约束

通道设置 SHALL 支持默认 `qa_type`、会话策略和投递偏好；配置 SHALL 仅能引用当前用户可用对象。禁用通道 SHALL NOT 接收入站 Agent run，也 SHALL NOT 产生出站投递。

#### Scenario: 禁用通道

- **WHEN** 用户关闭通道启用状态
- **THEN** 后续入站 SHALL NOT 触发 Agent 且后续出站 SHALL NOT 投递到该通道

**模型与可观测性**

### Requirement: 用户 SHALL 只读查看平台模型目录

设置页 `models` section SHALL 展示平台已配置的可用模型、默认模型及用户可理解的能力信息，不得提供 Provider、Base URL 或 API Key 输入。

#### Scenario: 查看模型设置

- **WHEN** 用户打开模型设置
- **THEN** 页面 SHALL 展示 `/api/models` 返回的目录且不出现凭据管理操作

### Requirement: 用户 SHALL 配置通知偏好

系统 SHALL 允许当前用户按事件类型和可用投递表面配置通知偏好，事件至少覆盖自动化完成/失败、HITL 待处理和通道异常。关闭某类通知 SHALL NOT 停止对应业务执行，只停止该类用户通知。

#### Scenario: 关闭任务成功通知

- **WHEN** 用户关闭自动化成功通知但保留失败通知
- **THEN** 成功 run SHALL 不通知用户而失败 run 仍 SHALL 按可用表面通知

### Requirement: 设置概览 SHALL 聚合平台依赖健康

系统 SHALL 聚合模型 Provider、MCP、Scheduler、通道、业务数据库、checkpoint、Qdrant 与 Sandbox 的 `healthy|degraded|unavailable|unknown` 状态、检查时间、用户可理解摘要和可选行动码。各检查 SHALL 独立超时；单项失败 SHALL NOT 使整个端点失败。

#### Scenario: Qdrant 不可用

- **WHEN** Qdrant 检查超时而其它依赖正常
- **THEN** 诊断端点 SHALL 返回 Qdrant unavailable 和其它依赖各自状态，HTTP 响应保持可用

### Requirement: 诊断响应 SHALL 避免暴露实现与敏感信息

普通用户诊断响应 SHALL NOT 包含连接串、主机凭据、绝对文件路径、内部堆栈或 secret；服务端日志可记录关联 id，前端 SHALL 使用行动码呈现可理解恢复建议。

#### Scenario: 数据库认证错误

- **WHEN** 后端诊断捕获数据库认证异常
- **THEN** 用户响应 SHALL 仅包含数据库不可用摘要与关联 id，不包含 DSN 或口令

**导入导出与审计**

### Requirement: 设置导入导出 SHALL 版本化且默认排除敏感数据

系统 SHALL 提供当前用户设置的版本化导出，以及 preview/apply 两阶段导入。导出 SHALL 排除 secret、Provider、消息、附件、checkpoint 和运行日志；preview SHALL 展示新增、修改、忽略、冲突和校验错误，未经确认 SHALL NOT 写入。导入 SHALL NOT 接受 providers 设置域；apply 的事务化与按域校验见 `user-platform`。

#### Scenario: 导出不含 Provider

- **WHEN** 用户导出当前设置
- **THEN** 导出文件 SHALL 不包含 Provider、API Key、Base URL 或模型用途绑定

#### Scenario: 导入预览

- **WHEN** 用户上传包含 providers 域的设置文件
- **THEN** 系统 SHALL 在预览中拒绝该域且不得写入 Provider 数据

### Requirement: 敏感设置变更 SHALL 形成脱敏审计

MCP secret、通道 Token、自动化定义、通知策略和设置导入等变更 SHALL 记录当前用户、动作、设置域、目标标识、时间和脱敏摘要；审计 SHALL NOT 保存旧值或新值的明文 secret。系统 SHALL NOT 再产生用户 Provider 变更审计。

#### Scenario: 替换通道 Token

- **WHEN** 用户替换通道 Token
- **THEN** 系统 SHALL 记录 replace 动作但 SHALL NOT 在审计响应或日志中返回 Token 内容

### Requirement: 用户显式上下文 SHALL 与机器经验分离

`USER.md` / `AGENTS.md` SHALL 只由用户或明确的文件编辑操作修改，作为显式上下文来源；decision、experience、workflow 和 gotcha SHALL 保存在机器经验事实源，并经独立状态、scope、provenance 和 evidence 治理。自动 extraction/consolidation SHALL NOT 修改显式上下文，设置预览 SHALL 分别标注显式上下文和自动 Bulletin。

#### Scenario: 自动整理不修改显式上下文
- **WHEN** 系统完成 Run extraction 或 consolidation
- **THEN** `USER.md` 与 `AGENTS.md` SHALL 保持不变

#### Scenario: 预览区分来源
- **WHEN** 用户查看下一次 Agent 上下文预览
- **THEN** 页面 SHALL 分别展示显式上下文与自动 Memory Bulletin
- **AND** SHALL NOT 把机器经验伪装成用户手写规则

### Requirement: 设置页 SHALL 提供四类经验记忆治理入口

设置页 Memory section SHALL 展示当前用户的 decision、experience、workflow 和 gotcha，至少包括 type、status、scope、statement、applicability、独立 Run evidence 数、last verified time、版本和来源入口。用户 SHALL 能按 type/status/project scope 搜索过滤，并执行查看来源、编辑、activate、disable、enable、invalidate 和 delete；candidate item SHALL 只能经用户显式确认（activate）转为 active；前端 SHALL 展示加载、成功、冲突和用户可理解的失败状态。

#### Scenario: 查看四类记忆和证据
- **WHEN** 已认证用户打开经验记忆列表
- **THEN** 页面 SHALL 只展示该用户 item 和独立 Run evidence 数
- **AND** 非 active 状态 SHALL 有明确标识且不暗示当前生效

#### Scenario: 编辑记忆形成版本
- **WHEN** 用户修改 statement 或 applicability
- **THEN** Service SHALL 保存可审计的新 revision 和用户来源
- **AND** 页面 SHALL 能识别当前版本

#### Scenario: 显式确认 candidate 全局适用
- **WHEN** 非 Git Run 自动产生的 global candidate 待用户确认
- **THEN** 用户显式 activate 后 item SHALL 转为 active 并记录用户确认来源
- **AND** 未经确认的 candidate SHALL NOT 自动注入

#### Scenario: 删除确认说明再生成语义
- **WHEN** 用户请求删除经验记忆
- **THEN** 页面 SHALL 在确认前说明删除当前记录不阻止未来相似 Run 重新生成
- **AND** 用户确认后 SHALL 清理 item/evidence/relation 并排队清理派生视图

### Requirement: 设置页 SHALL 展示自动处理健康

设置页 SHALL 展示当前用户最近成功 capture/consolidation 时间，以及 pending、partial、failed、dead、skipped-disabled 数量和 workspace/index 是否落后。用户文案 SHALL 使用业务含义，不得显示数据库表、claim token、provider key、服务端路径、内部网络位置或未脱敏错误。健康展示 SHALL 不改变单一用户开关语义。

#### Scenario: 后台任务部分失败
- **WHEN** 用户的长 Run 只有部分 chunk extraction 成功
- **THEN** 设置页 SHALL 显示“部分处理”及安全摘要
- **AND** SHALL NOT 把该 Run 展示为完整成功

#### Scenario: dead job 可诊断
- **WHEN** job 达到最大 attempts 进入 dead
- **THEN** 设置页 SHALL 计入处理失败
- **AND** SHALL 提供不泄露内部细节的处理建议或重试状态

### Requirement: 经验记忆设置 API SHALL 鉴权并经 Service 执行

系统 SHALL 在 `/api/user/memory/cortex` 静态前缀下提供 preference、item list/detail/update、source/evidence、activate、disable、enable、invalidate、delete 和 processing health API。所有接口 SHALL 使用 Cookie Session + CSRF、当前用户/授权 scope 校验、`noesis.schemas.memory`、统一响应和 Service 状态机；API SHALL NOT 定义同义 schema 或直接查询/修改 ORM。旧 Dream/按日记忆 API SHALL 被删除，该前缀 SHALL NOT 与 `USER.md` / `AGENTS.md` 文件 API 冲突。

#### Scenario: 未认证请求
- **WHEN** 未认证客户端请求任一经验记忆设置 API
- **THEN** 系统 SHALL 返回 HTTP 401

#### Scenario: 越权 memory/snapshot id
- **WHEN** 用户提交不属于自己的 memory、evidence 或 snapshot id
- **THEN** 系统 SHALL 返回 404 或等价不泄露响应
- **AND** SHALL NOT 返回内容、scope、provider、来源或处理状态

#### Scenario: 重复状态操作
- **WHEN** 用户重复 activate、disable、enable 或 invalidate 同一 item
- **THEN** Service SHALL 返回确定的幂等结果或明确冲突
- **AND** SHALL NOT 产生两个 current row 或分叉 supersession 链

### Requirement: 用户 SHALL 通过单一开关控制自动经验记忆

设置页 SHALL 只提供一个“经验记忆”用户开关，持久化字段为 `enabled`，默认关闭；系统 SHALL NOT 提供平台总开关或拆分的 capture/extraction/consolidation/injection 用户开关。开启后允许后续有稳定证据的终态主 Run 自动 capture、后台整理和新 Run fast Bulletin；关闭后不创建新的自动任务、不继续后续阶段且新 Run 不自动注入。关闭 SHALL NOT 删除已有 item/snapshot，且 SHALL NOT 禁止已有记忆查看、显式搜索、来源读取或治理。

#### Scenario: 用户开启经验记忆
- **WHEN** 用户开启“经验记忆”
- **THEN** 后续有稳定证据的终态主 Run SHALL 创建自动 capture job
- **AND** 后续新 Run MAY 注入通过门控的 Bulletin

#### Scenario: 用户关闭经验记忆
- **WHEN** 用户关闭“经验记忆”
- **THEN** 后续 eligible Run SHALL NOT 创建自动 capture job，claimed job SHALL 在阶段边界安全停止，后续新 Run SHALL 零自动注入
- **AND** 已有记忆 SHALL 保留并可查看、显式搜索或治理

#### Scenario: 重新开启不默认回放历史
- **WHEN** 用户关闭一段时间后重新开启经验记忆
- **THEN** 系统 SHALL 从重新开启后的有稳定证据终态主 Run 开始自动处理
- **AND** SHALL NOT 未经用户显式操作回放关闭期间全部历史 Run

