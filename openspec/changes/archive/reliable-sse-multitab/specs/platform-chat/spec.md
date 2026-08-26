## ADDED Requirements

### Requirement: 可靠 Web Agent Run SHALL 明确适用范围

系统 SHALL 将本 change 的可靠 Run、多 Tab、snapshot 恢复和统一 Delivery 要求应用于 `COMMON_QA`、`FAULT_OPERATION_QA` 与 `SUPER_AGENT_QA`。`TEST_CASE_QA` SHALL 继续被视为独立 CaseCoordinator workflow；本 change SHALL NOT 要求其 `phase-*`、test-case resume 或 export 接入新 Run 管线，也 SHALL NOT 为了该场景在新主路径保留旧 SSE parser、EventBus 或协议 adapter。

#### Scenario: 普通 Agent 聊天进入新 Run 管线

- **WHEN** 用户以 `COMMON_QA`、`FAULT_OPERATION_QA` 或 `SUPER_AGENT_QA` 创建 `/api/chat/runs`
- **THEN** 系统 SHALL 使用本 change 规定的 typed event、snapshot、sequence、PersistWriter 和 Delivery 路径

#### Scenario: 测试用例生成不进入验收

- **WHEN** 实施或测试本 change
- **THEN** `TEST_CASE_QA`、`phase-start`、`phase-delta`、`phase-end`、test-case resume 与 export SHALL NOT 作为本 change 的实施或验收项

### Requirement: 服务端 SHALL 提供权威 active Run 发现

系统 SHALL 提供 `GET /api/chat/sessions/{session_id}/active-run`。对已鉴权且拥有该 session 的用户，端点 SHALL 返回当前 active Run 的完整 RunSnapshot 或 `data=null`；返回 snapshot 的结构 SHALL 与 `GET /api/chat/runs/{run_id}` 一致。未知、已软删或不属于当前用户的 session SHALL 返回 404。

#### Scenario: 新 Tab 发现正在执行的 Run

- **WHEN** Tab A 已在 session S 中启动 Run R，Tab B 使用同一账号打开 session S
- **THEN** Tab B SHALL 通过 active-run 端点获得 R 的 `run_id`、`assistant_message_id`、status、`snapshot_sequence` 和 content
- **AND** Tab B SHALL NOT 依赖 Tab A 的 `sessionStorage`

#### Scenario: session 没有 active Run

- **WHEN** 用户查询自己的 session 且其中没有 active Run
- **THEN** 端点 SHALL 返回成功响应且 `data=null`

#### Scenario: 跨用户查询被拒绝

- **WHEN** 用户查询不属于自己的 session
- **THEN** 系统 SHALL 返回 404
- **AND** 响应 SHALL NOT 泄露 active `run_id` 或 `assistant_message_id`

### Requirement: 多 Tab SHALL 独立订阅同一 Run

同一用户的多个 Tab SHALL 能独立订阅同一 Run。每个 Tab SHALL 使用自己的 SSE subscription；断开、刷新或溢出任意一个 subscription SHALL 只移除该 subscription，SHALL NOT 取消 producer、改变 Run 状态或中断其它 Delivery。

#### Scenario: 关闭创建 Run 的 Tab

- **WHEN** Tab A 创建 Run，Tab A 与 Tab B 均已订阅，然后用户关闭 Tab A
- **THEN** producer SHALL 继续执行
- **AND** Tab B SHALL 继续收到后续事件和权威终态

#### Scenario: 两个 Tab 收到同一消息终态

- **WHEN** 两个 Tab 同时订阅同一 Run 直到完成
- **THEN** 两个 Tab 的 `assistant_message_id` SHALL 相同
- **AND** 两个 Tab 的最终 parts、status 与 `snapshot_sequence` SHALL 与 PostgreSQL 权威终态一致

### Requirement: 客户端 SHALL 以 snapshot replace 和 sequence 连续性恢复

客户端收到 `run-snapshot` 时 SHALL 以 replace 语义替换相同 `assistant_message_id` 的 parts，并将 `last_sequence` 设为 `snapshot_sequence`。业务事件 sequence 小于等于 `last_sequence` 时 SHALL 忽略；等于 `last_sequence + 1` 时 SHALL apply；大于 `last_sequence + 1` 时 SHALL 停止当前 reader、查询 snapshot 并重新订阅。

#### Scenario: 断网后用 snapshot 校正

- **WHEN** Tab B 在 Run 期间断网，Tab A 继续收取事件，随后 Tab B 恢复网络
- **THEN** Tab B SHALL 查询权威 snapshot 并 replace 当前 assistant
- **AND** Tab B SHALL NOT 重复 append 已包含在 snapshot 中的正文或 tool part

#### Scenario: sequence gap 不继续渲染

- **WHEN** 客户端的 `last_sequence=20` 且下一个业务事件 sequence 为 23
- **THEN** 客户端 SHALL NOT apply 该事件
- **AND** SHALL 进入 snapshot recovery

#### Scenario: 无终态 EOF 不伪装成功

- **WHEN** SSE 在 completed/partial/error/interrupted 终态之前 EOF
- **THEN** 客户端 SHALL 保持 Run 未完成语义并查询 snapshot
- **AND** SHALL NOT 调用成功或失败终态回调

### Requirement: 同 session 创建冲突 SHALL 加入已有 Run

同一 session 已有 active Run 时，`POST /api/chat/runs` SHALL 返回 HTTP 409 和稳定冲突数据：`run_id`、`assistant_message_id`、`session_id`、status。只有当前用户有权访问该 Run 时才能返回这些字段。客户端 SHALL 查询并加入已有 Run，SHALL NOT 启动第二 producer。

#### Scenario: 两个 Tab 同时发送

- **WHEN** Tab A 与 Tab B 对同一 session 并发创建 Run
- **THEN** 最多一个请求 SHALL 创建新 Run
- **AND** 其它请求 SHALL 收到 409 并能加入已创建的 Run

### Requirement: stop 与 HITL resume SHALL 按 Run 鉴权且幂等

`POST /api/chat/runs/{run_id}/stop` 与 `POST /api/chat/runs/{run_id}/hitl/resume` SHALL 按 `(run_id, current_user_id)` 鉴权，并再次确认 Run 与目标 session/assistant 关联一致。重复 stop SHALL 最多取消 producer 一次并产生一个 terminal transaction；重复或过期 HITL 命令 SHALL NOT 启动第二 producer。

#### Scenario: Tab B 停止 Tab A 创建的 Run

- **WHEN** Tab A 与 Tab B 使用同一账号订阅同一 active Run，Tab B 调用 stop
- **THEN** Run SHALL 只产生一个 `partial/stopped` 终态
- **AND** Tab A 与 Tab B SHALL 观察到同一权威终态

#### Scenario: Tab B 审批 HITL

- **WHEN** Tab A 与 Tab B 都显示同一 pending HITL，Tab B 提交有效决策
- **THEN** 系统 SHALL 在同一 `run_id` 与 `assistant_message_id` 上继续
- **AND** 两个 Tab SHALL 收到续跑事件与最终终态

#### Scenario: 旧 Tab 不能停止新 Run

- **WHEN** 旧 Tab 持有已终态 Run R1，同 session 已经启动新 Run R2
- **THEN** 针对 R1 的 stop SHALL NOT 影响 R2
