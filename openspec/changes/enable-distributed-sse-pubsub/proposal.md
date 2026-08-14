## Why

当前可靠 SSE 使用进程内 `RunManager`，并让未取得 PostgreSQL advisory lock 的 backend 直接启动失败。它能可靠服务多个用户和多个 Tab，但无法增加 Web/SSE worker；请求落到非 Run owner 进程时，也不能实时订阅、停止或提交 HITL。

当前单 worker 容量尚未证明是 Agent 执行瓶颈。本变更因此只解决 Web/SSE 横向扩展：保留一个 execution leader，通过 Redis Pub/Sub 向多个 Web worker 广播事件，并以 PostgreSQL 保存 Run 与命令的权威状态，避免过早引入按 Run lease、fencing 和运行中迁移。

## What Changes

- 将现有全局 PostgreSQL advisory lock 从“第二个 backend fail-fast”改为“选举一个 execution leader”；其它进程保持 ready，作为 Web/API/SSE worker。
- 任意 worker 均可处理 `/api/chat/runs*`。创建请求先事务性写入 queued Run；execution leader 通过 Redis wake-up 或数据库补扫启动 producer。
- queued Run SHALL 保存经过命令改写和模型解析后的不可变启动输入，leader不得依赖创建请求进程中的 `CreateRunRequest` 或 `CurrentUser` 对象。
- 引入 Redis Pub/Sub，execution leader 发布带 sequence 的 RunEvent；非 leader worker 订阅后向本进程多个 Tab fan-out。
- PostgreSQL 继续保存 Run、assistant snapshot、sequence 和终态；Redis 不保存权威状态，也不承担断线重放。
- stop 与 HITL resume 先写入 PostgreSQL durable command，再用 Redis 唤醒 execution leader；丢失通知时由 leader 补扫。
- 远端订阅采用“先订阅并缓冲，再读取 snapshot，再按 sequence 合并”的握手；active Run 增加有界周期 checkpoint，确保 Pub/Sub 丢消息后能最终恢复。
- leader 丢失后，新 leader 先将旧 leader 的 running/retrying/HITL Run 收口为 `interrupted/server_restart`；不自动重放模型或工具。未被 claim 的 queued Run 可以继续启动。
- scheduler、memory dream 与 Telegram/Feishu runtime 只在 execution leader 启动，避免多 worker 重复执行。
- 增加可替换的 Run bus port、`memory` / `redis` 两个 adapter、健康状态、Docker Compose 服务、多 worker 集成测试和故障验收。
- 通过必填 `NOESIS_RUN_BUS_BACKEND=memory|redis` 显式选择运行模式；不允许连接失败后自动fallback。两种模式共用 queued dispatch、durable command、checkpoint、sequence 与订阅状态机，只替换实时通知 transport。
- `memory` 模式仅支持单 backend，第二个实例继续 fail-fast；`redis` 模式允许一个 execution leader 与多个 Web worker，leader/follower角色由PostgreSQL选举，不由环境变量指定。
- `/api/chat` 路径、现有 SSE wire event 与前端协议保持兼容；不保留当前“第二实例直接退出”的并行实现。
- 非目标：多个 execution leader、按 Run 分片、运行中 owner takeover、Redis Streams、跨地域 active-active，以及 `TEST_CASE_QA` 旧 SSE 迁移。

## Capabilities

### New Capabilities

- `distributed-run-coordination`: 定义单 execution leader、多 Web worker 下的 leader election、Redis RunEvent 广播、durable command、故障恢复与可观测性。

### Modified Capabilities

- `platform-chat`: 允许任意 worker 接受 Run 创建、查询、SSE、stop 与 HITL，并规定远端订阅的 snapshot/sequence 恢复。
- `container-deployment`: 增加 Redis 依赖和多 Web worker 部署，取消第二个 backend 必须 fail-fast 的限制，同时保持 singleton runtime 只运行一份。

## Impact

- 后端：`noesis.chat.runs`、Run service/repository、`/api/chat/runs*`、lifespan、recovery 与 health/readiness。
- 数据库：新增 durable Run command 表；现有 `owner_instance_id` 用于记录 execution leader，不新增按 Run lease/fencing 状态机。
- 基础设施：新增 Run bus adapter、Redis 客户端、配置与 Compose 服务。分布式生产环境需配置 Redis 认证、TLS、超时和连接池。
- 前端：协议不变，补充连接不同 worker 的多 Tab E2E；用户不感知 execution leader。
- 运维：新增 leader、Pub/Sub、远端订阅、sequence gap、command 与 Redis 故障指标。
