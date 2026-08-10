## Context

Noesis 当前有两条独立但粒度不足的数据流：`backend/packages/harness/noesis/middlewares/context_metrics.py` 在最终模型调用前将 system message、messages 与 tools 合并估算为 `current_tokens`；`backend/noesis_server/domain/chat/streaming/langgraph_sse.py` 从 `AIMessage.usage_metadata` 累计 input/output/total。前端 `frontend/src/views/chat/messageParts.ts` 与 `useSSEStream.ts` 只接收总量。

DeerFlow（调研 commit `095092418ccf072aa866c0a663c4056c206091e5`）把 Provider usage 归属到 AI step、caller 和 subagent，但它的 context window 只估算 checkpoint `messages`，没有覆盖最终 system prompt、tools，也没有 system/Skills/conversation 分类。Noesis SHALL 参考其执行归属模型，不照搬其 context 估算入口。

主要约束：Provider 不返回 system、conversation、Skills 等业务来源明细；不同 OpenAI-compatible Provider 只保证部分 usage 字段；一次 Agent run 会有多次模型调用；最终 `ModelRequest.system_message` 可能已经合并基础 prompt、Skills、memory 和其它 middleware 注入内容。

## Goals / Non-Goals

**Goals:**

- 同时提供最新模型请求的 context breakdown 与整个 run 的实际 usage attribution。
- 在最终 `ModelRequest` 上统计，覆盖模型真正可见的 system、messages 和 tool definitions。
- 保留 Provider 的 cache/reasoning 明细，并按 caller、模型调用和执行步骤累计且去重。
- 通过兼容扩展现有 SSE 字段供 chat 页摘要和调试展示。
- 分类无法可靠识别时显式进入父分类或 `other`，不使用脆弱正则猜测。

**Non-Goals:**

- 不实现 Provider 账单对账或价格换算。
- 不改变 summarization、模型路由、Skills progressive disclosure 或子 Agent 调度策略。
- 不把线程历史累计 usage 当作当前 context window。
- 不要求所有 Provider 返回 cache/reasoning 字段。
- 不改会话上下文文件浏览面板。

## Decisions

### 1. Context 与 usage 使用不同的权威来源

`ContextMetricsMiddleware` 继续位于模型调用 middleware 尾部，在 handler 执行前读取最终 `ModelRequest`。其快照描述“下一次模型调用的输入构成”，每次调用覆盖同一 caller 的旧快照，不跨调用求和。

Provider `usage_metadata` 描述“已经发生的模型消耗”，由流式桥或共享 Agent event collector 按唯一 model run id 累计。run 总量可以求和，当前 context 不可求和。

替代方案是像 DeerFlow 一样从 checkpoint messages 事后估算。该方案无法覆盖 request-time system/tool 注入，故不采用。

### 2. 使用稳定的顶层分类，细分来源依赖显式 provenance

最终请求先按结构得到一定可判定的分类：

- `system`：最终 `request.system_message`；
- `conversation`：Human/AI 等普通历史消息；
- `tool_results`：`ToolMessage`；
- `tool_definitions`：最终 `request.tools` 序列化定义；
- `other`：无法归入以上类型的输入和协议差值。

Skills、memory、RAG、attachments 作为可选细分来源。对应 middleware/tool 在创建实际注入内容时写入 Noesis 内部 provenance 元数据；统计器只消费这些显式标记。基础 system 量等于 system 总量扣除已标记的 system 子来源，ToolMessage 子来源同理。内部 provenance SHALL 在发送 Provider 前剥离或存放于不会序列化到 Provider 的 request-scoped state，SHALL NOT 把调试元数据加入模型输入。

对于 `/skills/.../SKILL.md`、`/memory/...` 等文件工具结果，工具层可根据已经解析并校验的权威路径写 provenance；统计层 SHALL NOT 再用任意正文正则推断来源。没有 provenance 时保留在 `tool_results`，不强行归到 Skills。

替代方案是在最终 system prompt 中插入文本分隔符并解析。它会污染 prompt、影响 prompt cache，且容易随第三方 middleware 文案变化而失效，故不采用。

### 3. 分类 token 是估算，Provider 总量是实际值

分类计数优先使用当前模型公开 tokenizer；不可用时使用现有 `count_tokens_approximately`。快照携带 `estimated: true` 与计数方法。分类之和 SHALL 与本地 `current_tokens` 使用同一种计数路径，并允许 `other` 吸收本地序列化/framing 差值。

模型返回后不按比例重写各分类使其强行等于 Provider `input_tokens`。UI 可同时展示本地 context estimate 与 Provider actual usage，但 SHALL 标明语义不同。

### 4. Provider usage 保留规范化 detail

在不删除现有平铺字段的前提下，规范结构为：

```json
{
  "input_tokens": 100,
  "output_tokens": 20,
  "total_tokens": 120,
  "input_token_details": {
    "cache_read": 60,
    "cache_write": 0
  },
  "output_token_details": {
    "reasoning": 8
  }
}
```

归一层兼容 OpenAI/LangChain 常见的 snake_case、prompt/completion 别名；未知 detail 保留在受控字典中但不参与总量二次相加。缺失 detail 不补零，以区分“Provider 返回 0”和“不支持”。

### 5. Attribution 由 caller 与 step 两个正交维度组成

每次模型调用携带稳定 caller：`lead_agent`、`subagent` 或 `middleware`，并可携带 `model_id`、`step_kind`、`parent_tool_call_id`。平台按 model run id 去重后维护：

- `cumulative`：run 内总 usage；
- `by_caller`：caller 汇总；
- `by_model`：模型汇总；
- `steps`：调试用的有界调用记录。

`steps` SHALL 设置数量上限或只随语义模型完成事件持久化，SHALL NOT 随 token delta 无界增长。子 Agent usage 不再重复合并进主 Agent 调用后又参与总计；父 task 只保留引用或展示归属。

### 6. SSE 只做向后兼容扩展

`context-update` 保留 `current_tokens`、`max_tokens`、`used_percentage`，新增：

```json
{
  "estimated": true,
  "breakdown": {
    "system": 3200,
    "conversation": 5100,
    "tool_results": 1800,
    "tool_definitions": 1200,
    "other": 100
  },
  "sources": {
    "skills": 900,
    "memory": 300,
    "rag": 700,
    "attachments": 200
  },
  "caller": "lead_agent"
}
```

`usage-update` 与 `finish.usage` 保留现有 token 总量，新增 details、`by_caller`、`by_model`；step 明细仅在配置允许的调试展示中发送。事件仍经统一 RunEvent/SSE delivery 路线，不新建第二套接口。

### 7. UI 先展示摘要，调试明细按需展开

chat 页顶部保留轻量总量入口，展开后分成：

1. 当前上下文：占用比例、顶层 breakdown、可用的来源细分、估算标识；
2. 本轮消耗：input/output，以及 caller/model 汇总；cache/reasoning 等 Provider 明细仅在调试视图按需展示，不进入默认摘要；
3. 调试视图：按步骤展示有界 attribution，并可按需展示 cache/reasoning 等 Provider 明细。

Noesis 当前配置的 OpenAI-compatible / DeepSeek 系 Provider 普遍不返回差异化 cache 计费字段，终端用户也不关心 cache 使用率；因此默认 UI 只展示 input/output 与 caller/model 归属。Provider detail 在后端仍规范化保留（不丢数据、兼容字段），但不作为默认前端展示项。

旧事件、Provider 无 usage、分类缺失或部分字段异常时，UI SHALL 继续显示已有总量或不可用状态，不阻断聊天。

## Risks / Trade-offs

- [不同 tokenizer 与 Provider framing 导致本地估算有偏差] → 明确标记 estimated，保持 Provider usage 为实际值，不伪造精确对账。
- [第三方 middleware 注入内容缺少 provenance] → 归入稳定父分类；逐个为 Noesis 自有 Skills、memory、RAG、attachments 注入点补标记。
- [caller/step usage 重复累计] → 使用 model run id 去重，并为父 task 与 subagent 定义单一计入规则。
- [调试明细增加 SSE 与持久化体积] → 默认摘要、step 有界、禁止按 token delta 记录。
- [内部 provenance 泄漏给 Provider] → 在模型适配层契约测试中断言最终 wire payload 不含 Noesis 元数据。
- [全局 session registry 并发串数据] → provenance 与 attribution 优先随 request/run context 传递；现有 registry 仅作为 SSE 读取最新快照的短生命周期桥，并按 run/caller 隔离和终态清理。

## Migration Plan

1. 先扩展后端内部数据结构与测试，保持现有 SSE payload 不变。
2. 增加 Provider details、caller 去重和兼容字段；旧前端忽略新增字段。
3. 增加上下文顶层 breakdown，再为 Noesis 自有注入点逐步补 provenance。
4. 更新前端类型与摘要 UI，最后启用调试明细。
5. 部署后可通过配置关闭 breakdown/step 明细并回退到现有总量展示；既有字段和持久化数据无需迁移。

## Open Questions

- step attribution 是否需要跨刷新长期持久化，还是仅随当前 assistant parts 保存有界摘要；实现前根据现有 run snapshot 大小测试决定。
- 图片与多模态附件的 Provider token 规则差异较大，首期是否只显示 `attachments: estimated` 而不承诺精确 token。
