## Why

Noesis 当前已经在 `backend/agent/common_react_agent.py` 为通用问答接入了基础 `SummarizationMiddleware` 与 `ContextEditingMiddleware`，但整体 Agent 运行时仍缺少三类关键防护：

- **长上下文摘要成本治理不足**：现有 summarization 直接与主模型绑定，无法像 YuXi / DeerFlow 一样将摘要工作卸载到更便宜、更稳定的 summarizer 路径，长会话下成本与延迟都不可控。
- **工具循环缺少主动制动**：当前仅依赖 `recursion_limit=50` 做最终兜底，无法在“同类工具反复空转”或“相同工具集重复执行”时提前告警并强制收敛。
- **Dangling tool call 无自愈**：当 SSE 中断、页面刷新或任务取消发生在 `AIMessage.tool_calls` 已生成但 `ToolMessage` 尚未补齐的窗口，下一轮调用可能因消息历史不完整而失败。

仓库内已有对比与事故分析文档：

- `docs/readings/compare/noesis-vs-yuxi-backend.md`
- `docs/readings/compare/deerflow_vs_noesis_backend_analysis.md`
- `docs/readings/sse-dangling-tool-call-analysis.md`

这些文档已经明确指出：对于 Noesis 这类以 SSE 会话为核心的产品，优先补齐 **runtime guard** 比直接引入完整 run 平台更划算。因此需要新增一项变更，把 YuXi / DeerFlow 中已经验证过的三类防护以 Noesis 现有架构可承受的方式引入。

## What Changes

本 change 引入一组面向 Noesis Agent 运行时的防护能力，范围控制在 `backend/agent`、`backend/services/qa_service.py`、SSE/消息持久化契约与相应测试，不扩展到独立 worker、Redis replay 或持久化 checkpointer。

### 1. Summarization offload

- 新增 Noesis 自有的 agent middleware/factory 装配层。
- 将“对话摘要”与“主推理模型”解耦，允许为摘要步骤单独选择低成本模型或独立 `get_llm(...purpose="summarization")` 路径。
- 首批至少在 `GeneralQAAgent` 接入，其他 Agent 通过统一工厂逐步复用，而不是各自散落配置。

### 2. Loop detection

- 新增循环检测 middleware。
- 同时覆盖：
  - 相同或等价工具调用集合重复出现；
  - 某类工具在短窗口内高频空转。
- 达到阈值时分两级处理：
  - 先向模型注入“请收敛/总结当前结果”的警告；
  - 超过硬阈值后中止继续工具循环，并返回明确的收敛说明。

### 3. Dangling tool call repair

- 在进入下一轮模型调用前扫描消息历史。
- 若发现 `AIMessage.tool_calls` 缺少对应 `ToolMessage`，自动插入 synthetic error `ToolMessage` 补齐结构。
- 与现有取消/partial 落库逻辑配合，避免因中断造成后续会话不可继续。

### 4. 统一装配与测试

- 新增 Noesis agent factory 或等价运行时装配入口，集中决定：
  - model
  - checkpointer
  - summarization offload
  - loop detection
  - dangling tool repair
  - 现有 `ContextEditingMiddleware`
- 为 SSE 中断、历史恢复、循环触发与摘要分流补充后端自动化测试与文档。

## Non-Goals

- 不引入 Redis/Worker/RunManager 异步运行时。
- 不将 `InMemorySaver` 在本 change 内替换为 Postgres/SQLite checkpointer。
- 不新增前端 SSE 事件类型。
- 不重写四类 Agent 的业务图逻辑，只抽取公共 runtime guard。

## Capabilities

### Modified Capabilities

- `platform-chat`

## Impact

- **后端运行时**：`backend/agent/common_react_agent.py`、`backend/agent/base/base_agent.py`、新增 `backend/agent/middlewares/*` 与 `backend/agent/factory.py`（或等价路径）。
- **配置层**：`backend/llm.py` / `config/env.py` 可能新增摘要模型选择项。
- **SSE 与持久化一致性**：`backend/services/qa_service.py` 的取消/恢复链路需要验证与 dangling repair 的协同。
- **测试与文档**：补充 middleware 单测、流式取消回归测试，以及 PRD / TDD 文档。
- **兼容性**：对外 API 与 SSE 事件类型保持兼容；新增行为主要体现在更稳的历史修复与更早的循环收敛，属于非 breaking 变更。
