# 决策：平台层、Harness 与本地 CLI 的职责边界

状态：implemented
日期：2026-08-08
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**问题/症状：** 参考 YuXi 整理目录时，容易把 `noesis_server`、harness、middleware 和 CLI 按目录名机械合并，导致平台能力和 Agent 内核互相污染。

**最终边界：**
- `packages/harness/noesis/` 是 Agent 内核与运行时，承载 agents、services、domain、storage、repositories、knowledge、schemas 等业务能力。
- `noesis_server/` 是 HTTP 交付层，负责 routers、FastAPI middleware、lifespan、平台 wiring、数据库依赖和响应格式；它薄，但不是多余的。
- `packages/noesis-cli/` 是本地 harness CLI，直接复用 Agent 工厂和 in-memory checkpointer，不引入 TUI，也不为了评测额外引入 Cookie/API Key 认证。

**可迁移原则：** 参考项目只能提供依赖方向和职责问题的对照，不能把对方的 `utils/` 目录当成收纳箱。判断目录是否必要，要看它是否承担清晰的运行时边界；评测 CLI 应尽量走和评测 harness 相同的调用路径，避免为了“像产品”再复制一套 HTTP 层。

**验证与遗留：** harness boundary、后端和 CLI 测试在会话中通过；当前 Noesis 工作树仍有其他未提交改动，后续要单独做最终分支验收，不能把本次目录结论等同于整个分支已收尾。
