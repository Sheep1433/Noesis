## Why

评测与通道需要直接调用 Agent 内核，但内核长期嵌在平台 `services`/`domain` 反向依赖里，导致适配层膨胀、边界不清。Delivery Fan-out 已独立；本期将 Agent 内核物理拆成 `noesis-harness` 包，禁止 harness→services 反向依赖。

## What Changes

- **BREAKING（import 路径）**：Agent 内核迁至 workspace distribution `backend/packages/harness`（发行名 `noesis-harness`），其唯一 Python 顶层包为 `noesis`；旧 `agent.*` / `harness.*` 命名空间直接移除，不保留并行 shim。
- LLM 工厂与模型目录归入 `backend/packages/harness/noesis/llm`，统一从 `noesis.llm.*` 导入；不创建与 harness 平级的第二个发行包。
- Agent/LLM 所需运行时配置、路径、MCP 配置与 checkpointer 归入 `noesis.config`，logging 归入 `noesis.runtime.logging`；平台配置层单向复用，不再由 noesis import backend `config/common`。
- 所有具体 Agent 归入 `noesis.agents`，测试用例 Agent 位于 `noesis.agents.case_generate`；不再并列维护 `profiles` / `case_generate`。
- stream、HITL、依赖端口与附件输入适配归入 `noesis.runtime`；attachments 不再作为顶层子系统。
- 抽出 `noesis.runtime.stream.stream_agent_events`；Harbor 经同一 stream 核。
- 切断 `noesis` 对 `services.*` 的静态依赖：沙箱 lifecycle / skills revision / tool_failure / HITL 下沉；附件与 KB 经 `noesis.runtime.deps` 由平台绑定。
- 将平台宿主模块统一收敛到 `backend/noesis_server`，根目录只保留启动、评测、独立 package、迁移与测试；平台 import 统一使用 `noesis_server.*`，不保留旧顶层 package shim。
- 修正平台内部反向依赖：Telegram runtime 与 KB 启动编排归应用/bootstrap，KB 检索不再反向 import application service，删除 `services.qa_service` 兼容入口。
- **不做**：把 Delivery / RunOrchestrator 迁入 harness；不新建第二套 AgentRunService。

## Capabilities

### New Capabilities

- `agent-harness`：独立 Agent 内核包边界、依赖方向、评测入口约定。

### Modified Capabilities

- `offline-evals`：评测 SHALL 经 harness（factory + stream）；Harbor 不得自维护并行 runtime 栈。
- `agent-tool-failure-handling`：失败类型权威源迁至 `noesis.errors`，旧 domain 路径直接移除。

## Impact

- 代码：`backend/packages/harness/noesis/**`、`backend/noesis_server/**`、`app.py`、evals Harbor worker，以及平台 import → `noesis.*` / `noesis_server.*`。
- API/SSE：无路径变更；事件形状不变。
- 依赖：backend `pyproject.toml` 仅 path 依赖 `noesis-harness`；wheel SHALL 在 backend 源码目录外安装并导入 factory/LLM，不承诺发布 PyPI。
