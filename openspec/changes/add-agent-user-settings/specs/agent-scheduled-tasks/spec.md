## ADDED Requirements

### Requirement: 系统 SHALL 持久化用户级定时任务

系统 SHALL 为每个用户持久化定时任务记录（推荐 PostgreSQL 表），字段至少包括：唯一 `id`、`user_id`、`name`、cron 表达式、`timezone`、`enabled`、目标 `qa_type`、执行 `prompt`、会话绑定策略、投递策略、`last_run_at`、`next_run_at`、`last_status`。

任务定义（表达式、启停、prompt、绑定）SHALL 仅能由已认证用户经设置 UI 或 `/api/user/scheduled-tasks` API 变更；Agent 工具链 **SHALL NOT** 直接修改任务定义表。

#### Scenario: 创建任务

- **WHEN** 用户提交合法 cron 表达式、`qa_type` 与 prompt 的创建请求
- **THEN** 系统 SHALL 持久化任务、`enabled` 默认可为 true，并计算 `next_run_at`

#### Scenario: 非法 cron 拒绝

- **WHEN** 用户提交无法解析的 cron 表达式
- **THEN** SHALL 返回 HTTP 400 且不创建记录

### Requirement: 系统 SHALL 提供定时任务 CRUD 与启停 API

系统 SHALL 提供前缀 `/api/user/scheduled-tasks` 的 JWT 认证 API，支持：列表、获取、创建、更新、删除、启用/停用、手动触发一次（可选）。

列表与变更 **SHALL** 仅作用于当前 `user_id`；越权 id **SHALL** 返回 404。

#### Scenario: 列表仅本人任务

- **WHEN** 用户 A 调用列表 API
- **THEN** 返回集合中每条记录的 `user_id` SHALL 等于 A，且不含用户 B 的任务

#### Scenario: 停用任务

- **WHEN** 用户将任务 `enabled` 设为 false
- **THEN** 调度器 SHALL 不再触发该任务，直到重新启用

### Requirement: 设置页 automation section SHALL 管理定时任务

设置壳 `automation` section SHALL 展示当前用户任务列表（名称、日程摘要、启用状态、最近运行状态），并支持创建/编辑/删除/启停。

#### Scenario: 在设置中停用任务

- **WHEN** 用户在 `automation` 关闭某任务开关并保存成功
- **THEN** 该任务在 API 中 `enabled` SHALL 为 false

### Requirement: 调度执行 SHALL 按绑定策略运行 Agent

当任务到期且 `enabled` 为 true 时，系统 SHALL 使用任务指定的 `qa_type` 与 `prompt` 触发一轮 Agent 执行。

会话绑定：

- `none`（默认）：在与用户主聊天时间线隔离的执行上下文中运行（实现可使用内部 session 或无 UI 会话）。
- `session:{session_id}`：仅当该会话仍存在且属于该用户时运行；否则系统 SHALL 将任务自动 `enabled=false` 并记录原因。

投递策略 `delivery` MAY 包含 `none`、站内通知、或绑定已配置通讯通道（见 `agent-messaging-channels`）；首期至少 SHALL 支持 `none`（仅记运行状态）。

#### Scenario: 到期触发 isolated 任务

- **WHEN** 已启用且绑定为 `none` 的任务到达 `next_run_at`
- **THEN** 系统 SHALL 执行一轮对应 `qa_type` 的 Agent，并更新 `last_run_at` 与 `last_status`，且 **SHALL NOT** 把该例行跑次伪装为用户在主会话手动发送的消息（除非产品显式选择投递到会话）

#### Scenario: 绑定会话已删除则停用

- **WHEN** 任务绑定 `session:{id}` 且该会话已被删除
- **THEN** 系统 SHALL 停用该任务且不再调度

### Requirement: 删除用户 SHALL 级联清理定时任务

当用户账号数据被删除时，系统 SHALL 删除或使其不可调度该用户全部定时任务。

#### Scenario: 用户删除后无残留调度

- **WHEN** 用户 U 被删除且曾有启用中的定时任务
- **THEN** 调度器 SHALL 不再执行这些任务
