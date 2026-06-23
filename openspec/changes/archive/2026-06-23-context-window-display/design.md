## Context

- **现状**：`usage-update` 展示消息级 API 计费 token；`SummarizationOffloadMiddleware` 内部 `token_counter(messages)` 与 `summarization.max_input_tokens`（0 时误回退 `generation.max_tokens`）仅用于摘要触发，不对用户可见。
- **目标 UX**（对齐 Cursor Composer）：输入框 footer 右侧环形 + `68%`；hover 显示 `87K / 128K` 绝对值。
- **约束**：数据须与后端 `before_model` 估算一致；不引入前端独立计数；SSE 为实时主通道；配置走 `config.yaml` + `ModelConfig`。

## Goals / Non-Goals

**Goals:**

- 会话级上下文占用率：`used_percentage = round(current_tokens / max_tokens * 100)`。
- 每次 LLM 调用前（及摘要/卸载后）推送 `context-update` SSE。
- `context.max_input_tokens` 可配置，作为展示分母与 summarization 触发基准的统一来源。
- Composer footer 常驻指示器；无数据时隐藏；历史会话从 `session.extra.context` 恢复末次快照。
- Hover tooltip：`{formatTokenCount(current)} / {formatTokenCount(max)}`（如 `87K / 128K`）。

**Non-Goals:**

- 子 Agent 独立上下文条、task 嵌套双层指示器。
- `TEST_CASE_QA` CaseCoordinator 路径（无 `create_noesis_agent` runtime 栈）。
- 用 `usage-update` 或 provider `usage_metadata` 推导上下文填充率。
- 管理后台动态改配置；本期仅 yaml/env。
- 摘要/卸载事件的独立 toast（仅通过 context 骤降体现）。

## Decisions

### 1. 数据通路：独立 ContextMetricsMiddleware + custom stream

**选择**：新增 `ContextMetricsMiddleware`，在 `before_model` / `abefore_model` 中：

1. `current = token_counter(state["messages"])`（与 `SummarizationOffloadMiddleware` 同源计数器，提取共享 `get_agent_token_counter()` 避免漂移）。
2. `max = resolve_context_max_tokens()`（读 `ModelConfig.context_max_input_tokens`，见决策 2）。
3. 若 `ModelConfig.context_display_enabled`，通过 `langgraph.config.get_stream_writer()` 写入 custom 事件 `{ "type": "context-update", "context": { ... } }`。

`LangGraphSseBridge` 新增对 `on_custom_event`（或仓库实测的 custom stream 事件名）的处理，格式化为 SSE `event: context-update`。

**理由**：计数点与摘要逻辑一致；不依赖 provider usage；`summarization_enabled=false` 时仍可展示。

**未采纳**：仅在 `SummarizationOffloadMiddleware` 内顺带 emit — 与摘要开关耦合，关闭摘要后无指标。

**未采纳**：bridge 在 `on_chat_model_end` 累加 usage 作上下文 — 语义错误（缺历史与 tool 膨胀）。

### 2. 配置：`context` 段统一上限

**选择**：

```yaml
context:
  max_input_tokens: 128000   # 0 = 读 model.profile.max_input_tokens，再无则启动告警并用保守默认
  display_enabled: true
```

- `ModelConfig.context_max_input_tokens`、`ModelConfig.context_display_enabled`。
- `SummarizationOffloadMiddleware._get_profile_limits()` 改为优先读 `context_max_input_tokens`，保留 `summarization.max_input_tokens` 仅作**废弃兼容**（非 0 时打 deprecation 日志并覆盖）。
- **移除** `_default_max_input_tokens()` 回退到 `generation.max_tokens`。

**理由**：输出 `max_tokens` ≠ 输入上下文窗口；展示与摘要须同一分母。

### 3. SSE 负载形态

```json
{
  "type": "context-update",
  "message_id": "<assistant_message_id>",
  "context": {
    "current_tokens": 87040,
    "max_tokens": 128000,
    "used_percentage": 68
  }
}
```

- `used_percentage`：整数 0–100，`min(100, round(current/max*100))`。
- `message_id`：与当轮 `message-start` 一致，便于前端关联；会话级 UI 仍取**最新一帧**覆盖。
- 与 `usage-update` **并存**，前端分别处理。

### 4. 持久化：session.extra.context

**选择**：每次 `context-update` 时，`qa_service`  checkpoint 或流结束将最新快照写入会话 `extra.context`：

```json
{ "current_tokens": 87040, "max_tokens": 128000, "used_percentage": 68, "updated_at": "ISO8601" }
```

加载会话列表/消息时，前端读 `session.extra.context` 初始化 footer 指示器；流式中由 SSE 覆盖。

**未采纳**：写入每条 assistant `extra` — 会话级指标重复冗余。

### 5. 前端 UI

**选择**：

- 位置：`chat.vue` 的 `chat-composer` 与发送行**下方**新增 `chat-composer-status` 行（右对齐），结构类似 Cursor：`[qa_type 标签可选] ... [ContextWindowIndicator]`。
- 组件：`ContextWindowIndicator.vue` — SVG 环形弧 + 居中百分比文字；`n-tooltip` 内容为 `` `${formatTokenCount(current)} / ${formatTokenCount(max)}` ``。
- 颜色：`<60%` 中性灰、`60–84%` 警告色、`≥85%` 接近摘要触发色（与 `summarization_trigger_fraction` 默认 0.85 对齐）。
- `useSSEStream` 新增 `onContextUpdate` 回调；`display_enabled=false` 或 `TEST_CASE_QA` 时不渲染。

### 6. 中间件顺序

在 `build_noesis_runtime_middleware()` 中，`ContextMetricsMiddleware` 置于 `SessionClockMiddleware` 之后、`SummarizationOffloadMiddleware` 之前，确保摘要**前**先 emit 高占用率，摘要**后**由下一次 `before_model` 或摘要返回路径再 emit 低占用率。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| `token_counter` 与 provider 实际偏差 5–15% | Tooltip 文案注明「估算」；不以 100% 硬阻断发送 |
| custom stream 事件名随 LangGraph 版本变化 | 桥接层单测 + golden；抽象 `_handle_custom_stream` |
| CaseCoordinator 无指示器 | 该 `qa_type` 隐藏组件，spec 明确 |
| 子 Agent 上下文与主 Agent 不一致 | 首期仅主 Agent thread；document 为已知限制 |
| session.extra 写入频率 | 与现有 stream checkpoint 同频，避免每帧写 DB |

## Migration Plan

1. 部署前在 `config.example.yaml` / 各环境 yaml 补充 `context.max_input_tokens`（建议与真实模型窗口一致，如 Qwen 128K）。
2. 若曾依赖 `summarization.max_input_tokens: 0` + `generation.max_tokens` 隐式上限，迁移后需显式设置 `context.max_input_tokens`。
3. 回滚：设 `context.display_enabled: false` 即可隐藏 UI；移除中间件不影响对话功能。

## Open Questions

- 是否在 `GET /api/chat/sessions/{id}` 响应中冗余 `context` 字段（除 `extra` 外）以便前端少解析一层？**建议**：首期仅用 `extra.context`，实现时按现有 session VO 形状扩展。
- 是否需要在接近 85% 时输入框旁展示文字警告？**建议**：首期仅颜色变化 + tooltip，不做额外 banner。
