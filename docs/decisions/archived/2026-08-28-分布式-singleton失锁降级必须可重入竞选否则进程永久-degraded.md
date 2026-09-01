# 决策：分布式 singleton：失锁降级必须可重入竞选，否则进程永久 degraded

状态：implemented
日期：2026-08-28
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**问题/症状：** 分布式 SSE 的 leader 晋升循环是「一次性」的（`return` 结束）——leader 失锁降级后永远不会重新竞选，进程永久停留在 degraded；且失锁时只停了 live run，scheduler/memory/telegram/feishu 这些 singleton 都还在运行。

**根因：** 把「竞选+执行」写成了单次执行链，假设「获锁即持有期」；没有把「失锁→降级→重选」当作一个可反复发生的状态机循环。失锁时降级动作不完整（只停部分 singleton），窗口期新 leader 与旧 singleton 并存产生双写风险。

**解法/取舍：** 改为常驻角色监督循环：leader 监听失锁信号 → 降级时停掉全部 singleton（不只是 live run）→ 回到竞争；支持任意次数「失锁-降级-重选」。补回归用例验证「失锁→降级→重新获锁→二次接管」完整链路。

**可迁移原则：**
- 分布式 singleton 的 leader 选举必须是循环（re-entrant），不是一次性断言；任何「获锁后只能走一次」的实现都可能出现失锁后无法自愈。
- 降级（失锁处理）必须把所有 singleton 一致停干净，别只停正在跑的主体——停一半等于让新旧两代同时活动。
- 涉及真实 DB/外部依赖的生命周期测试要 mock 掉连接层（如 RunCommandConsumer），否则测试结果依赖环境状态；本次正是被「真实 start 连真 dev DB」造的竞态暴露的。

**验证与遗留：** `eb790777` 已修；剩余 8.4 staging 演练（Redis 运行中重启、PG 短断、滚动发布/回滚）需双副本生产环境，Playwright 双 backend E2E 未真实执行。
