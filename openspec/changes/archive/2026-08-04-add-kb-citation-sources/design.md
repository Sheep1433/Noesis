## Context

COMMON_QA 和 SuperAgent 都通过 LangGraph Agent 调用知识库或 Web 工具。原实现把最终回答改成 `CitedAnswer` structured response，再由 SSE bridge 将 segment binding 投影成 text annotation。这增加了 provider 门禁、虚拟 Tool、第二套文本拼接、offset 校验和前端专用渲染，并导致部分 OpenAI-compatible 模型返回 500 或失去正常流式输出。

外部源码核查显示，AgentScope Deep Research 的主实现让模型直接生成 Markdown citation；Qwen-Agent 只负责把 source 与 content 注入上下文；MS-Agent 虽维护 evidence store，最终报告引用仍由 Prompt 生成。因此 Noesis 采用同类边界：检索层保证来源数据完整，模型负责在普通答案中引用，平台不伪造语义绑定。

## Decisions

### 1. 引用属于普通 Markdown 正文

Web 和 KB 事实之后均紧邻输出编号：

```markdown
该项目提供 Deep Research 示例。[1]

### 参考资料

[1] AgentScope Samples — https://github.com/agentscope-ai/agentscope-samples
```

KB 事实使用编号并在文末列来源：

```markdown
验证码有效期为五分钟。[1]

### 参考资料

[1] 登录需求.md — Collection: requirement_docs，第三页
```

同一来源全文复用同一编号或链接。工具没有返回来源时不添加引用；模型不得输出内部 evidence/document/segment ID。

### 2. 不改变 Agent 输出协议

`create_noesis_agent` 不传 citation `response_format`。回答继续由 `AIMessageChunk.content` 产生 `text-delta`，最终只持久化一份正文。引用不新增 Tool，不监听 `structured_response`，也不新增 citation SSE event。

检索 Tool 只向 Agent 返回可读的来源元数据，不返回平台内部 `evidence_id`。SSE bridge 识别检索 Tool 输出后，由消息构建器为 retrieval result 分配内部稳定 ID。模型仍只看到 Markdown 友好的来源字段；平台在参考资料完成后使用这些字段回查本轮 retrieval identity。

### 3. Retrieval 与正文引用职责分开

检索工具继续返回 title、URL/file name、excerpt、Collection 和可用 locator。平台持久化独立 retrieval part，供回答末尾的来源入口和来源抽屉展示。

retrieval part 只表示工具返回过哪些资料。平台解析模型显式输出的参考资料，但只做来源身份匹配，不做语义充分性判断，也不从 retrieval score 或 Top-K 自动插入引用。

### 4. Prompt 是唯一引用生成规则

COMMON_QA 与 SuperAgent 共用一份 citation extension：

- Web 和 KB 统一使用 `[n]` 和参考资料列表；
- Web 参考资料逐字保留原始 URL；
- KB 参考资料逐字保留文件名、Collection 和可用 locator；
- 引用靠近对应事实；
- 不编造来源；
- 正常输出 Markdown，不包装 JSON，不调用答案提交 Tool。

子 Agent 的研究结果仍需保留来源 URL，使主 Agent 在汇总时可以继续引用。

### 5. 前端确定性匹配并渲染上标

前端在参考资料完整后解析编号。Web 以 canonical 原始 URL 为身份匹配，不要求模型生成的展示标题逐字一致；KB 按文件名与 Collection 匹配。唯一匹配才为正文中的 `[n]` 生成展示上标，不改写已持久化的 Markdown 内容。刷新时使用同一 text 与 retrieval parts 重建，不需要第二份正文或内存 annotation 恢复协议。

前端在 Markdown AST 上将已匹配的 `[n]` 渲染为可点击 `<sup>` 按钮。点击上标先打开当前回答绑定的来源抽屉，并滚动、高亮对应编号；上标本身不直接离开聊天页。用户点击抽屉中的 Web 来源后打开经安全校验的原始 URL；点击 KB 来源后在新标签页进入受认证保护的 Collection 详情页，并通过路由 query 打开对应文件的分片抽屉。分片抽屉必须在首次随路由挂载时立即加载，不能依赖后续 prop 变化触发请求。

正文实际出现对应 `[n]`、全部参考资料条目均唯一匹配成功、流式正文已完成且该段之后没有其他正文时，回答展示才隐藏仅供绑定使用的 `### 参考资料` 段；否则保留原始 Markdown，并将连续的参考资料条目按行分隔展示。界面不再展示占据正文宽度的“本轮检索结果”折叠块。原始 Markdown 始终完整持久化和复制。存在 retrieval results 时，在回答底部工具栏与 token 用量同行展示紧凑来源图标和去重后的来源文档数量；点击后打开统一“来源”抽屉，按“引用来源”和“其他检索结果”列出 Web 与 KB 来源。抽屉使用紧凑单行条目，只显示编号、单行省略标题与域名或 Collection，不展示 excerpt 正文；完整标题保留在 hover title 中。引用来源编号与正文 `[n]` 一致。Web 来源打开原始 URL，KB 来源进入对应 Collection 文档。

## Errors and Degradation

| 情况 | 行为 |
|---|---|
| 模型遗漏引用 | 正文照常完成；retrieval results 仍可查看，评测记录引用覆盖率 |
| 模型编造 URL | Prompt 明确禁止；集成评测判失败，不由平台猜测修复 |
| 工具无来源字段 | 不添加引用并说明依据不足 |
| 旧 Collection 缺少 document/version/segment identity | 命中标记为不可引用；重新入库生成身份，不增加历史兼容分支 |
| SSE 断连 | 后台继续生成并落库普通 Markdown；刷新读取权威快照 |
| provider 不支持 JSON schema | 不受影响，因为引用不使用 structured output |

## Risks / Trade-offs

- Prompt citation 不能保证每个事实与来源严格对应，但实现简单、模型兼容面广，并保留完整流式体验。
- KB 没有公开 permalink 时，前端使用受认证保护的 Collection 文档路由，不生成公开伪链接。
- Markdown 引用是正文的一部分，复制时会带走 `[n]`；这是预期行为，也便于引用随内容传播。
