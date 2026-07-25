## Why

知识库问答缺少可持久化、可授权回放的来源信息，用户只能从工具输出猜测答案依据。旧方案把回合内数字角标、Qdrant point id 和平台 CitationCollector 直接塞进 harness，不仅破坏 harness 边界，也会把“检索过的片段”误报为“回答实际引用”。

## What Changes

- 新增 assistant 级知识库来源能力：工具输出标准化 provenance，平台在 `tool-output-available` 时登记 evidence，消息终态统一落库。
- 模型使用稳定 `source_ref` 标记来源；前端只在展示层映射为连续角标，避免多次检索、并行调用和 HITL resume 导致编号漂移。
- 明确区分 `cited` 与 `retrieved`：正文命中的来源才是“引用来源”，其余只能显示为“检索来源”，禁止 Top-K fallback 冒充引用。
- 来源点击改为 assistant message scoped API，由服务端重新校验用户、session 和 collection 权限；Qdrant point id 不作为公开 URL 契约。
- 正常完成、停止、断连和 HITL resume 复用 builder 中已登记的 source part，不再依赖 finish 阶段 contextvar collector。
- `/api/chat` SSE 新增向后兼容的 `sources-available` / `sources-finalized` 事件；旧客户端可忽略未知事件。

## Capabilities

### New Capabilities

- `chat-kb-sources`: 规定来源对象、稳定引用标记、引用/检索语义、落库、授权查看和前端展示。

### Modified Capabilities

- `knowledge-base`: 检索命中 SHALL 输出与向量存储实现解耦的标准化 provenance locator。
- `agent-profiles`: COMMON_QA SHALL 使用工具提供的 `source_ref`，不得自行编造文件名或数字索引。
- `platform-chat`: SSE、message parts 和终态持久化 SHALL 支持 sources，并保持历史回放一致。

## Impact

| 区域 | 影响 |
|------|------|
| Harness | `noesis.tools.kb_search_tool` 仅扩展结构化 provenance，不依赖平台 domain/service |
| 平台 | `noesis_server.domain.chat` 登记 source part；Delivery/Bridge 投递来源事件 |
| KB | 检索 hit 增加内部 locator；新增 assistant-scoped source detail service/API |
| 前端 | parts 归一化、SSE 消费、正文 source token 渲染、来源面板 |
| 安全 | 点击来源时重新验证 message ownership、session ownership 和 collection scope |
| 兼容 | API/SSE 为增量扩展；旧消息和旧客户端继续工作 |
