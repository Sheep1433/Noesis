## Why

Noesis 需要在知识库问答和深度研究回答中展示来源，但不同模型对 structured output、虚拟 Tool 和长答案 JSON schema 的兼容性不稳定，并会破坏正常 token streaming。Qwen-Agent、AgentScope Deep Research 和 MS-Agent 的源码调研表明，成熟的通用路线是保留检索来源元数据，同时通过 system prompt 要求模型在普通 Markdown 正文中生成引用。

本变更采用 Prompt citation：不修改 `create_agent` 的回答协议，不为引用设置 `response_format`，不把最终答案提交为虚拟 Tool。模型输出普通 Markdown 编号，平台将能与本轮检索结果精确匹配的编号渲染为可点击上标。

## What Changes

- COMMON_QA 和 SUPER_AGENT_QA 共用引用 Prompt：Web 和 KB 均在事实后输出 `[1]`、`[2]`，并在文末 `### 参考资料` 逐字列出工具提供的标题、URL 或文件名、Collection 与可用定位信息。
- Agent 始终输出普通 Markdown，沿用现有 token streaming、消息落库与历史加载链路。
- 检索工具继续保留稳定 source metadata 和独立 retrieval part。平台只将参考资料中能与本轮 retrieval 精确匹配的条目登记为 cited，retrieved 不自动等于 cited。
- 保持删除 `CitedAnswer`、provider allowlist、structured response adapter 和虚拟答案 Tool；前端由 Markdown 编号和 retrieval results 派生展示绑定，不保存第二份答案。
- 前端将已匹配的 `[n]` 渲染为可点击上标；点击先打开当前回答的来源抽屉并定位对应条目，再由抽屉条目进入 Web 原始 URL 或受认证保护的 KB Collection 文档。

## Capabilities

### New Capabilities

- `chat-kb-sources`: 规定 Prompt Markdown citation、确定性来源匹配、可点击上标和检索结果分层。

### Modified Capabilities

- `knowledge-base`: 检索结果提供文件名、Collection、URL 和可用 locator，供模型生成引用。
- `agent-profiles`: COMMON_QA 与 SUPER_AGENT_QA 使用同一 Prompt citation 约束，保持正常文本回答。
- `platform-chat`: 持久化普通 Markdown text 和独立 retrieval results，供客户端确定性重建引用上标。

## Non-Goals

- 不承诺语义 verifier 或引用正确率 100%；但必须保证展示为可点击上标的引用来自本轮真实 retrieval。
- 不从 Top-K 自动生成“答案依据”。
- 不为 KB 编造公网 URL；KB 来源条目跳转到 Noesis 已受认证保护的 Collection 文档页。
- 不恢复依赖 structured answer segment 的历史实现。
