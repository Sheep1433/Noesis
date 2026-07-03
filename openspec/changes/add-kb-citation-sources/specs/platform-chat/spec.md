## ADDED Requirements

### Requirement: SSE SHALL 支持 citations-available 帧

`LangGraphSseBridge` 在启用 `CitationCollector` 的路径上，当 `finalize()` 返回非空 `items` 时，SHALL 在 **`finish` 之前**发出一次 **`citations-available`** 帧。

**时序**：

- 若本轮已发出 `text-end`，则 `citations-available` SHALL 在其后；
- 若本轮无 `text-end`（无正文流、仅工具或 fallback 场景），则 SHALL 在最后一个业务帧之后、`finish` 之前发出。

`data:` JSON SHALL 使用与现网一致的 **snake_case** 键名：

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 固定 `citations-available` |
| `message_id` | string | assistant `message_id` |
| `part_id` | string | citations part 稳定 id |
| `citations` | array | Citation 对象列表（snake_case 项） |
| `citation_fallback` | boolean | 是否触发无角标 fallback |

`citations` 每项 SHALL 含 `citation_id`、`index`、`collection_name`、`file_name`，及可选 `shard_id`（可为 null）、`chunk_index`、`header_path`、`snippet`、`score`。

SSE 载荷与落库 `CitationsPart.items` **SHALL 同形**（均为 snake_case），**SHALL NOT** 引入 camelCase 双套解析。

若无引用可展示，桥接层 SHALL NOT 发出该帧。

客户端 SHALL 允许忽略未知 SSE `type`。

#### Scenario: 有引用时发出 citations-available

- **WHEN** 流结束前 `CitationCollector` 产出 2 条 items
- **THEN** SSE 序列 SHALL 在 `finish` 之前含一条 `citations-available`
- **AND** `citations` 数组长度 SHALL 为 2
- **AND** 帧键名 SHALL 含 `message_id` 与 `citation_fallback`（snake_case）

#### Scenario: 无 text-end 时仍可发 citations

- **WHEN** 本轮无 text 流但有 KB 命中且 fallback 产出 items
- **THEN** SHALL 在无 `text-end` 情况下于 `finish` 前发出 `citations-available`

#### Scenario: 无引用时不发帧

- **WHEN** 本轮无 KB 检索命中
- **THEN** SSE SHALL NOT 含 `citations-available`

### Requirement: assistant multipart SHALL 支持 citations part

`domain/chat/message_builder.py` SHALL 支持 **`CitationsPart`**（`type: "citations"`），字段：

- `items`：Citation 数组（snake_case）；
- `citation_fallback`：boolean。

`AssistantMessageBuilder` SHALL 提供 `append_citations(...)` 与 `merged_text_content()`（仅合并 text parts）。

`finalize` citations 的共享逻辑 **SHALL** 被正常结束、`stop_chat`、`_persist_disconnect_partial` 三条落库路径调用（见 `chat-kb-citations`）。**仅 bridge 正常 `finish` 路径**发出 `citations-available`；partial 路径 **SHALL NOT** 要求客户端在 stop 当下收到该帧。

#### Scenario: 终态落库含 citations part

- **WHEN** 流正常结束且 finalize 产出 items
- **THEN** assistant 消息 `content.parts` SHALL 含 `type: "citations"`
- **AND** `items` 与 SSE `citations-available.citations` 语义一致

#### Scenario: partial 落库含 citations part

- **WHEN** 用户 `/stop` 且 finalize 产出 items
- **THEN** `status=partial` 的消息 SHALL 仍含 `citations` part

#### Scenario: stop 不强制 SSE citations 帧

- **WHEN** 用户 `/stop` 且未收到 `citations-available`
- **THEN** 行为 SHALL 仍符合平台 partial 约定
- **AND** 刷新后 **SHALL** 可从 DB 渲染 citations part

### Requirement: chat 页 SHALL 在 parts 循环中渲染 citations part

`chat.vue` SHALL 对顶层 `type: "citations"` 渲染 **CitationList**；`useSSEStream` 收到 `citations-available` SHALL 合并为 `CitationsUiPart`（读 snake_case）。

`messageParts.ts` 的 `normalizeApiContent` / `normalizeParts` **SHALL** 识别 `type: "citations"`（新前端）；未实现前旧前端跳过未知 type。

#### Scenario: parts 含 text 与 citations 的顺序

- **WHEN** 持久化 parts 为 `[text, tool, citations]`
- **THEN** UI SHALL 在正文区域下方展示来源列表

## MODIFIED Requirements

### Requirement: 流式问答与 SSE 核心契约

系统 SHALL 通过 `POST /api/chat/sessions/stream`（及设计文档中约定的同前缀端点）以 `text/event-stream` 输出 Noesis SSE 帧；事件流由 `LangGraphSseBridge` 从 LangGraph `astream_events` 转换，包含推理与文本增量、工具调用与输出、错误与结束标记，并以 `data: [DONE]` 收尾。

新增的 summarization offload、loop detection 与 dangling tool repair **SHALL NOT** 引入新的必选 SSE 事件类型；前端既有 `useSSEStream` 与 assistant multipart 渲染路径 SHALL 在不识别新增内部实现细节的前提下继续工作。

**本 change 增补**：`citations-available` 为**可选**业务帧；键名 **SHALL** 遵循现网 snake_case。实现本能力时 **SHALL** 同步更新 `docs/prd/platform/SSE流式数据设计.md` §2.2 事件表，与 spec 一致。

#### Scenario: runtime guard 开启时 SSE 仍兼容

- **WHEN** 系统启用 runtime guard 且客户端建立流式请求
- **THEN** 输出 SHALL 仍使用既有事件类型与 `[DONE]` 收尾

#### Scenario: citations-available 为可选帧

- **WHEN** 客户端未处理 `citations-available`
- **THEN** 流式 SHALL 仍可完成解析直至 `[DONE]`
