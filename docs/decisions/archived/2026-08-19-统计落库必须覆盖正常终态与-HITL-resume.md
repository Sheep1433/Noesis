# 决策：统计落库必须覆盖正常终态与 HITL resume

状态：implemented
日期：2026-08-19
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**问题/症状：** 统计条在实时运行中有数据，但刷新或重新打开会话归零；HITL pause/resume 后 usage 段可能被覆盖；`/statsline` 只显示本轮而不是完整会话。

**根因：** 现代 run 终态走 `repository.finalize` 时 extra 没有带 usage；HITL resume 复用同一 assistant message，resume 段直接覆盖旧 extra 会丢掉 pause 段；内存 registry 不能替代持久化。

**解法/取舍：** bridge 在 finish 注入 message usage，projection 捕获后传给 repository；`finalize` 采用 extra 合并、usage 字段累加；前端从消息历史汇总恢复 session stats，`/statsline` 只做展示模板定制，不改变统计真源。

**可迁移原则：** 运行统计是 durable 业务数据，不是纯 UI 状态；终态落库、HITL resume、刷新回放必须使用同一合并语义，不能只验证实时 SSE。

**验证与遗留：** `29df483`、`45c8091` 已补终态和 resume 合并测试；仍需验证跨进程恢复和不同 provider usage 字段的统一口径。
