## Context

COMMON_QA 和 SuperAgent 都通过 LangGraph Agent 调用知识库或 Web 工具。原实现把最终回答改成 `CitedAnswer` structured response，再由 SSE bridge 将 segment binding 投影成 text annotation。这增加了 provider 门禁、虚拟 Tool、第二套文本拼接、offset 校验和前端专用渲染，并导致部分 OpenAI-compatible 模型返回 500 或失去正常流式输出。

外部源码核查显示，AgentScope Deep Research 的主实现让模型直接生成 Markdown citation；Qwen-Agent 只负责把 source 与 content 注入上下文；MS-Agent 虽维护 evidence store，最终报告引用仍由 Prompt 生成。因此 Noesis 采用同类边界：检索层保证来源数据完整，模型负责在普通答案中引用，平台不伪造语义绑定。

## Decisions

### 1. 引用属于普通 Markdown 正文

Web 事实之后紧邻输出：

```markdown
该项目提供 Deep Research 示例。[AgentScope Samples](https://github.com/agentscope-ai/agentscope-samples)
```

KB 事实使用编号并在文末列来源：

```markdown
验证码有效期为五分钟。[1]

### 参考资料

1. 登录需求.md — requirement_docs，第三页
```

同一来源全文复用同一编号或链接。工具没有返回来源时不添加引用；模型不得输出内部 evidence/document/segment ID。

### 2. 不改变 Agent 输出协议

`create_noesis_agent` 不传 citation `response_format`。回答继续由 `AIMessageChunk.content` 产生 `text-delta`，最终只持久化一份正文。引用不新增 Tool，不监听 `structured_response`，也不新增 citation SSE event。

检索 Tool 只向 Agent 返回可读的来源元数据，不返回平台内部 `evidence_id`。SSE bridge 识别检索 Tool 输出后，由消息构建器为检索结果块分配内部稳定 ID；该 ID 只服务检索块去重、刷新恢复和 UI key，不参与模型引用生成。

### 3. Retrieval 与正文引用职责分开

检索工具继续返回 title、URL/file name、excerpt、Collection 和可用 locator。平台可持久化独立 retrieval part，供“本轮检索结果”折叠展示和调试。

retrieval part 只表示工具返回过哪些资料。平台不解析 Markdown 来推断 cited 子集，也不从 retrieval score 自动插入引用。

### 4. Prompt 是唯一引用生成规则

COMMON_QA 与 SuperAgent 共用一份 citation extension：

- Web 使用原始 URL 的 Markdown 链接；
- KB 使用编号和参考资料列表；
- 引用靠近对应事实；
- 不编造来源；
- 正常输出 Markdown，不包装 JSON，不调用答案提交 Tool。

子 Agent 的研究结果仍需保留来源 URL，使主 Agent 在汇总时可以继续引用。

### 5. 前端使用现有 Markdown renderer

前端不再按字符 offset 修改正文，不再维护 annotation patch 或 citation dialog。普通 Markdown 链接沿用 renderer 的安全外链行为。KB 编号引用作为正文和参考资料文本展示；未来若提供安全文档 permalink，可直接由工具返回 URL，无需改变 Agent 输出协议。

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
- KB 没有稳定 permalink 时引用不可点击；首版选择诚实展示文件名和定位信息，不创建伪链接。
- Markdown 引用是正文的一部分，复制时会带走 `[n]` 或链接；这是预期行为，也便于引用随内容传播。
