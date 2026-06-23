## Context

- `GeneralQAAgent` 当前直接在 `create_agent(...)` 处内联 middleware，只包含 `SummarizationMiddleware` 与 `ContextEditingMiddleware`；其它 Agent 的 runtime guard 也分散在各自实现中。
- `BaseAgent._stream_agent_response()` 只负责把 `astream_events` 转成上游事件并处理 cancel/error；对历史消息完整性、tool 循环、摘要模型分流没有统一治理。
- `QaService.exec_query()` 已经具备 streaming skeleton、partial/error/completed 落库与 CancelledError 兜底，但“中断后消息结构可能悬空”的问题仍然存在。
- 现有 SSE 契约与前端消息渲染已经较稳定，本次设计优先复用现有协议，不引入新的 stream 类型。

## Goals / Non-Goals

**Goals**

- 为 Noesis 建立统一 agent runtime 装配层，集中接入 summarization offload、loop detection、dangling tool repair。
- 保持 `POST /api/chat/sessions/stream`、`POST /api/chat/sessions/{session_id}/test-case/resume` 等对外协议兼容。
- 将循环收敛与中断修复前移到模型调用前后，而不是仅靠 `recursion_limit` 尾端报错。

**Non-Goals**

- 不实现完整的 RunManager / StreamBridge / replay。
- 不重构现有 `LangGraphSseBridge` 事件类型。
- 不在本 change 内推广到所有 Agent 的定制 prompt/图状态重构。

## Decisions

### 1. 新增统一 Agent Runtime Factory

新增 `backend/agent/factory.py`（或等价命名）作为唯一公共装配入口，负责生成 `create_agent(...)` 所需参数：

- `model`
- `tools`
- `system_prompt`
- `checkpointer`
- `middleware`

最低要求：

- `GeneralQAAgent` 必须迁移到该 factory；
- `FaultOperationAgent`、`DeepResearchAgent`、测试用例 Agent 可分步迁移，但 factory 必须支持这些场景接入；
- middleware 顺序由 factory 统一定义，而不是每个 agent 自己拼。

建议顺序：

1. dangling tool repair
2. tool error handling / 现有 context editing
3. summarization offload
4. memory/其它后续中间件
5. loop detection

原因：

- dangling repair 要在进入模型前最早修正历史消息；
- summarization 发生在正常上下文裁剪阶段；
- loop detection 靠近模型决策末端，更适合基于最新消息与 tool 轨迹判断是否收敛。

### 2. Summarization offload（`before_model`）

`SummarizationOffloadMiddleware` 在 `before_model` 中按 token 占比触发（默认 `summarization_trigger_fraction=0.85`，基准为 `summarization_max_input_tokens` / `MAX_TOKENS`）：

1. 先卸载超大 `ToolMessage`：有 filesystem backend 时写入 `/summary_offload/*.txt` 并替换为占位符；否则丢弃为短占位文本。
2. 若卸载后仍高于 `trigger × summarization_max_retention_ratio`（默认 0.6），再调用独立摘要模型做 LLM 摘要。

摘要模型：`get_llm(purpose="summarization")`，未配置时回退主模型。

配置项：`SUMMARIZATION_ENABLED`、`SUMMARIZATION_TRIGGER_FRACTION`、`SUMMARIZATION_MAX_INPUT_TOKENS`、`SUMMARIZATION_TOOL_OFFLOAD_THRESHOLD`、`SUMMARIZATION_MAX_RETENTION_RATIO`、`SUMMARIZATION_MESSAGES_TO_KEEP`。

### 3. Loop detection 采用“双层检测 + 双阈值处置”

新增 `LoopDetectionMiddleware`，至少记录以下状态：

- 最近 N 次 tool 调用的标准化签名；
- 最近 N 次 tool name 序列及计数；
- 已经发出过 warning 的轮次。

检测规则：

- **重复工具集合检测**：同一工具名 + 近似入参签名在窗口内重复达到 warning/hard-stop 阈值。
- **同类工具空转检测**：例如 `read_file`、`search_*`、`bash` 等同类工具在短窗口内高频出现但没有带来新的 assistant text 或明确结果收敛。

处置规则：

- 达到 warning 阈值：向模型追加一条 system/tool-style 提示，要求总结已有发现、避免继续重复工具。
- 达到 hard-stop 阈值：阻断下一轮继续调用工具，返回一条面向用户的收敛说明，并让流式路径以正常 `finish` 或明确 error/abort 收尾。

注意：

- 这不是替代 `recursion_limit`，而是前置收敛；
- middleware 输出的停止原因必须可被 `_format_agent_stream_error()` 或等价错误格式化逻辑稳定呈现。

### 4. Dangling tool repair 采用“模型调用前补丁消息历史”

新增 `DanglingToolCallMiddleware`，在每次模型调用前扫描 `messages`：

- 收集所有 `AIMessage` 中的 `tool_calls`；
- 收集所有 `ToolMessage.tool_call_id`；
- 找出缺失对应结果的 tool call；
- 在对应 `AIMessage` 之后插入 synthetic `ToolMessage`。

synthetic `ToolMessage` 约定：

- `status="error"`
- `tool_call_id` 为悬空 id
- `name` 取 tool call name，缺失时用 `unknown`
- `content` 为稳定、可追踪的错误文案，例如“Tool call was interrupted before a result was recorded.”

为什么使用“补丁消息”而不是直接删除悬空 `tool_calls`：

- 删除会破坏真实历史；
- 补一条 error tool message 更符合 LangChain/LangGraph 的结构预期，也便于前端历史解释。

### 5. SSE / 落库契约保持不变，仅增强一致性

本 change 不新增 SSE 事件类型，也不要求前端新增特殊解析。

与 `QaService` 的协同要求：

- `CancelledError` 路径仍保存 `partial` assistant；
- 下一轮继续对话时，由 dangling repair 负责修复历史消息结构；
- 如果 synthetic `ToolMessage` 不需要单独透传到前端，则只需保证它参与下一轮模型上下文；
- 若后续选择落库该 synthetic 信息，应通过既有 multipart `tool` part 兼容写入，而不是另起消息类型。

### 6. 先覆盖 COMMON_QA，再向其它 Agent 推广

首批强制范围：

- `GeneralQAAgent`
- 统一 factory
- middleware 单测与至少一个流式取消集成测试

二次接入目标：

- `FaultOperationAgent`
- `DeepResearchAgent`
- 测试用例 Agent 的通用部分

原因：

- COMMON_QA 路径最简单，便于验证 runtime guard；
- 故障运维与深度调研工具链更复杂，接入后更能体现 loop detection 价值，但不应阻塞第一批 proposal。

## Data Flow

### A. 正常流式对话

1. `QaService.exec_query()` 选择 agent。
2. agent 通过 runtime factory 构建模型与 middleware。
3. 模型调用前，`DanglingToolCallMiddleware` 检查并修补历史消息。
4. 达到上下文阈值时，summarization middleware 调用独立摘要模型压缩历史。
5. 工具链执行过程中，loop detection 持续记录最近工具轨迹。
6. 若未触发 hard-stop，事件继续进入 `LangGraphSseBridge` 并按现有 SSE 契约输出。

### B. 页面刷新 / 中断后继续提问

1. 上一轮 SSE 在 tool call 发出后中断。
2. `QaService` 以 `partial` 状态保存 assistant multipart。
3. 用户再次在同一会话提问。
4. runtime factory 装配的 `DanglingToolCallMiddleware` 扫描历史并补 synthetic `ToolMessage`。
5. 新一轮模型可继续执行，不因缺少 tool result 直接失败。

### C. 工具空转

1. Agent 连续多轮调用相同或同类工具。
2. `LoopDetectionMiddleware` 达到 warning 阈值，插入“请总结已知结果”的提示。
3. 若仍继续重复，达到 hard-stop 阈值。
4. middleware 阻断继续工具调用，返回可展示的收敛说明。
5. SSE 正常结束，避免仅在 recursion_limit 处报通用错误。

## Risks / Trade-offs

| 风险 | 说明 | 缓解 |
|------|------|------|
| 摘要模型与主模型行为差异 | 过于便宜的模型可能摘要质量差，影响后续回答 | 保留回退主模型配置；先在 COMMON_QA 启用 |
| Loop detection 误杀 | 合法的多步诊断可能被识别为循环 | 采用 warning/hard-stop 两级；阈值可配置；先针对 COMMON_QA/FaultOperation 调优 |
| synthetic ToolMessage 影响历史展示 | 若后续选择落库，前端可能出现“莫名错误工具项” | 首版先确保用于模型上下文即可；是否展示单独评估 |
| middleware 顺序耦合 | 顺序不当会导致 summarization/repair/loop 互相干扰 | 将顺序固化在 factory，并为顺序编写单测 |

## Migration Plan

1. 新增 runtime factory 与 middleware 骨架。
2. 抽离 `GeneralQAAgent` 到 factory，接入 summarization offload、loop detection、dangling repair。
3. 为 `QaService` 的取消后恢复场景补集成测试。
4. 将 `FaultOperationAgent` 与 `DeepResearchAgent` 接到统一 factory，按 flag 控制启用。
5. 更新设计文档与测试文档。

## Open Questions

- `get_llm()` 当前是否已经具备按用途选模型的能力；若没有，需要在 `llm.py` 增加最小扩展接口。
- loop detection 的 hard-stop 更适合返回 `finish_reason=stop` 还是显式 error，需要结合前端当前 stop/error 展示语义确认。
- synthetic ToolMessage 是否需要同步写入 `content.parts` 历史消息；本次 proposal 先不强制要求展示层变更。
