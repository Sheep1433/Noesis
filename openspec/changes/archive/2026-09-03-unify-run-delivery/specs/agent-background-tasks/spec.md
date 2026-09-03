# agent-background-tasks · Delta

## MODIFIED Requirements

### Requirement: 子会话详情与事件流

子会话正文 SHALL 读取标准会话消息（`GET /sessions/{id}/messages`，与其他会话同一协议）；实时过程 SHALL 经 run 事件流订阅（`GET /runs/{run_id}/stream`），且事件词汇、编码与恢复语义 SHALL 与主会话 run 完全一致（见 `agent-delivery`）：帧级事件（`text-delta` / `reasoning-delta` / `tool-input-*` / `tool-output-available` / `context-update` / `stats-update` 等）+ run 级生命周期事件（`run.started` / `run.finished` / `approval.required` / `approval.resumed`）。checkpointer SHALL 只用于 LangGraph 执行恢复，SHALL NOT 作为产品消息读模型。

事件流契约：

- 连接 SHALL 先订阅、再取权威快照、按 sequence 连续性重放 durable 事件（transient 事件仅在线投递，不重放）；SHALL NOT 存在按事件类型的序号豁免白名单；
- 前端 SHALL 从帧级事件自组装 assistant 投影，投影函数族与主聊天为同一实现；SHALL NOT 依赖服务端 `message.updated` 全量投影事件；
- `run.finished` SHALL 终止流并发送 `[DONE]`；
- 客户端断开（关闭详情抽屉）SHALL 立即退订（generator finally），终态 run 只发快照 + `[DONE]`、不建立订阅；
- 断流自愈 SHALL 与主聊天同模式：有界重试 + 权威 run 快照收口；重试耗尽且 run 非终态时 SHALL 向用户展示可感知的失败/重连入口，SHALL NOT 静默停留在「生成中」。

前端 SHALL 复用主 Agent 的消息渲染组件（Markdown / 工具块 / 审批卡 / 输入框）；父会话只展示带 child session 引用的轻量卡片，目录与卡片打开同一详情视图。

#### Scenario: 断线重连恢复

- **WHEN** 详情抽屉重开或刷新后按游标重连
- **THEN** SHALL 从权威快照 sequence 之后连续重放 durable 事件，不重复、不丢消息
- **AND** 断线期间的 transient 事件 SHALL NOT 被补发，客户端状态仍与快照一致

#### Scenario: 帧级事件自组装投影

- **WHEN** 子 Agent run 产生 text delta 与工具调用
- **THEN** 详情抽屉 SHALL 以与主聊天相同的投影函数族从帧事件组装 assistant parts
- **AND** 流式中与终态后的渲染结果 SHALL 与落库消息回放一致

#### Scenario: 重试耗尽可见失败

- **WHEN** 详情抽屉的流订阅连续重试达到上限且 run 仍非终态
- **THEN** 视图 SHALL 展示连接失败提示或重连入口
- **AND** SHALL NOT 持续显示「正在生成」或可用停止按钮

#### Scenario: 关闭详情退订

- **WHEN** 用户关闭详情抽屉
- **THEN** SSE 订阅 SHALL 立即释放，不产生泄漏

#### Scenario: 越权访问

- **WHEN** 用户 A 读取用户 B 的子会话或 run 事件流
- **THEN** SHALL 返回 404 语义

## ADDED Requirements

### Requirement: 子 Agent run 写操作 SHALL 对齐主链路错误契约

子 Agent run 的写操作端点（stop、HITL resume、subagent-followup）SHALL 使用类型化异常映射：资源不存在 SHALL 返回 404，状态冲突（重复决策、非法状态迁移）SHALL 返回 409，SHALL NOT 以 500 或字符串嗅探表达业务冲突。`POST /api/chat/runs/{run_id}/stop` 对子 Agent run SHALL 返回 `RunSnapshot` 契约的响应体（status 覆写 stopping 的受理快照）。写操作族 SHALL 与 `hitl/resume` 一致实施 CSRF 校验。

#### Scenario: stop 响应为快照契约

- **WHEN** 用户对 running 子 Agent run 调用 stop
- **THEN** 响应 data SHALL 为 RunSnapshot 形状（含 id/status/sequence 等字段）
- **AND** SHALL NOT 因响应序列化失败返回 500

#### Scenario: 重复审批决策返回 409

- **WHEN** 用户对非 awaiting_approval 的子 Agent run 再次提交审批决策
- **THEN** 系统 SHALL 返回 409 与冲突语义文案
- **AND** SHALL NOT 返回 500

#### Scenario: 写操作 CSRF 一致

- **WHEN** 客户端不带 CSRF token 调用子 Agent run 的 stop / followup 写端点
- **THEN** 系统 SHALL 与 hitl/resume 一致拒绝请求
