# Design: 统一 Agent 工厂与文件系统集成

## 架构

```
create_noesis_agent
  ├─ FilesystemMiddleware (optional, deepagents)
  ├─ SubAgentMiddleware / AsyncSubAgentMiddleware (optional)
  ├─ extra_middleware（Skills 等由调用方按需挂载）
  └─ build_noesis_runtime_middleware()
        ├─ DanglingToolCallMiddleware
        ├─ SummarizationOffloadMiddleware（StateBackend 来自 deepagents）
        ├─ ContextEditingMiddleware
        ├─ LoopDetectionMiddleware
        └─ ToolCallLimitMiddleware (optional)
```

## 文件系统

- **不 vendoring**：直接 `pip install deepagents`
- 工具面用 `FilesystemMiddleware`，存储面用 `BackendProtocol` 实现（Noesis 深度研究为本地 `LocalShellBackend` + `CompositeBackend`）

## Agent 映射

| Agent | backend | extra_middleware | tools |
|-------|---------|------------------|-------|
| GeneralQA | — | — | — |
| DeepResearch | CompositeBackend | `SkillsMiddleware(sources=["/skills/"])` | — |
| FaultOperation | — | — | MCP |
| CaseCoordinator | N/A (StateGraph) | N/A | N/A |

## 配置

- 故障运维 MCP 端点写死在 `fault_operation_agent.FAULT_MCP_URL`
