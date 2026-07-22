## Why

SuperAgent 在 per-session 沙箱中可执行 `execute`、写入 `/memory/` 等持久化路径，当前工具调用「模型决定即执行」，缺少用户对高风险操作的确认，也无法在任务中途结构化地等待用户补充信息。deepagents / LangChain 已提供 `HumanInTheLoopMiddleware` 与 `interrupt_on` 能力，Noesis 尚未接入；需在 SuperAgent 场景落地「命令审批」与「主动提问」两条人机协同链路，且与现有 SSE 流式、checkpoint 落库兼容。

## What Changes

- 在 `create_noesis_agent` 透传 `interrupt_on`，挂载 `HumanInTheLoopMiddleware`（依赖既有 LangGraph checkpointer）。
- 新增 `ask_user` 工具：模型在任务中途澄清需求时调用，经 HITL `respond` 决策注入用户回答后继续推理。
- 为 SuperAgent 配置沙箱适度策略：`execute`（网络/pipe-to-shell）、`/memory/` 写入类文件工具条件审批；其余 workspace 读写、常规开发命令与沙箱内破坏性命令默认放行。
- 新增 SSE 事件 `hitl-required` 与 `POST /api/chat/sessions/{session_id}/hitl/resume`（CSRF 保护；成功返回**新** SSE，对齐 test-case/resume）；首段流以 `finish_reason=hitl_pending` + `[DONE]` 收尾且不 completed 落库。
- 前端聊天页：审批卡片（允许一次 / 本会话允许 / 拒绝）与澄清卡片（文本/可选项回答）；`hitl_pending` 后卡片可交互，resume 再开流。
- 任务入口的轻量澄清仍由 SuperAgent prompt 纯文本完成，**不**走 HITL。
- **非目标（首期）**：`FAULT_OPERATION_QA` MCP `bash` 审批、`FilesystemPermission` 对 `execute` 的静态路径 ACL、审批审计落库、多 interrupt 批量 UI 优化。

## Capabilities

### New Capabilities

- `agent-hitl`：LangGraph HITL 中间件装配、`ask_user` 工具、SuperAgent 工具审批策略、resume 编排与 session grant 语义

### Modified Capabilities

- `platform-chat`：SSE `hitl-required` 帧、`hitl/resume` API、assistant 消息在 HITL 等待期的状态约定
- `agent-super-agent`：SuperAgent 启用 HITL、`task-worker` 继承策略、与既有「交互分流」纯文本澄清的分工

## Impact

- 后端：`agent/factory.py`、`agent/super_agent.py`、新建 `agent/hitl/`（策略与 `ask_user` 工具）、`domain/chat/streaming/langgraph_sse.py`、`services/qa_service.py`、`api/chat_api.py`、`schemas/qa_vo.py`
- 前端：`useSSEStream.ts`、`chat.vue`、新 HITL 组件（审批/澄清）
- 配置：`config.yaml` → `hitl.*`（enabled、ask_timeout_seconds、策略开关）
- 依赖：复用已安装 `deepagents` / `langchain` 的 `HumanInTheLoopMiddleware`；**无新 pip 包**
- 兼容：配置 `hitl.enabled=false` 时行为与现网一致（非 BREAKING）；旧客户端不识 `hitl-required` 时流仍可结束于 interrupt 超时/拒绝策略
