# 决策：删除运行时 owner 前先回看 spec

状态：implemented
日期：2026-08-15
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**问题/症状：** 清理 runtime 时发现 governor、thread context、context snapshot、旧 retry/预算状态和若干 delivery 事件从未接入真实路径，代码量大但没有运行时价值；直接删除后又暴露出工具循环检测、subagent 并发/总数/深度限制仍写在 spec 中。

**解法/取舍：** 先用调用方搜索确认死代码，再删除未接线的 owner、数据库列和空转事件；同时把运行预算从 Run Governor 改成独立 Agent middleware 的契约。工具循环与 subagent 限制不能继续依赖被删除的 governor，必须另立职责清晰的 middleware 和验收场景。

**可迁移原则：** “代码存在”不等于“能力存在”，而“删除死代码”也不等于“需求消失”。清理前要把 spec 要求映射到真实调用链；若需求仍有效，先迁移到可观测、可测试的独立组件，再删除旧 owner。

**验证与遗留：** 8/15 已删除旧预算/重试死代码并同步 agent-delivery、platform-chat、agent-runtime spec；工具循环和 subagent 限制尚待独立 middleware 实现。
