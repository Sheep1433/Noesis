## Context

- **现状**：`LangGraphSseBridge._handle_langchain` 在 `on_chat_model_stream` 只读取 `chunk.content` 并 `_emit_text_delta`；`docs/dev/langchain-stream-demo-implementation.md` 已定义 `reasoning_content` → `reasoning-*` 映射，但未落地。
- **LLM 工厂**：`get_llm()` 已支持 `qwen` → `ChatQwen`（`langchain-qwq`）、`openai`/`minimax` → `ChatOpenAI`；**无** `deepseek` 分支。
- **厂商字段（首版）**：

| 来源 | 流式增量常见位置 | 说明 |
|------|------------------|------|
| Qwen / DashScope | `delta.reasoning_content` → 宜写入 `AIMessageChunk.additional_kwargs["reasoning_content"]` | `langchain-qwq` 非流式 `_create_chat_result` 已映射；流式 `_convert_chunk_to_generation_chunk` **当前未映射**，需仓库内最小子类补全 |
| DeepSeek | `delta.reasoning_content` → `additional_kwargs["reasoning_content"]` | `langchain-deepseek` 已映射；`deepseek-reasoner` 需 `ChatDeepSeek` 而非裸 `ChatOpenAI` |
| OpenRouter 等网关 | `delta.reasoning` 或 `model_extra.reasoning` | 统一归一为 `reasoning_content` 再提取 |
| 其它 OpenAI 兼容 | 常无独立 reasoning 字段 | 依赖前端 `<think>` 兜底 |

- **前端**：`useSSEStream` 仅处理 `reasoning-delta`；`chat.vue` 对所有 `text-delta` 走 `appendTextDeltaWithRedactedThinking`。

## Goals / Non-Goals

**Goals:**

- `MODEL_TYPE` 路由：`deepseek` → `ChatDeepSeek`；`qwen` → `ChatQwen`（`langchain-qwq`）；其余 → `ChatOpenAI`。
- 桥接层从 chunk **统一提取**思考字符串并发出标准 `reasoning-*` SSE，持久化 `reasoning` part。
- 首版验收：**Qwen**、**DeepSeek** 在开启 `show_thinking_process` 时，聊天页可见 `ReasoningBlock` 流式更新。
- 无原生 reasoning 时，行为与现网一致：仅 `text-delta` + `<think>` 解析。

**Non-Goals:**

- 历史消息列表默认展示 reasoning（`initChatHistory` 仍只取 text，除非另开需求）。
- Claude `thinking`、Gemini `thought` 等未在首版实测的厂商（可在 extractor 预留别名，不阻塞交付）。
- 修改 `phase-*` 语义或 TodoList / Subagent 协议。

## Decisions

### 1. 单一提取器 + 厂商无关 SSE

在 `backend/core/reasoning_chunk_extractor.py` 实现 `extract_reasoning_delta(chunk: AIMessageChunk) -> str | None`：

1. `chunk.additional_kwargs.get("reasoning_content")`（非空字符串）
2. `chunk.additional_kwargs.get("reasoning")`（OpenRouter 等）
3. `getattr(chunk, "reasoning_content", None)`（防御性）
4. 返回 `None` 表示本 chunk 无思考增量

**不在** bridge 内写 `if model_type == qwen` 分支；厂商差异由 LangChain 集成写入 `additional_kwargs` 负责。

### 2. Qwen 流式：仓库内最小子类

新增 `NoesisChatQwen(ChatQwen)`（放 `backend/core/llm_util.py` 或 `backend/core/chat_models_qwen.py`），覆盖 `_convert_chunk_to_generation_chunk`：若 `choices[0].delta` 含 `reasoning_content`（或 `reasoning`），写入 `message_chunk.additional_kwargs["reasoning_content"]`（逻辑抄 `langchain-deepseek` / `langchain-qwq` 非流式路径）。

`get_llm()` 的 `qwen` 分支实例化 `NoesisChatQwen` 而非裸 `ChatQwen`。

### 3. DeepSeek：`langchain-deepseek`

- 依赖：`langchain-deepseek>=0.1`（与 lockfile 对齐）。
- `MODEL_TYPE=deepseek` → `ChatDeepSeek(model=..., api_key=..., base_url=..., timeout=..., max_retries=...)`。
- `deepseek-reasoner` / thinking mode：文档注明需 `model` 与官方 thinking 参数；工具调用场景依赖 integration 回灌 `reasoning_content`（使用官方包，不手写 payload）。

### 4. `LangGraphSseBridge` 状态机

在 bridge 实例上增加与 text 对称的 reasoning 状态：

- `_reasoning_open`、`_current_reasoning_part_id`、按 `run_id` 可在 ctx 记录（多轮 LLM 时 `on_chat_model_start` 关闭上一轮 reasoning/text，与 demo 文档一致）。

`on_chat_model_stream` 处理顺序（同一 chunk）：

1. 若 `extract_reasoning_delta(chunk)` 非空 → `_emit_reasoning_delta`（首次 `_emit_reasoning_start`）
2. 若 `content` 非空 → 先 `_close_reasoning`（reasoning-end），再 `_emit_text_delta`

`on_tool_start` / `_close_text` 前：`_close_reasoning`。

`show_thinking_process` 为 `false`（`ModelConfig`，大小写不敏感）：跳过步骤 1 及 reasoning SSE，仍允许前端标签兜底。

### 5. 前端互斥：原生 reasoning 优先

在 `chat.vue` 流式 ctx 增加 `nativeReasoningSeen: boolean`：

- 收到 `reasoning-delta`（或 `reasoning-start`）后置 `true`
- `onTextDelta` 中：若 `nativeReasoningSeen`，走普通 `appendTextDelta`；否则走 `appendTextDeltaWithRedactedThinking`

`finish` / 新 assistant 消息时重置标志。

`useSSEStream` 增加可选 `onReasoningStart` / `onReasoningEnd`（创建/完成 reasoning part 的 `partId` 与 status，与 text 对称）。

### 6. 兜底保留

- **不删除** `messageParts.ts` 中 `<think>` 解析与历史 `expandRedactedThinkingInPlainText`。
- 后端与前端均 **禁止** 对同一段内容同时走原生 reasoning SSE 与标签拆 reasoning（以后端不发标签正文 + 前端互斥为主）。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| `langchain-qwq` 升级覆盖子类行为 | 子类仅补 delta 映射；单测锁定 chunk → `additional_kwargs` |
| DeepSeek thinking + 工具调用 400 | 使用 `langchain-deepseek` 最新版；文档注明 `deepseek-reasoner` 限制 |
| 双通道重复展示 | 前端 `nativeReasoningSeen` + 后端不把 reasoning 写入 `content` |
| `ChatOpenAI` 接 DeepSeek 官方 API 拿不到 `reasoning_content` | 强制 `MODEL_TYPE=deepseek` 文档说明 |

## Migration Plan

1. 增加依赖并扩展 `get_llm()`，默认行为不变（仍为 `qwen` / `openai`）。
2. 部署 bridge + extractor；`SHOW_THINKING_PROCESS=true` 时验证 Qwen/DeepSeek。
3. 前端发布互斥逻辑；无后端 reasoning 的模型自动回落标签解析。
4. 回滚：关闭 `SHOW_THINKING_PROCESS` 或回退 bridge 提交，前端兜底仍可用。

## Open Questions

- （无阻塞）是否在历史消息详情页展示已持久化的 `reasoning` part——本变更 **不** 改变 `initChatHistory` 策略，若产品需要可另开 change。
