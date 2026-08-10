## Why

Noesis 已统一通过 harness factory 装配 Agent，但模型调用、上下文压缩、工具输出和运行预算仍由多个相互依赖的 middleware 分别处理，存在重复计数、触发时机偏晚和终止语义不完整的问题。参考 Codex 的显式 turn/context/tool/runtime 边界，本变更将公共运行时收敛为少量职责完整的生命周期组件，同时保持 Noesis 自己的通用 Agent 定位，不复制 DeerFlow 的细粒度 middleware 清单。

## What Changes

- 建立统一 Agent runtime outcome，表达模型成功、可重试传输失败、长度/安全终止、partial output、工具后空终态及 run governor 主动停止。
- 将 dangling tool-call 修复、上下文预算、compaction 与稳定上下文重建归入单一 Context Lifecycle；压缩后从权威来源恢复 Skills、Memory、任务信息等长期上下文。
- 将工具异常归一和有界输出归入统一 Tool Execution 边界；有 backend 时直接采用 DeepAgents 的 large-tool-result offload，无 backend 时执行一次有界 fallback。
- 将循环检测、工具调用限制、子 Agent 并发/总量/深度统一为 Run Governor；为后续累计 token budget 保留接入点，不在本变更重复实现 token attribution。
- 区分公共 runtime kernel、Agent Profile capability middleware 与 telemetry，删除被新边界替代的重复 middleware 和 token 计算。
- 建立全局 middleware inventory 与 Profile 矩阵：直接使用 DeepAgents Filesystem/SubAgent/AsyncSubAgent、LangChain TodoList/HITL，并明确 Noesis 自定义 runtime 与少量 capability adapter；第三方已完成的大结果 offload SHALL NOT 二次处理。
- Skills 使用来源 revision 使 checkpoint 缓存失效；Memory 在每次用户 turn 开始时加载一次并在该 run 内保持不变。两者均保留 DeepAgents 的发现、加载和 prompt 注入实现；附件从 middleware 迁为 Agent 调用前的 input resolver。
- 扩展现有 SSE/parts 终止原因，保持现有 `/api/chat` 路径和既有字段兼容；只新增可选字段或新的枚举值。
- 非目标：不移植 DeerFlow 全部 middleware；不新增通用 ReadBeforeWrite、全局文本转义、title/progress/upload runtime guard；不重写 LangGraph/LangChain Agent 框架。

## Capabilities

### New Capabilities

- `agent-runtime-lifecycle`: 规定 Model Execution、Context Lifecycle、Tool Execution、Run Governor 与 Runtime Telemetry 的统一职责、状态和装配约束。

### Modified Capabilities

- `agent-harness`: 调整统一 factory 的运行时装配契约，明确公共 kernel 与 Profile capability 的边界，并禁止保留职责重叠的并行运行栈。
- `agent-tool-failure-handling`: 将现有调用失败/执行结果语义接入统一 tool result envelope，并保证第三方 offload/fallback 不改变 status、category 与 outcome。
- `platform-chat`: 增加稳定的 Agent stop reason 在 SSE、parts 和最终消息中的兼容表达。

## Impact

- Harness：`backend/packages/harness/noesis/factory.py`、`runtime/`、`middlewares/`、工具 dispatch、子 Agent 装配与配置。
- 平台：`backend/noesis_server/domain/chat/streaming/`、RunEvent/SSE 映射、assistant parts 与终态持久化。
- 评测：Harbor、BrowseComp、Agentic RAG 继续使用统一 harness runtime，不增加评测专用运行栈。
- OpenSpec 依赖：`add-agent-context-usage-attribution` 提供实际 Provider usage attribution；本变更只定义累计 token budget 接入点，在该数据可用前不得用估算值冒充实际 run cost。
- 兼容性：不新增或删除 `/api/chat` 端点；已有 SSE 事件和字段继续有效，新增 metadata 对旧客户端可忽略。
