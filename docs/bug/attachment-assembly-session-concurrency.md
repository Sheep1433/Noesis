# 附件装配期共享请求级 AsyncSession 被 producer 并发使用

**状态**：✅ 已修复
**日期**：2026-09-03

## 现象

带附件会话的 Agent run 在装配期偶发 `InvalidRequestError: concurrent operations`（asyncpg/SQLAlchemy 异步 session 不允许并发使用）。触发链：`run_manager.start` 在返回前已启动 producer，producer 的检查点/终态落库与主协程装配期的附件探测（`session_has_attachments`）共用同一个请求级 `AsyncSession`。

## 根因

请求级 session 的生命周期跨越了 producer 边界——装配代码与 producer 并发操作同一 session。与子 Agent 隔离 loop 的 `run_on_main_loop` 纪律同一类问题：跨执行边界不能共享绑定于特定执行上下文的 DB session。

## 修复

附件链路全部改为自开短命 session，不再接收调用方 session：

- `chat_attachment_tools.resolve_attachment_tools`（新增单一入口，主/子 Agent 装配点共用）：装配期探测自开 session，有附件才装配工具——顺带消除了 `super_agent`/`common_qa` 两处逐行相同的装配块（Duplicated Code）。
- `build_attachment_tools` 去掉 `db` 参数：工具在 Agent 运行期内被调用（此时请求级 session 可能仍被 producer 使用），每次调用自开短命 session。

`super_agent`/`common_qa` 装配点的 `db is not None` 门槛保留：它区分请求上下文与无 DB 的 headless/eval 场景（`backend/evals/agent/_agent.py` 不传 db），不是残留——附件探测虽已可自开 session，但 eval 上下文不应引入会话附件的 DB 依赖。

## 修复方与验证

- 修复方：另一 Agent（2026-09-03），本仓库评审后收敛为 `resolve_attachment_tools` 单一入口并补本文档。
- 验证：后端全量绿；装配块去重后 `attachments_enabled` 标志语义保持（common_qa 的 system prompt 分支）。
