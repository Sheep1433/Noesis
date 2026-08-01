## Why

Noesis 需要在知识库问答和深度研究回答中展示来源，但不同模型对 structured output、虚拟 Tool 和长答案 JSON schema 的兼容性不稳定，并会破坏正常 token streaming。Qwen-Agent、AgentScope Deep Research 和 MS-Agent 的源码调研表明，成熟的通用路线是保留检索来源元数据，同时通过 system prompt 要求模型在普通 Markdown 正文中生成引用。

本变更采用 Prompt citation：不修改 `create_agent` 的回答协议，不为引用设置 `response_format`，不把最终答案提交为虚拟 Tool，也不再维护正文 offset annotation。

## What Changes

- COMMON_QA 和 SUPER_AGENT_QA 共用引用 Prompt：Web 来源输出 `[标题](原始 URL)`；KB 来源使用 `[n]`，并在文末 `### 参考资料` 中列出文件名、Collection 和可用定位信息。
- Agent 始终输出普通 Markdown，沿用现有 token streaming、消息落库与历史加载链路。
- 检索工具继续保留稳定 source metadata 和独立 retrieval part，供折叠查看与调试；retrieved 不自动等于 cited。
- 删除 `CitedAnswer`、provider allowlist、structured response adapter、typed answer events、text annotations、citation resolve API 和前端 offset marker 注入。
- 前端通过现有 Markdown renderer 展示模型输出的引用；Web 链接遵守外链安全策略。

## Capabilities

### New Capabilities

- `chat-kb-sources`: 规定 Prompt Markdown citation、来源元数据和检索结果展示行为。

### Modified Capabilities

- `knowledge-base`: 检索结果提供文件名、Collection、URL 和可用 locator，供模型生成引用。
- `agent-profiles`: COMMON_QA 与 SUPER_AGENT_QA 使用同一 Prompt citation 约束，保持正常文本回答。
- `platform-chat`: 持久化普通 Markdown text 和独立 retrieval results，不增加 citation 专用流式协议。

## Non-Goals

- 不承诺程序化 claim-to-source 绑定、语义 verifier 或引用正确率 100%。
- 不从 Top-K 自动生成“答案依据”。
- 不为 KB 编造可点击 URL；没有安全文档链接时使用编号和参考资料列表。
- 不保留 typed citation、annotation 或 citation resolve API 的历史兼容实现。
