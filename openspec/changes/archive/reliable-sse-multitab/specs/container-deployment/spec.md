## ADDED Requirements

### Requirement: P0 backend SHALL 使用 PostgreSQL advisory lock 保证单 active 实例

在未实现跨实例 owner claim、command routing 与 event transport 前，Noesis backend SHALL 只允许一个 active Uvicorn worker/backend 实例。每个实例 SHALL 在接收业务流量、执行 Run recovery 和启动 scheduler/channel runtime 之前，使用专用 PostgreSQL 连接获取固定 application advisory lock，并在整个 lifespan 中保持该连接。无法获取 lock 的实例 SHALL 启动失败，SHALL NOT 以降级模式接收流量。

#### Scenario: 误启第二个 Uvicorn worker

- **WHEN** 第一个 backend 已持有 advisory lock，运维再启动第二个 worker
- **THEN** 第二个 worker SHALL 在接收流量前启动失败
- **AND** 第一个 worker 的 active Run SHALL 不被第二个 worker recovery 为 interrupted

#### Scenario: 误启第二个 backend 容器

- **WHEN** 另一个 backend 容器使用同一 PostgreSQL 启动
- **THEN** 该容器 SHALL 因无法获取同一 application advisory lock 而启动失败
- **AND** 系统 SHALL NOT 只依赖 `WEB_CONCURRENCY` 或命令行 `--workers` 检查来保证单实例

#### Scenario: owner lock 连接丢失

- **WHEN** active backend 持有 advisory lock 的专用 PostgreSQL 连接断开
- **THEN** 该实例 SHALL 退出或变为 not-ready 并停止接收新 Run
- **AND** SHALL NOT 在不确定是否仍拥有 lock 时继续声称拥有 live Run

### Requirement: Run recovery SHALL 在单实例所有权确立后执行

backend lifespan SHALL 按以下顺序启动：初始化必要的 PostgreSQL 连接能力、获取 advisory lock、执行 migration/连接验证、收口上次进程留下的非终态 Run，再启动 checkpointer、knowledge base、scheduler 与 channel runtime。未持有 advisory lock 的进程 SHALL NOT 执行 Run recovery 或启动后台 runtime。

#### Scenario: 单实例重启收口旧 Run

- **WHEN** 新 backend 成功获取 advisory lock 且数据库存在上次进程留下的非终态 Run
- **THEN** recovery SHALL 保留最后一个原子 snapshot，将 Run 收口为 `interrupted`，将 assistant 收口为 `partial/server_restart`
- **AND** SHALL NOT 重放模型或可能产生副作用的工具

#### Scenario: 未持有 lock 不执行 recovery

- **WHEN** backend 进程无法获取 advisory lock
- **THEN** 该进程 SHALL NOT 调用 `recover_orphaned_runs()`
- **AND** SHALL NOT 启动 scheduled task、memory dream、Telegram 或飞书 runtime

### Requirement: 单 worker 容量 SHALL 可观测且通过负载验收

系统 SHALL 观测 active Run、active SSE subscription、event-loop lag、event-to-client latency、replay/subscriber bytes、subscriber overflow、checkpoint latency/sequence lag、terminal persistence 与 RunHandle 回收。实施 SHALL 以至少 100 active Run、每 Run 2–3 个 SSE subscription 的基线执行压力测试；不能仅根据 worker 数或配置上限声称容量达标。

#### Scenario: 基线压测不发生正确性丢失

- **WHEN** 系统运行 100 active Run、每 Run 2–3 个 subscription，并混入慢消费者与重连
- **THEN** 正常 subscriber SHALL 收到权威终态或明确可恢复状态，不得出现未处理 sequence gap
- **AND** 单个慢 subscriber 溢出 SHALL NOT 使其它 subscriber 或 terminal persistence 失败

#### Scenario: event loop 阻塞可定位

- **WHEN** event-loop lag 超过设计中的验收阈值或持续增长
- **THEN** 指标和日志 SHALL 能将问题关联到实例、Run 负载、subscriber 与 checkpoint 状态
- **AND** 系统 SHALL NOT 以直接增加 Uvicorn worker 作为未验证的修复
