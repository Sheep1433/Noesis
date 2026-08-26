## ADDED Requirements

### Requirement: 用户级信令流

系统 SHALL 提供按用户订阅的信令流（`GET /api/chat/events/stream`），该用户任意会话的 run 状态迁移（running / hitl_pending / 终态）SHALL 向流内投递轻量信令（type + session_id + run_id + status），信令 SHALL 为「提示去拉取」的 hint——不承载 run 内容，队列有界满则丢弃，丢失不影响正确性。

#### Scenario: 列表实时刷新

- **WHEN** 用户停留在会话列表，任一会话的 run 发生状态迁移
- **THEN** 前端 SHALL 经用户级信令流实时 patch 对应列表行的 run_status（终态清除徽章）
- **AND** 信令对应的会话不在列表（他处新建）时 SHALL 全量刷新列表

#### Scenario: 首帧对齐

- **WHEN** 连接建立（含断线重连）
- **THEN** 服务端 SHALL 先下发该用户全部活跃 run 的定位符作为首帧
- **AND** 前端重连后 SHALL 全量刷新列表对齐

#### Scenario: 订阅上限

- **WHEN** 同一用户订阅数超过上限（每用户 16）
- **THEN** 新订阅 SHALL 返回 429 语义，提示关闭其它标签页

#### Scenario: 会话级信令不受影响

- **WHEN** run 状态迁移
- **THEN** 既有 `/sessions/{id}/events` 会话级信令的帧格式与语义 SHALL 保持不变
