# 决策：Provider 网关掩盖协议风险：reasoning_content 不能只看是否报错

状态：implemented
日期：2026-08-08
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**问题/症状：** Noesis 通过 OpenCode 调用 DeepSeek，并且带工具调用；DeepSeek 文档曾要求后续 assistant 消息回传 `reasoning_content`，但线上没有出现 400。

**排查结论：**
- 当前部署走的是 `https://opencode.ai/zen/v1`，不是 DeepSeek 官方 endpoint；中转层可能替请求补齐或剥离字段，因此“没有报错”不能证明客户端协议正确。
- 8 月 8 日对官方 API、OpenCode 以及不同模型/调用方式做了对照，当前版本的简单多轮用例都返回 200；历史上 DeepSeek V4 thinking + tools + 多轮回放确实出现过 400，触发条件还会随模型版本、网关和序列化路径变化。
- `ReasoningAwareChatDeepSeek` 的价值是防御协议变化和保留模型上下文，不应把“当前网关能兜底”当成永久契约。

**可迁移原则：** Provider 兼容性要按「目标 endpoint + 真实工具集合 + 流式/非流式 + 多轮回放」做能力测试；中转网关能把错误隐藏一段时间，不能用一次成功请求替代协议验证。适配层保留低成本防御，但要记录事实、推测和验证范围。

**验证与遗留：** 当前简单用例未复现 400；仍需在切换官方 endpoint、不同 thinking 模式和工具回放场景时保留回归测试。相关 provider/SSE 适配入口见 `packages/harness/noesis/llm/` 与 `noesis.domain.chat.streaming`。
