## ADDED Requirements

### Requirement: LLM 工厂 SHALL 按 MODEL_TYPE 选用厂商 LangChain 集成

系统 SHALL 在 `backend/core/llm_util.get_llm()` 中根据 `ModelConfig.model_type`（环境变量 `MODEL_TYPE`）实例化聊天模型，且 SHALL 满足：

- `MODEL_TYPE` 为 **`qwen`**（大小写不敏感）时，SHALL 使用 **`langchain-qwq`** 的 `ChatQwen`（或项目内继承该类的最小子类，用于补全流式 `reasoning_content` 映射）。
- `MODEL_TYPE` 为 **`deepseek`** 时，SHALL 使用 **`langchain-deepseek`** 的 `ChatDeepSeek`。
- `MODEL_TYPE` 为 **`openai`**、**`minimax`** 或未在上述列表中声明的其它值时，SHALL 使用 **`langchain_openai.ChatOpenAI`**（兼容 OpenAI 协议 base URL）。

未配置的 `MODEL_TYPE` 或缺失必填密钥时，系统 SHALL 按现有约定在启动或首次调用时失败并给出明确错误，不得静默回退到错误厂商类。

#### Scenario: 配置为 qwen 时使用 ChatQwen

- **WHEN** 环境变量 `MODEL_TYPE=qwen` 且 DashScope 相关密钥与 base URL 已配置
- **THEN** `get_llm()` 返回的实例类名或模块路径 SHALL 表明其来自 `langchain_qwq`（或项目子类）

#### Scenario: 配置为 deepseek 时使用 ChatDeepSeek

- **WHEN** 环境变量 `MODEL_TYPE=deepseek` 且 DeepSeek API 密钥已配置
- **THEN** `get_llm()` 返回的实例 SHALL 为 `langchain_deepseek.ChatDeepSeek`（或等价导入路径）

#### Scenario: 配置为 openai 时使用 ChatOpenAI

- **WHEN** 环境变量 `MODEL_TYPE=openai`
- **THEN** `get_llm()` 返回的实例 SHALL 为 `langchain_openai.ChatOpenAI`

### Requirement: LangGraphSseBridge SHALL 从 AIMessageChunk 提取思考并发出 reasoning-* SSE

当 `ModelConfig.show_thinking_process` 为真（默认 `true`，大小写不敏感）时，在 `POST /api/chat/sessions/stream` 及共用 `LangGraphSseBridge` 的其它流式端点中，系统在处理 LangChain `on_chat_model_stream` 事件时 SHALL：

1. 通过统一的 **`extract_reasoning_delta`**（或等价函数）从 `data.chunk`（`AIMessageChunk`）读取思考增量，读取顺序 SHALL 为：`additional_kwargs["reasoning_content"]` → `additional_kwargs["reasoning"]` → 可选的 chunk 级 `reasoning_content` 属性；均为空则视为本 chunk 无思考增量。
2. 对非空思考增量，按 **`reasoning-start` → （一次或多次）`reasoning-delta` → `reasoning-end`** 发出 SSE `data:` JSON，字段 SHALL 与 `docs/prd/platform/SSE流式数据设计.md` 一致（含 `messageId`、`partId`、`textDelta`）。
3. 当同一 chunk 或后续 chunk 出现非空 **正文** `content` 时，SHALL 在发出 `text-delta` 之前结束当前 reasoning 块（`reasoning-end`）。
4. 在 `on_tool_start`、下一轮 `on_chat_model_start`（若实现区分 run）、流正常结束前，SHALL 关闭尚未结束的 reasoning 块。
5. 同步调用 `AssistantMessageBuilder.append_reasoning`（或等价）以写入 `content.parts` 中 `type: "reasoning"` 的片段。

当 `show_thinking_process` 为假时，系统 SHALL NOT 因本 Requirement 发出 `reasoning-start` / `reasoning-delta` / `reasoning-end`，且 SHALL NOT 改变既有 `text-*` 与 `tool-*` 帧。

#### Scenario: Qwen 流式返回 reasoning_content 时出现 reasoning 序列

- **WHEN** `MODEL_TYPE=qwen`，`show_thinking_process` 为真，且上游 `AIMessageChunk.additional_kwargs` 含非空 `reasoning_content`
- **THEN** SSE 输出 SHALL 包含至少一对 `reasoning-start` 与 `reasoning-delta`，且 `reasoning-delta.textDelta` 累积非空

#### Scenario: DeepSeek 流式返回 reasoning_content 时出现 reasoning 序列

- **WHEN** `MODEL_TYPE=deepseek`，`show_thinking_process` 为真，且 chunk 含非空 `reasoning_content`（经 `langchain-deepseek` 映射）
- **THEN** SSE 输出 SHALL 包含 `reasoning-delta` 帧，且 JSON 可被 `useSSEStream` 解析

#### Scenario: 正文开始后关闭 reasoning

- **WHEN** 已发出 `reasoning-start`，随后同一 LLM 轮次出现非空 `content` 增量
- **THEN** 系统 SHALL 在对应 `text-delta` 之前发出 `reasoning-end`，且同一 `partId` 的 reasoning 块不再追加 `reasoning-delta`

#### Scenario: 关闭 show_thinking_process 时不发 reasoning 帧

- **WHEN** `show_thinking_process` 为 `false`
- **THEN** 流式响应 SHALL NOT 包含 `type` 为 `reasoning-start`、`reasoning-delta` 或 `reasoning-end` 的业务帧

### Requirement: 思考提取 SHALL 具备 bridge 层自动化回归

系统 SHALL 为 `LangGraphSseBridge`（或与其共享的 reasoning 发射入口）提供自动化测试：向 `on_chat_model_stream` 合成事件注入带 `additional_kwargs.reasoning_content` 的 mock `AIMessageChunk`，收集输出字符串后 SHALL 断言：

- 存在合法 SSE 帧边界；
- 至少一段 `data:` JSON 的 `type` 为 `reasoning-delta` 且含非空 `textDelta`；
- 在后续注入带 `content` 的 chunk 后，存在 `reasoning-end` 再出现 `text-delta`。

#### Scenario: reasoning golden 断言

- **WHEN** 测试仅传入 reasoning 增量 chunk 后再传入 content chunk
- **THEN** 输出字符串中 `reasoning-delta`  SHALL 先于对应 `text-delta` 出现，且 `reasoning-end` 位于二者之间

### Requirement: chat 流式页 SHALL 原生 reasoning 优先于 redacted_thinking 兜底

在 `chat.vue` 流式路径中，当前端经 `useSSEStream` 收到 **`reasoning-delta`**（或 `reasoning-start`）后，本轮 assistant 消息 SHALL 标记为已收到 **原生 reasoning 流**。此后对 **`text-delta`** 的处理 SHALL 使用普通 `appendTextDelta`，**SHALL NOT** 再对同一段 `text-delta` 执行 `<think>` 标签拆分。

若整轮流式回合内 **未** 收到任何 `reasoning-delta`，前端 SHALL 继续对 `text-delta` 使用现有 **`appendTextDeltaWithRedactedThinking`** 兜底逻辑，行为与变更前一致。

`useSSEStream` SHALL 支持处理 `reasoning-start` 与 `reasoning-end`（回调或内部更新 part `status`），使 `ReasoningBlock` 在流式过程中与 `finish` 时状态正确。

#### Scenario: 有 reasoning-delta 时不拆 redacted 标签

- **WHEN** SSE 序列先出现 `reasoning-delta`，后出现含 `<think>` 子串的 `text-delta`
- **THEN** 该 `text-delta` 中的标签子串 SHALL 保留在 `text` part 或按纯文本追加，且 SHALL NOT 再生成额外 `reasoning` part

#### Scenario: 无 reasoning-delta 时仍走标签兜底

- **WHEN** 整轮 SSE 未出现 `reasoning-delta`，且 `text-delta` 含完整 `<think>…</think>` 对
- **THEN** 前端 SHALL 生成 `reasoning` part 并在 `ReasoningBlock` 中展示，与 `docs/dev/redacted-thinking-inline-parsing.md` 一致

#### Scenario: 原生 reasoning 流式展示

- **WHEN** 后端发出 `reasoning-delta` 且 `show_thinking_process` 为真
- **THEN** chat 页 SHALL 渲染 `ReasoningBlock`，且流式过程中标题为「思考中…」类状态直至 `reasoning-end` 或 `finish`
