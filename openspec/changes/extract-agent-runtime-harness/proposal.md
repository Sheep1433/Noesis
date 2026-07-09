## Why

Noesis 当前将 Agent **运行时**（`create_noesis_agent`、middleware、backends、`astream_events` 消费）与 **产品 Harness**（`qa_service` 路由、`LangGraphSseBridge`、消息落库、Harbor 评测 worker）耦合在同一 `backend/agent/` + `services/` 调用链中。离线评测（`evals/agent/_agent.py`、`harbor/noesis_worker.py`）各自拼装 Agent，与线上路径分叉，不利于长期演进与 benchmark 可信度。参考 [deer-flow harness/app 拆分](https://github.com/bytedance/deer-flow)、[Yuxi BaseContext + agent_run_service](https://github.com/xerrors/Yuxi)、deepagents 作为**纯运行时库**的定位，需要将 Agent 运行时独立为可复用层，Harness 仅负责产品编排与传输。

## What Changes

- 新增 **`noesis_runtime`** 模块（首版仍在 `backend/` 内，逻辑边界先行、物理拆包后续）：集中 Agent 工厂、运行时 middleware、backends、流式执行内核（`run_agent` / `AgentRunExecutor`）。
- 新增 **`AgentRuntimeContext`**（对标 Yuxi `BaseContext`）：将 `qa_type`、model、KB collections、sandbox、skills、附件等运行时输入收敛为单一 dataclass，由 Harness 层解析后注入 Runtime。
- 新增 **`AgentRunService`**（对标 Yuxi `agent_run_service` + deer-flow `runtime/runs/worker`）：统一「创建 run → 后台执行 graph → 发布事件」；`QaService` 与 `evals.agent` **SHALL** 经此入口调用，不再各自 `SuperAgent().run_agent()`。
- 提供 **`NoesisRuntimeClient`** 嵌入式入口（对标 deer-flow `DeerFlowClient`）：脚本 / Harbor / 单测可跳过 FastAPI，仍走同一 runtime 路径。
- 将 per-qa Agent 类（`GeneralQAAgent`、`SuperAgent` 等）降级为 **Profile 装配器**（prompt + tools + extra_middleware），删除重复的 `run_agent` 样板。
- Harbor worker（`noesis_worker.py`）与 BrowseComp runner **SHALL** 复用 `AgentRunService` + `AgentRuntimeContext`，**SHALL NOT** 直接 `create_noesis_agent` + `astream_events`。
- **非目标（本 change）**：不发布独立 PyPI wheel、不引入 ARQ/Redis worker 队列、不改变对外 SSE 事件契约与 `/api/chat/sessions/stream` 路径。

## Capabilities

### New Capabilities

- `agent-runtime-harness`：规定 Noesis Agent **运行时**与 **Harness 平台层**的职责边界、`AgentRuntimeContext`、`AgentRunService`、嵌入式 Client 及依赖方向（Runtime 不得 import `services`/`api`）。

### Modified Capabilities

- `agent-offline-eval`：评测 runner **SHALL** 经 `AgentRunService` 执行，与线上共用 runtime；Harbor worker 禁止平行拼装路径。
- `platform-chat`：`QaService` **SHALL** 委托 `AgentRunService` 启动/取消 run；SSE 桥接层仅消费 runtime 事件，不直接持有 Agent 实例生命周期。

## Impact

| 区域 | 影响 |
|------|------|
| `backend/agent/` | 运行时核心迁至 `noesis_runtime/`；现有 `factory.py`、`middlewares/`、`backends/` 为迁移源 |
| `backend/services/qa_service.py` | 变薄：会话/落库 + 调用 `AgentRunService` |
| `backend/domain/chat/streaming/` | 事件来源改为 runtime 发布；`LangGraphSseBridge` 接口保持 |
| `backend/evals/agent/` | `_agent.py`、`harbor/noesis_worker.py` 改接 runtime client |
| `backend/tests/` | 新增 runtime 单测；更新 cancel/stream 相关用例 |
| 前端 | **无破坏性变更**（SSE 契约不变） |
| 依赖 | 继续以 PyPI `deepagents` 为底层运行时库，不 fork |
