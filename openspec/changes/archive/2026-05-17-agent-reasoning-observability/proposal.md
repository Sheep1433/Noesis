## Why

Noesis 已能通过 `LangGraphSseBridge` 向前端推送文本、推理与工具事件，但研发与运维仍难以在专用界面中查看「一次问答 → LLM/工具调用链」的完整痕迹与耗时。参考 **Aix-DB** 的落地方式：以 **Langfuse** 为观测后端（可选开启）、LangChain/LangGraph 侧挂 `CallbackHandler`、延迟导入避免拖垮启动。本变更以实现可读的 LLM 追踪效果为优先目标，**不追求后端可插拔或多协议扩展**。

## What Changes

- 引入 **Langfuse Python SDK**（版本与当前 LangChain/LangGraph 栈兼容），在 `backend/config/env.py` 对齐 **Aix-DB 风格**环境变量：`LANGFUSE_TRACING_ENABLED`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_BASE_URL`，默认关闭。
- 在 Agent 调用 LangGraph / `create_agent` 传入的 `config` 中，于开关开启时注入 `langfuse.langchain.CallbackHandler` 及 `metadata`（如 `langfuse_session_id` 与会话标识对齐），**仅在启用时延迟 import**，与 Aix-DB 一致。
- 不引入单独的 FastAPI/OpenTelemetry 接入作为交付范围；若 Langfuse SDK 内部使用 OTel，不作为本变更的设计义务。SSE 主协议**不 BREAKING**；如需辅助排障字段，仅允许可选、可关闭的增量键。
- （非目标）不实现故障运维「经验记忆」模块；不并行维护 Phoenix/Tempo/Jaeger 等第二套导出方案。

## Capabilities

### New Capabilities

- `agent-reasoning-observability`: 定义基于 Langfuse 的 Agent/LLM 追踪开关、配置项、与 LangGraph 运行绑定的回调注入方式及启动/降级行为。

### Modified Capabilities

- `chat-sessions-and-streaming`: 增补「可选」在启用 Langfuse 时，将**会话级引用**与结构化日志或 SSE 可选字段对齐（若有透出，须为非破坏性可选键）。

## Impact

- **代码**：`backend/config/env.py`；`backend/agent/**`（各 Agent `invoke`/`astream` 的 `config`）；`backend/services/qa_service.py`（传入 `chat_id`/`session_id` 等供 metadata）；必要时 `langgraph_sse_bridge` 仅打日志或可选字段。
- **依赖**：`langfuse`（及传递依赖，由 lockfile 锁定）。
- **运维**：自托管或云 Langfuse；文档说明四项环境变量与 Docker 启动参考（与 Aix-DB 文档同构即可）。
- **兼容**：默认关闭时行为与现网一致；Langfuse 服务不可达时问答主路径不得因遥测失败而 500。
