# 知识库与 Web 引用溯源研究

> 状态：Research
> 调研日期：2026-07-28，结论更新：2026-08-01
> 关联 OpenSpec：[`add-kb-citation-sources`](../../openspec/changes/add-kb-citation-sources/)

## 1. 调研目标与范围

本报告研究自建检索 Agent 如何在不改变正常对话协议的前提下输出来源引用。范围覆盖 `COMMON_QA` 的知识库检索和 SuperAgent 的 Web 检索，重点比较两条路线：

- provider 原生或 structured output 产生 typed citation；
- 检索工具保留来源元数据，模型按 system prompt 在普通 Markdown 中引用。

可验收行为以 OpenSpec 为准；当前数据流见 [`chat-streaming.md`](../architecture/platform/chat-streaming.md)。

## 2. Noesis 现状

### 2.1 当前调用链与边界

```text
KB/Web Tool
  → 向 Agent 返回 title、URL/file_name、excerpt、Collection/locator
  → Agent 按共享 system prompt 生成普通 Markdown 引用
  → LangGraphSseBridge 交付原有 text-delta
  → AssistantMessageBuilder 保存 text 与独立 retrieval part
  → 前端用通用 Markdown renderer 展示链接和参考资料
```

关键边界：

- Prompt 是唯一的引用生成规则，不修改 `create_agent` 的回答协议。
- Web 使用 `[标题](原始 URL)`；KB 使用 `[1]` 和文末 `### 参考资料`。
- 工具不向模型暴露平台内部 `evidence_id`。
- retrieval part 表示本轮检索到的资料，不等同于正文实际引用的来源。
- 平台不解析正文来推断 cited 子集，也不自动把 Top-K 插入答案。

### 2.2 约束

- Noesis 使用自建 Qdrant、hybrid retrieval 和 rerank，不能直接依赖某个 provider 的托管检索引用协议。
- 模型来源多样，引用机制不能要求所有 OpenAI-compatible endpoint 都稳定支持长 JSON schema 或 structured Tool output。
- 引用必须保留普通 token streaming、断线恢复和同一 assistant message 的终态持久化。
- KB 尚无稳定安全 permalink，因此首版诚实展示文件名、Collection 和定位信息，不创建伪 URL。

## 3. 外部方案

### 3.1 AgentScope Deep Research

- 已确认事实：Deep Research 示例让模型按报告 prompt 直接生成带来源链接的 Markdown；检索结果向报告生成阶段提供 URL 和内容。
- 设计分析：引用属于报告正文，不需要额外 answer Tool 或字符 offset annotation。
- 对 Noesis 的启示：研究子任务必须保留原始 URL，主 Agent 才能在最终汇总中继续引用。

### 3.2 Qwen-Agent

- 已确认事实：检索组件负责把 source 与 content 提供给模型，通用 Agent 框架不替应用生成平台专用 citation annotation。
- 设计分析：工具层保证来源数据完整，最终展示格式由应用 prompt 决定，适合多模型环境。
- 对 Noesis 的启示：KB/Web Tool 输出可读来源字段即可，不应承担最终答案协议。

### 3.3 MS-Agent

- 已确认事实：研究流程可以维护 evidence store，但最终报告中的引用仍由报告 prompt 生成。
- 设计分析：内部 evidence 管理和用户可见引用是两项职责；前者不要求把内部 ID 暴露给模型或用户。
- 对 Noesis 的启示：独立 retrieval part 可用于恢复、调试和结果折叠，但不能冒充模型实际引用。

### 3.4 OpenAI 与 Anthropic 原生 citation

- 已确认事实：OpenAI Responses 和 Anthropic Citations 都能返回结构化 citation；能力与各自托管工具、content block 或 provider 协议绑定。
- 设计分析：这类协议适合作为单一 provider 的原生能力，不适合作为 Noesis 自建检索和多模型 Agent 的公共回答协议。
- 对 Noesis 的启示：借鉴“来源元数据与正文分离”的边界，不复制 provider 专用事件和 annotation 模型。

### 3.5 RAGFlow 与 Dify

- RAGFlow 使用 prompt marker 和服务端映射，说明 prompt 引用可落地，但 parser/repair 会增加 marker 泄漏和流式截断处理。
- Dify 保存 retriever resources，适合展示检索来源，但 retrieved resources 不等于逐句语义归因。
- Noesis 只采用来源元数据与 prompt 约束，不增加 marker parser、引用修复器或 Top-K 自动归因。

## 4. 横向比较

| 路线 | 正常 token streaming | 多模型兼容 | 逐句结构化绑定 | 额外协议复杂度 | Noesis 选择 |
|---|---:|---:|---:|---:|---|
| Provider 原生 citation | 取决于 provider | 低 | 强 | 高 | 不作为公共协议 |
| Structured answer / 虚拟 Tool | 易退化为终态 | 中低 | 强 | 高 | 不采用 |
| Marker + parser/repair | 支持 | 中 | 中 | 中高 | 不采用 |
| Prompt Markdown citation | 支持 | 高 | 弱 | 低 | 采用 |
| Retrieval-only results | 不影响 | 高 | 无 | 低 | 作为辅助展示保留 |

## 5. 推荐方案

采用 Prompt Markdown citation：

1. COMMON_QA 与 SuperAgent 共用引用 prompt。
2. 检索工具返回模型能直接使用的来源元数据，不返回内部 evidence ID。
3. Agent 在事实附近生成 Web 链接或 KB 编号，并保持正常文本流。
4. 平台原样持久化 Markdown 正文，同时保存独立 retrieval part。
5. 前端用通用 Markdown renderer 展示引用；HTTP(S) 外链使用新窗口及安全属性。
6. 通过真实模型集成测试评估“调用检索工具、正文出现有效链接、终态仍保留链接”。

该路线不声称提供审计级逐句归因。模型漏引或错引由引用覆盖率和来源有效性评测发现，平台不猜测、不补写。

## 6. 不采用的方案

- 不向 `create_agent` 传 citation `response_format`。
- 不创建提交最终答案的虚拟 Tool。
- 不维护 `CitedAnswer`、typed answer segment、text annotation 或 citation 专用 SSE event。
- 不提供 citation resolve API 或前端字符 offset marker 注入。
- 不把所有 retrieval Top-K 自动显示为正文引用。
- 不保留上述旧路线的兼容代码。

## 7. 待验证问题

1. 长报告、多次检索和子 Agent 汇总时的引用覆盖率。
2. KB 文件名与 locator 的稳定展示质量。
3. 不同模型对“引用紧邻事实”和“同一来源复用同一编号”的遵循率。
4. KB 获得权限安全 permalink 后，是否将编号列表升级为可点击链接。

## 8. 资料来源

- [AgentScope](https://github.com/agentscope-ai/agentscope) 与 [AgentScope Samples](https://github.com/agentscope-ai/agentscope-samples)，源码核查日期 2026-08-01。
- [Qwen-Agent](https://github.com/QwenLM/Qwen-Agent)，源码核查日期 2026-08-01。
- [MS-Agent](https://github.com/modelscope/ms-agent)，源码核查日期 2026-08-01。
- [OpenAI Web Search](https://developers.openai.com/api/docs/guides/tools-web-search)，读取日期 2026-07-28。
- [Anthropic Citations](https://platform.claude.com/docs/en/build-with-claude/citations)，读取日期 2026-07-27。
- RAGFlow：`infiniflow/ragflow`，commit `53e83dcadfef`。
- Dify：`langgenius/dify`，2026-07-27 HEAD。
- Noesis 真实模型集成用例：`backend/tests/api/test_super_agent_real_llm.py`。
