## Why

Noesis 聊天主路径依赖 `POST /api/chat/sessions/stream` 的长连接 SSE。工具调用或模型首包前长时间无业务帧时，易被反向代理/浏览器按空闲掐断，引发 `CancelledError`、assistant 落库竞态与半截 UI；TCP 分片还会导致尾帧解析遗漏。仓库已在 `docs/prd/platform/SSE流式数据设计.md` §6 与主规格中沉淀约定，本变更将其落实为可交付的实现与验证闭环（不引入第二套前端协议）。

## What Changes

- 在流式生成路径上增加 **SSE 注释行保活**（例如 `: keepalive`），间隔与 `config`/环境变量可对齐，默认落在 15–30s 量级；**不**改变现有 `event:`/`data:` JSON 业务帧形状，**不破坏**前端 `useSSEStream` 解析契约。
- **全链路超时对齐**：在 `config/env.py`（或等价）中声明可配置的流式读超时/保活间隔；文档或部署说明中明确 Nginx `proxy_read_timeout`、Uvicorn 与上游 LLM/MCP 超时与保活的关系。
- **写入侧断开语义**：向已断开客户端写入时，将典型连接类异常与业务异常区分日志级别，避免 BrokenPipe 刷屏为未分类 ERROR（不改变对外 API）。
- **契约测试**：为关键 SSE 帧（如 `message-start`、`text-delta`、`finish`、`error`、`[DONE]` 收尾）增加最小 golden 或黑盒用例，防止 `LangGraphSseBridge` 回归静默破坏前端。
- （可选）前端对保活注释行的 **显式忽略** 单测或注释说明，防止未来解析器误把注释当 `data:`。

## Capabilities

### New Capabilities

（无；可靠性归属既有聊天流式能力。）

### Modified Capabilities

- `chat-sessions-and-streaming`：在 delta 规格中细化「保活帧必须发出」「配置项名称与默认值」「断开时日志与取消语义」等可验收条目，与主规格 `SSE 传输稳定性与超时对齐` 章节互为补充；归档时合并进 `openspec/specs/chat-sessions-and-streaming/spec.md`。

## Impact

- **后端**：`backend/services/qa_service.py`（流式 async generator 外层或并列 tick）、`backend/config/env.py`（新增可选配置项）、`backend/api/chat_api.py`（必要时仅日志）、`backend/utils/langgraph_sse_bridge.py`（仅当保活与桥同文件更清晰时；否则保持桥只做业务帧）。
- **前端**：`frontend/src/views/chat/useSSEStream.ts`（若需注释行单测或解析健壮性微调）。
- **文档**：`docs/prd/platform/SSE流式数据设计.md` §6 与实现双向链接（实现落地后更新「未编码」表述）。
- **测试**：`backend/tests/` 或既有测试目录新增 SSE 形状/保活相关用例；`docs/test/test_tdd_design.md` 补充测试点（若仓库要求）。
- **依赖**：无新版本强依赖；部署侧需按文档校对 Nginx/网关超时。
