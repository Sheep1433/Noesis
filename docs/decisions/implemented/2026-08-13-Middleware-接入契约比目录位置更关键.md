# 决策：Middleware 接入契约比目录位置更关键

状态：implemented
日期：2026-08-13
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**问题/症状：** `create_agent` 装配 `SubAgentContextMiddleware` 时抛出 `AttributeError: type object 'SubAgentContextMiddleware' has no attribute 'wrap_tool_call'`；同时 middleware 曾被迁到顶层 `noesis/middleware`，与 Agent runtime 的 `agents/middlewares` 语义边界脱节。

**根因：** 文件放进“看起来合理”的目录，不会自动满足 LangChain/DeepAgents 的 middleware contract。参与 `create_agent` 的对象必须遵守框架识别的 `AgentMiddleware` 继承/方法覆盖与 hook 签名；如果只实现了自定义状态函数，却被当作完整 middleware 装配，就会在工厂扫描 hook 时直接失败。目录循环的真正风险是 factory 与 agents 场景模块双向 import，不是 middleware 是否位于 `agents/` 下。

**排查路径：** 先对照 `langchain.agents.factory` 的 middleware 分类和 hook 检测，再沿 `factory → stack → create_agent → task/subagent` 生产链追踪；同时对照 DeerFlow 的 `agents/` 包边界，区分包位置、装配顺序和运行时接口三件事。

**解法/取舍：** middleware 回到 `noesis/agents/middlewares/` 作为 Agent runtime 子包；每个自研 middleware 要么完整实现框架要求的 hook，要么只作为纯 helper/状态函数存在，不能伪装成 middleware。最终以真实 `create_agent` 和 DeepAgents `task` tool 验证，不能只测 mock handler 或纯函数。

**可迁移原则：** 框架扩展先验证 integration contract，再讨论目录风格；“能 import”不等于“能被运行时装配”。测试至少覆盖工厂装配、真实 hook 调用和长时 subagent 生命周期。

**验证与遗留：** 已定位当前 AttributeError 的接口契约原因；isolated/fork/resume 的真实 task/checkpoint 接线、并行工具事件分组和刷新恢复仍需 E2E 验收。
