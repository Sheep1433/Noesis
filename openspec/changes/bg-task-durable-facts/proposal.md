# 变更：后台任务事实源出内存落库

## Why

后台任务台（`BackgroundTaskExecutor` 与 `jobs/` 机制层）把任务目录、状态事实、followup 待执行消息与排队顺序全部放在进程内存（`jobs/registry.py` 的 `_TASKS` / `_PENDING_QUEUES`、`_TaskEntry.followups`）。审计记录 `docs/bug/background-executor-memory-state-audit.md` 确认了前三条后果；第四条（shell 任务零落库）为本次实现走查补充（审计列举的 `_SUBSCRIBERS` 跨进程漏发与状态双写两项，已由已落地的分布式设施覆盖或消解）：

1. **终态不回收**：`_TASKS` 收纳进程存活期内所有启动过的任务，唯一清理路径是 `shutdown()` 清空——长跑进程内存随任务数线性增长。
2. **追加消息蒸发**：工具路径 `update_async_task` 追加的消息先入内存 deque、转化为新 run 才落库，进程重启即静默消失，用户以为已下达「继续」实际从未执行（HTTP 路径已先写 user message，工具路径没有）。
3. **查询无 DB 兜底**：check/list/cancel 全部先查内存目录，miss 即「任务不存在」；回收终态条目或跨进程后查询无从回答。
4. **shell 任务零落库**：后台命令任务不写任何 DB 行，重启后除未送达通知外无任何可对账痕迹。

主 run 一侧的 durable 体系（advisory lock 单 active backend、启动对账、终态 retention）与 `enable-distributed-sse-pubsub` 已落地的设施（leader 选举、bg-tasks 信令桥、子会话 run 事件总线、durable stop 命令）都已就位，任务台未接入这套「DB 权威」原则。本变更把任务台的事实源对齐过去：**PG 是事实，内存只留执行热集**。审计文档建议的 Redis 队列与租约不再需要——执行面在已落地的 leader-only 架构下是单点，DB 行本身就是队列（见 design 备选方案）。

## What Changes

- **追加消息 write-ahead**：`deliver_message`（现 `deliver_followup`，followup 符号族随本变更更名，见 design 术语说明）收敛为唯一入口并保证先落库再入队；载体复用既有 child session 的 pending user message 行（`extra.pending_run` 标记，HTTP 路径已投产），工具路径并入同一模式，不新增表；重启对账把未投递行按所属任务去向分流（任务重建排队的保留入队，不可续终态的翻转 dropped，重启前已可续终态的保留供冷恢复重载）并在查询投影可见。
- **shell 任务落库**：新增 `t_bg_shell_job` 表，shell job 的状态事实与结果摘要落库，不再「重启即丢」。
- **查询 DB 兜底**：check/list/cancel/任务目录对内存 miss 的任务从 DB 投影回答（subagent 从 `t_chat_session`/`t_agent_run` 派生，shell 从 `t_bg_shell_job`），消除「任务不存在」误报；对 DB 已终态任务的重复取消 SHALL 幂等返回。
- **终态回收**：内存终态条目按 retention（默认 1h）与上限清理，回收后的查询由 DB 兜底——内存增长有界。
- **重启对账扩展**：现有 child run 收口（`error/SUBAGENT_PROCESS_RESTARTED`）之外，新增非终态 shell 行收口为 cancelled、未投递追加消息行按所属任务去向分流（任务重建排队的随任务保留，不可续终态的翻转 dropped，重启前已可续终态的保留供冷恢复重载）并在查询投影可见；排队任务按落库行重建进程内排队序。既有对账把 queued 的 child run 也收口为 error，本次将其排除出收口集合、改走排队重建。
- **冷恢复续聊对已回收任务生效**：`deliver_message` 对内存 miss 但 DB 可续的任务，从 child session descriptor 重建执行条目后开新 turn。
- **既有规格场景修正**：主规格「并发上限」场景原文为超限报错并清理 child session，与现状（超限进入会话内 FIFO 排队，`executor.py` 已如此实现）不符，本变更在该场景的重写中一并修正。
- **模型工具更名**：`update_async_task` → `send_message`——主规格「追加消息」条款本就以 `send_message` 为模型侧工具名，此次是代码向规格靠拢（不保留上游别名，静态引用点与在途影响面见 design 术语说明）；归档时主规格残留的 followup 措辞（协作式停止、输出截断等节）随本变更统一替换为「追加消息」。

不改变执行内核（隔离 loop、watchdog、协作停止、终态收口链）与 leader-only 执行面；不新增 Redis 数据结构；不触碰主 run 的 RunManager 与 advisory lock 体系（全应用多 worker 仍是独立改造线，见审计文档边界说明）。

## Capabilities

### Modified Capabilities

- `agent-background-tasks`：注册表语义从「内存为事实源（重启丢失为接受限制）」改为「DB 为事实源、内存为执行热集」；追加消息 write-ahead；shell 任务持久化与对账；终态回收与 DB 兜底；冷恢复覆盖已回收任务。

## Impact

- 后端存储：新表 `t_bg_shell_job` + Alembic migration（仅此一张新表；subagent 任务事实复用既有 `t_chat_session` / `t_agent_run` / `t_chat_message`，追加消息 write-ahead 复用 pending user message 行标记，均无迁移）。
- 后端机制层：`jobs/registry.py`（热集语义与回收）、`jobs/settle.py`（落库同步）、`agents/background/executor.py`（deliver_message 入口——followup 符号族随本变更更名、查询回退、冷恢复）。
- 后端服务层：`services/subagent_session_service.py`（DB 投影）、`services/run_recovery_service.py` 或同级对账装配（shell 与追加消息收口）、`services/agent_catalog_service.py`（shell 目录 DB 兜底）。
- 配置：`subagents` 配置组新增终态 retention 与回收上限两项。
- 前端：追加消息端点调用点更名（`/subagent-messages`）、pending 标记消息待执行状态与 dropped 标记消息「未执行」标注的渲染。
- 测试：`backend/tests/`（write-ahead、对账、回收、DB 兜底投影、冷恢复、投递失败回退）。
- 行为变更（非破坏）：重启前下达的追加消息不再静默消失（未投递的翻转 dropped 标记并可查）；重启前 running 的 shell 任务从「凭空消失」变为「收口为 cancelled 且注明原因」；长跑进程内存不再随终态任务数增长。
