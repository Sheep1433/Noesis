# 决策：run-terminal 信令在生产路径从未生效

状态：implemented
日期：2026-08-25
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **症状**：run 完成后前端徽章永远「运行中」，监听连接只收到 keepalive，`run-terminal` 帧不发。
- **根因**：信令只在 `transition()` 发布，但生产代码只有测试用例续跑路径调用它；主对话终态走 `apply_event → _commit_terminal_candidate` 直接赋值 `handle.status` 不发信令。hitl_pending 迁移同样只走直接赋值——会话级跨窗口信令两类在生产均从未生效。此前测试直接调 `transition()`，没走生产路径所以假绿。
- **修复**（`b643022`）：在真实状态赋值点补发信令三处（apply_event 普通分支状态变化时 / `_commit_terminal_candidate` 提交成功分支 / hitl_pending），补同状态幂等回归（测试用例续跑不双发）。
- **可迁移**：事件/信令等副作用挂在内部方法上时，测试必须覆盖生产调用链，否则「测试全绿」可能只是副作用和测试一起绕开了真实路径。
