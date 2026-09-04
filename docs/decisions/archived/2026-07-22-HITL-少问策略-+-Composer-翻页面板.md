# 决策：HITL 少问策略 + Composer 翻页面板

状态：implemented
日期：2026-07-22
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **少问**：`is_dangerous_execute` 仅网络出口 / pipe-to-shell；`rm -rf` 等沙箱内破坏命令不再打断。`/memory/**` 写入仍审批。
- **一点全过根因**：LangChain 要求 `decisions` 与 `action_requests` 等长；旧 UI `map(() => approve)` 把整批一次放行。
- **UI**：气泡内联卡移除；输入区上方（Todo 同槽）`HitlComposerPanel` 左右翻页，本地累积 draft，审批最后一条自动 resume，提问全部答完点 Continue 再 resume。
- **有 pending HITL 时优先显示面板、暂隐 Todo**。
