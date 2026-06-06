## Context

- **现状**：`LangGraphSseBridge` 处理 `on_chat_model_stream`、`on_tool_start/end/error`，不处理 `on_chat_model_end`；`base_agent._stream_agent_response` 结束时固定 `yield {"type": "__tw_finish__", "usage": {}}`；前端 `useSSEStream` 已解析 `finish.usage` 但 `chat.vue` 的 `onUsageUpdate` 为空；`ToolCallCollapse` 无耗时展示；tool part 类型无 `durationMs`。
- **Agent 形态**：单轮 `create_agent` 与多轮 deep agent / StateGraph 均经同一 bridge 输出 Noesis SSE；**不**引入第二套 token 通路（如 AgentMiddleware 驱动 UI）。
- **Provider 行为**：DashScope/Qwen 的 `usage_metadata` 通常在该轮 LLM **结束**时可用（末 chunk 或 `on_chat_model_end`），流式生成过程中无法可靠获得 mid-stream output token。

## Goals / Non-Goals

**Goals:**

- 单次工具调用展示服务端测量的 **`durationMs`**（写入 tool part 与 `tool-output-available`）。
- 整条 assistant 回复展示 **累计 LLM token**（`input_tokens` / `output_tokens` / `total_tokens`），多轮 LLM 相加。
- 流式过程中每完成一轮 LLM 通过 **`usage-update`** 刷新 UI；`finish.usage` 与消息 `extra.usage` 一致。
- 历史消息从持久化 `parts[].durationMs` 与 `extra.usage` 恢复展示。

**Non-Goals:**

- TTFT、总 wall time、tokens/s、模型名 badge、费用估算。
- 会话级累计 token（输入框旁 badge）。
- 每轮 LLM 逐步明细 UI（debug 模式二期）。
- `TokenUsageMiddleware` 或 Langfuse 作为前端主数据源。
- 修改 `TestAssistant.vue`（除非复用同一 `MessagePartsRenderer` 组件时自动获益）。

## Decisions

### 1. 数据通路：统一走 `LangGraphSseBridge`（非 Middleware）

**选择**：在 bridge 的 `_handle_langchain` 中新增 `on_chat_model_end`（及必要时从 stream 末 chunk 读取 `usage_metadata`），维护 bridge 内部累计器；工具耗时在 `_on_tool_start` 记录 `perf_counter()`，在 `_on_tool_end` / `_on_tool_error` 计算差值。

**理由**：
- 前端只消费 Noesis SSE，与 Agent 类型（create_agent / deepagents / StateGraph）无关。
- deer-flow 的 `TokenUsageMiddleware` 仅打日志，UI 仍依赖 message 上的 metadata；Noesis 无 LangGraph SDK 消息列表，SSE 是唯一实时通道。

**未采纳**：AgentMiddleware 写 state 再由 service 读 — 需每个 Agent 类型重复接线，StateGraph 不适用。

### 2. Token 语义：消息级累计，非单次 LLM

**选择**：
- `usage-update` 与 `finish.usage` 的数值均为 **自 `message-start` 起所有已完成 LLM 调用的累计**。
- 每轮 LLM 结束后 emit 一次 `usage-update`；前端覆盖显示，不叠加心算。

**理由**：最小 UI 仅一行 token；用户关心「这条回复一共多少 token」。与 PRD 已有 `finish.usage` 消息级语义一致。

**去重**：用 `ctx["usage_seen_run_ids"]: Set[str]`，同一 `run_id` 的 usage 只计一次（stream 末 chunk 与 `on_chat_model_end` 可能重复，参考 deer-flow `counted_usage_ids`）。

### 3. SSE 事件与字段扩展

| 事件 / 字段 | 变更 |
|------------|------|
| `tool-output-available` | 新增可选 **`durationMs: number`**（≥0 整数） |
| **`usage-update`**（新增） | `{ type, messageId, usage: { input_tokens, output_tokens, total_tokens? } }`，累计值 |
| `finish` | **`usage`** 填真实累计；无 provider 数据时 `{}` 或省略各键 |
| tool part（DB） | 新增可选 **`durationMs`** |
| message `extra` | **`usage`** 与 `finish` 一致 |

**未采纳** `token-details`（旧 demo 命名）：与 Noesis 标准事件表不一致，统一用 `usage-update` + `finish.usage`。

### 4. `base_agent` 与 finalize 分工

**选择**：
- `base_agent` 仍 yield `__tw_finish__`，但 **不再携带占位 usage**（或携带空对象）；bridge 在 `_handle_tw_or_business` 处理 `__tw_finish__` 时 merge 已累计的 `ctx["usage_cumulative"]` 进 payload。
- `finalize()` 合成 finish 时同样填入累计 usage。

**理由**：避免 service 层双写；累计逻辑集中在 bridge 一处。

### 5. 前端展示位置（最小 UI）

**选择**：
- **工具耗时**：`ToolCallCollapse` header 右侧，格式如 `1.2s`；`status=running` 时可显示前端 live 计时（可选，首版可用静态「…」直至 output）。
- **累计 token**：assistant 气泡底部一行小字（或在 `AssistantReplyToolbar` 内左侧），如 `↑1.2K ↓340 · 共 1.5K`；无 usage 时隐藏该行。
- **`SubagentCollapse`**：若复用 tool part，同样展示 `durationMs`（task 工具也是 tool）。

**未采纳**：每轮 LLM 后插入 token 小字 — 信息过多。

### 6. 历史与流式统一

**选择**：累计 token 存 `message.extra.usage`；工具耗时存 `content.parts[].durationMs`。`MessagePartsRenderer` / `chat.vue` 历史路径读同一字段，不单独 Pinia。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| Provider 不返回 usage | UI 隐藏 token 行；finish 仍成功；不报错 |
| 同一 run_id 重复计数 | `usage_seen_run_ids` 单测覆盖 |
| CaseCoordinator 非标准 astream 事件 | 在 qa_service 合成路径验证；无 usage 则降级 |
| `durationMs` 含 SSE 网络延迟 | 以服务端 `perf_counter` 为准，不含客户端 RTT |
| 旧消息无字段 | 前端缺字段时不展示，不崩溃 |

## Migration Plan

1. 扩展 bridge + message_builder + golden 测试。
2. 调整 `base_agent` / qa_service finish 落库。
3. 前端解析 `usage-update`、`durationMs`，接线 UI。
4. 更新 PRD 与 test_tdd_design。
5. `uv run app.py` + `pnpm lint` + 手动多步 Agent 冒烟。
6. 回滚：前端忽略新键即可；后端新键为可选，旧前端兼容。

## Open Questions

- （无）Qwen `output_token_details.reasoning` 是否单独展示 — **首版不展示**，仅累计进 `output_tokens`（若 provider 已计入 total）。
