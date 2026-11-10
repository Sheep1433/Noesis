# 生产化待办：对照企业级项目的差距清单

> 来源：NextAgent（电信网络智能体 TS 框架）工程设计调研报告的对照分析（2026-11）。
> 用途：按优先级记录 Noesis 补齐"上生产"工程能力的待办；每条附事故/代码证据，开工前不必重新调研。
> 结论：Noesis 的弱项不在分布式形态（redis 总线、SKIP LOCKED 抢占、三角色拆分已领先于被调研对象），
> 而在单请求生命周期的契约化与交付事实的持久化。P0 三条各自对应一次已发生的事故。

## 已具备、不重复建设

提交幂等键（client_request_id + digest 唯一约束）；终态 CAS（mark_terminal compare-and-set + claim_epoch fencing）；
协作停止三段式（受理即落库 + 宽限 + 对账硬杀，成熟度超过被调研对象）；同会话单 active run 门控；
总线形态显式选择且配错 fail-fast（REDIS_URL 必填）；scheduler 的 SKIP LOCKED + 幂等键多实例安全。

## P0 不做会出事故

- [ ] **终态与通知 outbox 化**：`executor.py` 的 `_finalize_task_sync` 对落库 fire-and-forget，且注释明确
  "落库失败不吞事件（照发通知）"——违反终态原子性。通知队列（`notifications._PENDING`）、唤醒计数
  （`bg_continuation._wake_counts`）纯内存，重启即丢。事故证据：定时任务 09:00 日报死锁（timed_out 通知
  等一个不会来的"下次交互"）。方案：终态 + 通知同一事务写 outbox 表，交付按会话 origin 策略驱动。
- [ ] **恢复语义声明化**：现状"重启 = 全部收口 interrupted/ERROR"，有界但零恢复且未声明边界。事故证据：
  `user_scheduled_task_runs` 重启后无任何收口路径，永久停在 running（无人知道它不自愈）。方案：按 durable
  facts（DB 终态 + outbox）重写启动对账，三类任务（子 Agent / 后台命令 / 定时任务）的恢复语义写进
  openspec 规格——能恢复什么、不能恢复什么、不能恢复的显式终态。
- [x] **基础设施守护**（2026-11-10 完成，见决策记录「生产基础设施守护」）：服务器看门狗
  （磁盘水位 + `/health` 探测，Telegram 告警，状态跃迁触发 + 恢复通知，crontab 5 分钟）；`deploy-remote.sh`
  清理后剩余 <10G 拒绝部署；`change-scope.py` 跨层耦合提示（后端启动形态/compose/配置样例 → 部署面对端核对）。
  上线首日即捕获真实水位事件（构建缓存涨至 90% 告警 → 清理回 80% → 恢复通知，双向链路实测）。事故证据：
  磁盘满 → Postgres 崩溃循环（登录 502）；worker-role-split 只改代码漏改部署面 → nginx upstream 指向已删
  服务名，502 中断 35 分钟。

## P1 可靠性

- [ ] **工具 replay policy 一等字段**：恢复策略从"收口 ERROR 绝不重放"（安全但零恢复）升级为三态决策
  （复用已存结果 / 带稳定幂等键重放 / 拒绝）。工具 descriptor 声明 IDEMPOTENT / NON_IDEMPOTENT，缺失按
  NON_IDEMPOTENT。是 P0 恢复声明的前置。
- [ ] **HITL 等待 deadline 与跨进程恢复**：`hitl-required` 目前是"悬着的等待"——补 deadline、超时收口、
  崩溃后 pending 状态的恢复路径。
- [ ] **幂等键与观测脱敏**：`qa/service.py:59` 直接 `logger.info(f"query param: {req_obj.json()}")` 打全量
  请求；Langfuse 上报无脱敏纪律。开源后他人上生产的最先踩雷点。

## P2 可维护性

- [ ] **量化边界 + 增量治理**：ruff 接 PLR0915（函数行数）/ C901（复杂度）/ 循环依赖检查进 CI。新增代码
  必须合规；触碰的超限单元只许变好。证据：`executor.py` 2496 行七种职责。
- [ ] **同形同策纪律**：code-review skill 补一条"同类对象同一 owner/形态"检查。证据：`subscribe_run_events`
  与 `_delivery_core` 两份重复的 DeliveryCore 创建；`fail_session_shell_tasks` 名不符实；
  middleware 注释谎称"已查持久层"。

## 明确不做（已裁决，勿重开）

- 三通道内容审计、指标低基数 allowlist：强审计合规产物，观测已由 Langfuse 承担；
- 六审 + 人工门禁：单人开发场景，CI + code-review skill 足够；
- 存量代码同形同策全量标准化：取新增遵守之精神，不全量回改。

## 已裁决的关联方案

后台任务体系重构三步走（outbox → executor actor 化 + 模块拆分 → 暂缓进程拆分）见会话结论，
落地时与本文 P0/P1 合并为同一 openspec 变更提案，本清单为需求来源。
