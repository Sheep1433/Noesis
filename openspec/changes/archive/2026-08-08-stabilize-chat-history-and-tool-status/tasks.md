## 1. 消息确定性顺序

- [x] 1.1 为 `t_chat_session` 增加 `next_message_sequence`、为 `t_chat_message` 增加 `message_sequence`，添加会话内唯一约束与查询索引
- [x] 1.2 编写 Alembic 回填：按稳定规则生成旧消息 sequence，修正 parent assistant 在 user 之后，并在加 NOT NULL 前执行完整校验
- [x] 1.3 在 `ChatService` 实现基于会话行锁的连续序号分配，禁止 Agent/网络调用占用该锁
- [x] 1.4 更新 `RunService.create`，在一个事务中为 user 分配 N、assistant 分配 N+1，并覆盖失败回滚
- [x] 1.5 更新直接消息、通道、测试用例/HITL 等所有 `TChatMessage` 写入口，确保不存在绕过 sequence 的新写入路径
- [x] 1.6 更新 `/api/chat/sessions/{id}/messages` Schema、排序与 `before_id` cursor，使其只按 `message_sequence` 工作
- [x] 1.7 更新前端历史初始化，校验 sequence 单调并按服务端顺序 replace；协议缺字段/重复时显示可恢复错误而不是角色猜序
- [x] 1.8 增加真实 PostgreSQL 回归测试，覆盖同毫秒 user/assistant、同会话并发分配、cursor 不漏同毫秒消息及刷新后 user → assistant

## 2. 会话标题

- [x] 2.1 将标题规范化与“仅默认标题可自动更新”收敛到 `ChatService` 的单一方法
- [x] 2.2 在 `RunService.create` 的消息事务内设置首条非空用户消息标题，验证任一写入失败时标题同步回滚
- [x] 2.3 扩展 Run 创建响应返回 `session_title`，更新前端 API 类型并在创建成功后立即更新当前标题与侧栏
- [x] 2.4 删除前端对 `finish.title` 的权威依赖及重复自动标题路径，保留用户手动改名优先级
- [x] 2.5 增加默认标题、已有自定义标题、空文本/纯附件、幂等重放与事务回滚测试

## 3. 工具权威状态模型

- [x] 3.1 在共享后端模块定义 tool `state` 枚举、允许的状态迁移及 `status/outcome/errorCategory → state` 映射
- [x] 3.2 扩展 `AssistantMessageBuilder` tool part，强制保存 state 及 outcome、exit code、timeout、duration、truncated 等完整字段，并保证重复终态幂等
- [x] 3.3 更新 `LangGraphSseBridge` 的 tool start/end/error 事件，使每次开始与结束都携带规范 state，且进程结果只读结构化协议
- [x] 3.4 更新 `RunProjection.apply`，完整保留 tool 字段，禁止投影时把失败终态退化为 running/success
- [x] 3.5 实现 Run 边界 reconcile：completed/error/partial/interrupted 前无非终态 part，HITL pause 只保留本次 action 与父 task 的 approval_pending
- [x] 3.6 更新 PersistSink、Run snapshot 与历史消息序列化，确保实时 SSE、snapshot、DB 三者对同一 tool_call_id 状态一致
- [x] 3.7 修正 Noesis 生成的 execute/bash 包装，保留真实 exit code 且不追加吞错 `|| true`；用户显式 shell 容错仍按 shell 语义
- [x] 3.8 增加网络失败、invoke timeout、process timeout、非零退出、empty、reject、stop、晚到事件和重复事件的后端状态矩阵测试
- [x] 3.9 增加并行工具 + HITL、父 task + 失败子工具的事件顺序与自底向上 reconcile 测试

## 4. 工具状态与失败 UI

- [x] 4.1 扩展前端 `UiPart`/SSE reducer，按服务端 state 幂等更新 tool part，snapshot/history 对当前 assistant 执行 replace
- [x] 4.2 更新 `ToolCallCollapse`，互斥展示正在执行、等待确认、已完成、执行失败、执行超时、已拒绝、已停止
- [x] 4.3 更新 `SubagentCollapse`，保留父子独立状态并正确显示包含 HITL action 的父 task
- [x] 4.4 保持 HITL subscription active 时授权按钮可用，仅在决策提交期间禁用，并覆盖刷新恢复场景
- [x] 4.5 为失败卡片实现默认折叠的安全详情，展示适用的短句、退出码/安全输出与建议，过滤路径、堆栈、provider 和网络内部信息
- [x] 4.6 从结构化 tool states 派生无正文阻断态与重试入口；有最终回答时不显示误导性完整性汇总
- [x] 4.7 增加前端 reducer/组件测试，覆盖日志失败后刷新不再 running、有回答时不显示汇总及 HITL 可点击

## 5. 集成验证与文档

- [x] 5.1 在真实数据库与浏览器完成标题、刷新顺序、网络失败、命令失败、HITL approve/reject、stop 和子 Agent 嵌套验收
- [x] 5.2 验证 Run 终态 assistant 的所有 tool parts 均为终态，并对历史数据抽样执行一致性查询
- [x] 5.3 运行后端相关测试与全套测试，前端运行 `pnpm test`、`pnpm lint`、`pnpm build`
- [x] 5.4 按实现结果更新 `docs/architecture/platform/chat-streaming.md` 与工具状态工程文档，记录状态机、消息 sequence、标题事务和排障查询
- [x] 5.5 执行产品文案审计，确认工具失败和协议错误提示不泄露内部路径、环境变量、provider 或堆栈
