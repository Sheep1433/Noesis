## Context

当前链路：

```
Tool 实现 / Sandbox / MCP
  → ToolErrorHandlingMiddleware（仅捕获异常 → status=error）
  → on_tool_end（status 来自 ToolMessage）
  → LangGraphSseBridge._resolve_tool_failure（仅处理 error）
  → tool-output-available（success 时 outcome 未定义）
  → ToolCallCollapse（result 为空则不渲染）
```

痛点案例：`AioSandboxBackend.execute` 在异常时返回 `ExecuteResponse(output=..., exit_code=1)` 而不抛错；超时时可能表现为空 output；前端 `v-if="resultDisplay"` 导致用户看不到任何反馈。

约束：不新增 SSE 事件类型；`failureCategory` 十类枚举保持向后兼容；旧客户端忽略未知 JSON 键。

## Goals / Non-Goals

**Goals:**

- 建立 **invoke（status）** 与 **execution（outcome）** 双层模型，写入规格与代码。
- 新增 `tool_outcome.py` 统一解析 execute JSON、MCP bash 形态、纯文本工具。
- 修复沙箱超时/基础设施错误的抛错路径，杜绝「空 success」。
- SSE / 落库 / `ToolCallCollapse` 贯通 `outcome`、`exitCode`、`timedOut`。
- 保留既有 `ToolFailureCategory`、middleware、`failure_notice` 分流逻辑。

**Non-Goals:**

- 不将 `command_failed` 自动升级为 `status=error`（模型需看到 stderr 自行重试）。
- 不做 bash 命令语义解释（如解析 `grep` 未找到 vs 权限错误）。
- 不新增 per-tool 自定义 UI 组件。
- 不修改 `recursion_limit`、自动重试策略。

## Decisions

### 1. 双层字段而非单一 expanded status

**决策**：保留 LangGraph 原生 `status`（success/error），新增独立 `outcome` 枚举。

**理由**：`status=error` 会改变 Agent 对 `ToolMessage` 的处理语义；命令非零退出在业界惯例中仍返回 success（见 MCP bash `timed_out: false, exit_code: N`）。合并为单一状态机会迫使所有「软失败」走 error 路径，破坏 Agent 续推逻辑。

**备选（否决）**：扩展 `status` 为 `success|error|warning` — 前端与 LangGraph 契约改动面过大。

### 2. 模块划分

| 模块 | 职责 |
|------|------|
| `tool_errors.py` | `ToolFailureCategory`、异常层次（不变） |
| `tool_failure.py` | 调用失败分类、`failure_to_sse_error_fields`（不变） |
| `tool_outcome.py`（新） | `ToolOutcome` dataclass、`parse_tool_outcome`、`outcome_to_sse_fields` |
| `langgraph_sse.py` | success 路径调用 `parse_tool_outcome`；error 路径不变 |
| `aio_sandbox.py` | 超时/基础设施 → 抛 `ToolFailureError`；成功 → 结构化字段 |

### 3. 结构化输出解析规则

`parse_tool_outcome(tool_name, raw: str) -> ToolOutcome`：

1. 尝试 `json.loads`；识别键 `exit_code`/`exitCode`、`timed_out`/`timedOut`、`stdout`/`output`、`stderr`。
2. 若 `timed_out is True` → `outcome=timed_out`（优先于 exit_code）。
3. 否则若 `exit_code` 存在且 `!= 0` → `outcome=command_failed`。
4. 否则合并 stdout+stderr+output 文本；全空白 → `outcome=empty`；否则 `ok`。
5. 非 JSON：纯文本空白 → `empty`；有内容 → `ok`。

进程类工具名列表：`execute`、`bash`（可配置常量 `_PROCESS_TOOL_NAMES`）。

### 4. SSE / 落库字段命名

与现有 `durationMs` / `errorCategory` 风格对齐，桥接层输出 **camelCase**：

```json
{
  "type": "tool-output-available",
  "status": "success",
  "outcome": "command_failed",
  "exitCode": 127,
  "timedOut": false,
  "truncated": false,
  "output": "..."
}
```

`AssistantMessageBuilder.append_tool_output` 增加可选参数 `outcome`, `exit_code`, `timed_out`, `truncated`，写入 tool part。

### 5. 前端展示

`ToolCallCollapse`：

| 条件 | header 标签 | 输出区 |
|------|------------|--------|
| `status=error` | 错误 | error 区（现有） |
| `outcome=empty` | 完成 | 「（无输出）」灰色占位 |
| `outcome=command_failed` | 命令失败 | 输出 + `退出码: N` |
| `outcome=timed_out` | 执行超时 | 已有输出 + 超时提示 |
| `outcome=ok` 或缺省 | 完成 | 正常输出 |

`useSSEStream` / `applyToolOutput` 透传新字段。

### 6. AioSandboxBackend 行为变更

| 场景 | 现况 | 目标 |
|------|------|------|
| `exec_command` 超时 | 可能异常被 catch 为 ExecuteResponse | `raise ToolTimeoutError(...)` |
| 沙箱不可达 | `ExecuteResponse(output="AIO sandbox execute failed: ...")` | `raise ToolInfrastructureError(...)` |
| 正常结束 | `ExecuteResponse` | 保持；由上层 execute 工具序列化为 JSON |

## Risks / Trade-offs

- **[Risk] deepagents execute 工具返回格式与 JSON 解析不一致** → 在 `tool_outcome` 单测覆盖真实样本；必要时在 middleware 后处理层适配。
- **[Risk] 历史消息无 outcome 字段** → 前端缺省时按「有 output 则 ok，无 output 则不展示」兼容，不崩溃。
- **[Risk] command_failed 用户看到 stderr** → 进程类 stderr 属执行结果，非调用失败；规格明确与 `error` 字段分离。
- **[Trade-off] empty 与 timed_out 在旧沙箱数据上仍难区分** → 本 change 以修复抛错路径为主；历史数据无法 retroactive。

## Migration Plan

1. 落地 `tool_outcome.py` + 单测。
2. 改 `aio_sandbox.py` 抛错路径。
3. 改 `langgraph_sse.py` / `message_builder.py` 写入新字段。
4. 改前端解析与 `ToolCallCollapse`。
5. `uv run pytest` + 前端 lint。

回滚：移除 outcome 字段写入，恢复 aio_sandbox catch-all；前端忽略未知字段即可兼容。

## Open Questions

- deepagents 内置 `execute` 是否已输出 JSON；若仍为纯文本，是否需在 Noesis 包装层统一序列化（实现阶段确认 `agent/backends` 与 deepagents 工具注册点）。
