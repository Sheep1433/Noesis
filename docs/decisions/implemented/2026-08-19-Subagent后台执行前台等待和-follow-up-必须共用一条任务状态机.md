# 决策：Subagent：后台执行、前台等待和 follow-up 必须共用一条任务状态机

状态：implemented
日期：2026-08-19
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**问题/症状：** 初版后台子 Agent 只能通过 5 秒轮询看摘要，主 run 结束后无法继续查看细节；尝试跨事件循环复用主连接池时出现 `create_isolated_checkpointer` 缺失和 cross-loop 资源风险；原 steering 注入只能影响当前模型调用，无法自然续聊。

**解法/取舍：** 用专用守护线程事件循环和进程级任务注册表承载后台任务；同一个 `start_task` 工具通过 `run_in_background` 参数支持后台立即返回或前台等待，前台等待超过 120 秒自动转后台；checkpointer/httpx 在 worker loop 内惰性创建。`send_message` 改为子会话追加 follow-up turn，completed 可冷恢复，failed/timed_out/cancelled/one_shot 拒绝续话；子会话历史通过 checkpointer thread 只读查看，前端提供详情抽屉。

**可迁移原则：** 同步/异步不是两套工具，而是同一任务状态机的两种等待策略；跨事件循环的资源必须在目标 loop 创建；人与子 Agent 的沟通应追加真实 turn，而不是偷偷修改当前模型调用上下文。

**验证与遗留：** `6cdf05c` 已覆盖后台非阻塞、前台等待/超时转后台、审批续跑、follow-up、one_shot 和子会话读取；executor 契约 17/17、全量 1018 passed。运行中任务注册表仍是进程内存，进程重启恢复暂未实现。
