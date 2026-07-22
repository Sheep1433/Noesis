## Context

SuperAgent（`SUPER_AGENT_QA`）经 `create_noesis_agent` 装配 `FilesystemMiddleware`（含 `execute`）、Web 工具、`task-worker` 子 Agent。沙箱为 per-session Docker（或 `local_shell`），workspace 可写，Skills 只读，`/memory/AGENTS.md` 与 `/memory/USER.md` 跨会话持久。

deepagents `create_deep_agent(interrupt_on=...)` 会在栈尾挂载 LangChain `HumanInTheLoopMiddleware`：在 `after_model` 阶段对匹配的 `tool_calls` 调用 `interrupt(HITLRequest)`，resume 时接收 `HITLResponse.decisions`（`approve` / `reject` / `edit` / `respond`）。Noesis 使用 `create_agent` 自建中间件栈，**未**挂载该中间件。

测试用例 Agent 已有 LangGraph `interrupt_before` + `test-case/resume` 先例，但属于工作流节点级暂停，与工具级 HITL 不同。本设计复用同一 **checkpoint + Command(resume)** 基础设施，统一 resume API 形状。

相关模块：`agent/factory.py`、`agent/super_agent.py`、`domain/chat/streaming/langgraph_sse.py`、`services/qa_service.py`、`frontend/src/views/chat/useSSEStream.ts`。

## Goals / Non-Goals

**Goals:**

- SuperAgent 沙箱内对**有后果**的操作（持久记忆写入、网络类 `execute`）经用户确认后执行。
- 任务中途通过 `ask_user` + HITL `respond` 阻塞等待用户补充信息，图状态可 checkpoint 恢复。
- 单一 `HumanInTheLoopMiddleware` 实例，按工具名分流审批 vs 澄清 UI。
- SSE + resume API 与现有流式落库兼容：interrupt 段以 `hitl_pending` 收尾，resume 新开 SSE（对齐 test-case）。

**Non-Goals:**

- 首期覆盖 `FAULT_OPERATION_QA` 远程 MCP `bash`（后续可复用 `agent-hitl` 能力）。
- `FilesystemPermission` 对 `execute` 的路径级静态 ACL（deepagents 尚未实现）。
- 审批/澄清历史独立审计表（首期仅落在 assistant `content.parts` 可观测字段）。
- 任务开始前的寒暄/意图不明：继续 **prompt 纯文本**澄清，不注册 HITL。

## Decisions

### D1：复用 LangChain `HumanInTheLoopMiddleware`，不自研 wrap_tool_call 闸门

- **选择**：在 `create_noesis_agent` 增加可选 `interrupt_on`，条件挂载 `HumanInTheLoopMiddleware(interrupt_on=...)`，置于 `ToolErrorHandlingMiddleware` **之前**（deepagents 放在 memory 之后；Noesis 放在 runtime guards 之前，确保审批先于执行错误包装）。
- **理由**：与 deepagents 0.6.x 对齐；`respond` 已覆盖 ask-user 语义；减少维护成本。
- **备选**：自研 `ToolApprovalMiddleware` 仅 `wrap_tool_call`——需重复实现 batch interrupt、edit、reject 文案，弃用。

### D2：审批与澄清分工具、分决策类型

| 工具 | `allowed_decisions` | 用途 |
|------|---------------------|------|
| `execute` | `approve`, `reject` | 危险 shell 命令预览后执行 |
| `write_file`, `edit_file` | `approve`, `reject` | 仅当目标为 `/memory/**` |
| `ask_user` | `respond` | 用户回答问题，不执行任何副作用 |

- `execute` / memory 写入 **SHALL NOT** 暴露 `respond`，避免用「回答问题」绕过审批。
- `ask_user` **SHALL NOT** 暴露 `approve`（无真实 handler 副作用）。

### D3：`ask_user` 工具契约

```python
class AskUserInput(BaseModel):
    question: str = Field(description="向用户提出的澄清问题")
    options: list[str] | None = Field(default=None, description="可选答案；为空则自由文本")
```

- 工具实现体可为 no-op（或返回占位字符串）；真实用户输入仅经 HITL `respond.message` 进入 `ToolMessage`。
- system prompt 补充：任务已启动且缺少关键参数时 **MAY** 调用 `ask_user`；任务入口模糊时仍 **SHALL** 纯文本追问且不调用工具（保留现有 `<interaction>` 块）。

### D4：SuperAgent 沙箱审批 `when` 谓词（少问）

**自动放行（不 interrupt）：**

- `read_file` / `ls` / `glob` / `grep`
- `write_file` / `edit_file` 目标路径 **不以** `/memory/` 开头
- `execute` 常规开发命令与沙箱内破坏性命令（含 `rm -rf`、`find -delete` 等，仅伤当前 session）
- `web_search` / `web_fetch`（fetch 已有 SSRF 防护）
- `task` / `write_todos`

**条件 interrupt（`execute`）：**

- 网络出口：`curl` / `wget` / `ssh` / `scp` / `nc` / `git push` / `pip install` / `npm install`（容器有 egress 时）
- pipe-to-shell：`| sh`、`| bash`、`curl ... |`

**条件 interrupt（文件工具）：**

- `write_file` / `edit_file` 且规范化路径以 `/memory/` 开头

**Session grant（可选，memory 除外）：**

- 用户对网络类 `execute` 可选「本会话允许同类」；`/memory/` 写入 **每次** 须确认。

谓词集中在 `agent/hitl/policy.py`，单元测试覆盖正则边界。

### D5：SSE 与 resume API（对齐 test-case/resume，新开 SSE）

> **纠正**：初稿「挂住原 HTTP 流 + heartbeat 等待 resume」与现有 `test-case/resume`、assistant 终态落库互斥冲突（流正常结束会 `_finalize` 为 completed）。首期改为与测试用例相同的 **分段 SSE**：interrupt 段结束后发 `[DONE]`；resume 再开新的 `text/event-stream`。

**事件 `hitl-required`**（在 `tool-input-available` 之后、实际执行之前发出；图已 interrupt）：

```json
{
  "type": "hitl-required",
  "interrupt_id": "uuid",
  "session_id": "...",
  "message_id": "...",
  "kind": "approval | clarification",
  "action_requests": [
    {
      "tool_call_id": "...",
      "name": "execute",
      "args": { "command": "curl https://..." },
      "description": "..."
    }
  ],
  "review_configs": [
    { "action_name": "execute", "allowed_decisions": ["approve", "reject"] }
  ],
  "expires_at": 1730000000
}
```

- `kind=clarification` 当且仅当 `action_requests` 全部为 `ask_user`；若同批混有审批类工具，`kind=approval`（UI 按各 `action_requests`/`review_configs` 分别渲染）。
- 桥接：在 `astream` / `astream_events` 中识别 `__interrupt__`（或 `GraphInterrupt`），转为 `hitl-required`。
- 发出 `hitl-required` 后，本段 HTTP 流 **SHALL** 再发 `finish`（`finish_reason=hitl_pending`）与 `data: [DONE]`，结束该次 StreamingResponse。
- 此时 assistant 行 **SHALL NOT** 走 `completed` 终态；保持 `status=streaming`（parts 含 `hitl.status=pending`），供 resume 同一 `message_id` 续写。
- **SHALL NOT** 在无连接期间依赖 heartbeat；等待态由前端卡片 + 服务端超时任务负责。

**Resume：**

```
POST /api/chat/sessions/{session_id}/hitl/resume
```

- 成功时响应 **SHALL** 为新的 `text/event-stream`（与 `POST .../test-case/resume` 同形），**不是** JSON body。
- Body（对齐 LangChain `HITLResponse`）：

```json
{
  "interrupt_id": "...",
  "decisions": [
    { "type": "approve" }
  ],
  "grant_scope": "once | session | null"
}
```

- 须 CSRF（与 stop 同级）；校验会话归属与 `interrupt_id` 匹配 pending 状态。
- Service：`Command(resume={"decisions": ...})` 继续同一 `thread_id`；新 SSE 经现有桥接转发；同一 `assistant_message_id` 续写。
- 一轮内可多次 interrupt → 每段流均以 `hitl_pending` 或最终 `stop`/`error` 收尾。
- `grant_scope=session` 仅对网络类 `execute` 生效，写入进程内 session grant 集（不落 DB）。

**超时**：配置 `hitl.ask_timeout_seconds`（默认 86400，即 24h）。无 resume 时后台任务按 `reject` 恢复图并终态落库（`partial`/`error`，遵循终态互斥）。无活跃 SSE 时仅更新 DB；前端刷新或下次交互可见。

### D6：`create_noesis_agent` 与 subagent 继承

```python
def create_noesis_agent(..., interrupt_on: dict | None = None):
    ...
    if interrupt_on:
        middleware.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))
```

- `SuperAgent` 构建 `interrupt_on` 并传入主 Agent。
- `task-worker` 的 `SubAgent` spec **SHALL** 继承相同 `interrupt_on`（与 deepagents 默认一致）。
- `hitl.enabled=false` 时不传 `interrupt_on`，不注册 `ask_user` 工具。

### D7：前端 UI

| kind | 组件 | 操作 |
|------|------|------|
| `approval` | `HitlApprovalCard` | 允许一次 / 本会话允许（若允许）/ 拒绝；展示 command 或 path |
| `clarification` | `HitlClarificationCard` | 文本框或 options **单选**；提交映射为 `{type:"respond", message:"..."}` |

- 嵌入 assistant 时间线，位于对应 `tool_call_id` 的 tool part 上，状态 `pending_hitl`。
- resume 成功后卡片只读展示用户决策摘要。

### D8：落库与历史

- HITL 等待期间 assistant 行已存在（`status=streaming`）；`hitl-required` 对应 part **MAY** 写入 `content.parts` 扩展字段 `hitl: { kind, status: "pending", ... }`。
- resume 后更新 part 为 `approved` / `rejected` / `answered`；**SHALL NOT** 因 HITL 产生第二行 assistant。
- 拒绝的工具 **SHALL** 在 SSE 上仍出现 `tool-output-available`（`status=error`），与 `agent-tool-failure-handling` 一致。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| 分段 SSE 后前端需记住 pending 卡片 | `hitl-required` 写入 parts；`finish_reason=hitl_pending` 时 UI 保持卡片可操作，不按普通完成收尾 |
| 多 tool_call 并行 interrupt 仅一批 | 首期按 LangChain batch 行为一次展示多张卡片；UI 按 `action_requests` 长度渲染 |
| 用户刷新页面 | checkpoint 仍在；首期刷新后卡片只读/不可 resume（P1 再做恢复入口） |
| `when` 谓词误报/漏报 | 策略单测 + 可配置开关；默认偏「少问」 |
| subagent 绕过主 Agent 策略 | task-worker 显式继承 `interrupt_on` |

## Migration Plan

1. 后端：`hitl.enabled=false` 默认关闭；先合工厂 + 策略单测。
2. 打开配置后仅 `SUPER_AGENT_QA` 注册 `ask_user` 与 `interrupt_on`。
3. 前端识别 `hitl-required`；旧客户端忽略事件，依赖超时 reject。
4. 回滚：`hitl.enabled=false` 即可，无需 DB migration。

## Resolved Decisions（原 Open Questions）

1. **刷新后恢复 pending HITL**：首期 **不做**（P1）。流内必做；刷新后以 DB parts 中 `hitl.status=pending` 只读展示，用户需重开任务或后续迭代补 resume 入口。
2. **`edit` 决策**：首期 **隐藏**，仅 `approve` / `reject`（澄清为 `respond`）。
3. **session grant 持久化**：首期 **进程内存**，会话/进程重启失效。
