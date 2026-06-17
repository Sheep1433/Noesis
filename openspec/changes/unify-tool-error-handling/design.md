## Context

后端已按 **`common/`（无业务语义）+ `domain/`（业务域）+ `agent/`（LangGraph 运行时）** 分层；`backend/utils/` 已移除。与本 change 相关模块：

| 职责 | 路径 |
|------|------|
| 工具失败分类（新增） | `domain/chat/streaming/tool_failure.py` |
| 用户可见流式文案 | `domain/chat/streaming/failure_notice.py` |
| SSE 桥接 | `domain/chat/streaming/langgraph_sse.py`（`LangGraphSseBridge`） |
| 流式内存桥 | `domain/chat/streaming/bridge.py`（`MemoryStreamBridge`） |
| 消息 parts 构建 | `domain/chat/message_builder.py` |
| 工具异常 middleware | `agent/middlewares/tool_error_handling_middleware.py` |
| 运行时装配 | `agent/factory.py` |
| 问答编排与落库 | `services/qa_service.py` |
| 日志 | `common/logging.py` |

当前工具失败处理链路：

```
Tool 实现 / MCP / 子 Agent（agent/tools/*）
    → LangGraph ToolNode
    → ToolErrorHandlingMiddleware（agent/middlewares/）
    → astream_events（on_tool_end / on_tool_error）
    → LangGraphSseBridge（domain/chat/streaming/langgraph_sse.py）
        → tool-output-available + AssistantMessageBuilder（domain/chat/message_builder.py）
    → QaService（services/qa_service.py）落库 content.parts
    → 前端 ToolCallCollapse / SubagentCollapse
```

痛点：

1. **分类缺失**：`failure_notice.sanitize_user_facing_error` 仅覆盖基础设施标记与网络超时粗判，未区分入参错误、执行超时、权限等。
2. **双通道混用**：`ToolMessage.content` 同时服务 LLM 续推与用户展示，英文模板 `"Continue with available context..."` 可能泄漏到 UI。
3. **子 Agent 盲区**：`task` 工具内嵌失败常以纯文本 `Task Failed` 返回，bridge 难以统一分类。
4. **并行/乱序**：`message_builder` 已支持 `tool_call_id` 索引，但 bridge 层 `_safe_append_tool_output` 失败时仍需统一告警与兜底策略（与 bug 记录对齐）。

约束：不新增 SSE 事件类型；`GraphBubbleUp` / 用户 `stop_chat` 路径保持现有 `unify-backend-user-stop` 语义。

## Goals / Non-Goals

**Goals**

- 建立可扩展的 **ToolFailureCategory** 与 `classify_tool_failure(exc | raw_output) -> ToolFailure` 单一入口。
- 规定每层职责：middleware 产结构化 error ToolMessage；bridge 写 SSE + builder；`failure_notice` 与整轮 abort/stop 路径协同，不重复堆叠 tool 短句。
- 覆盖下表全部场景的可验收行为（至少单测 + 文档）。

**Non-Goals**

- 不实现工具调用时间线 API / 管理页（见 `docs/TODO.md`）。
- 不在本 change 内为每类错误做自动重试 / 熔断（仅向模型提供建议）。
- 不修改 LangGraph `recursion_limit` 或 loop detection 阈值。

## Decisions

### 1. 错误分类与用户文案

**内部分类**（10 类，用于 `errorCategory`、日志与模型续推）与 **用户可见摘要**（6 条固定短句）分离。用户侧不区分过细原因，细节只给模型与运维日志。

| Category（内部） | 典型来源 | 用户可见 `error`（固定） |
|------------------|----------|--------------------------|
| `network_unreachable` | ECONNREFUSED、主机不可达、MCP 连不上 | **连接失败** |
| `network_timeout` | Connect/Read 超时、socket hang up | **连接失败** |
| `execution_timeout` | bash/MCP 超时、`TimeoutError` | **执行超时** |
| `invalid_arguments` | ValidationError、MCP invalid params | **参数错误** |
| `tool_not_found` | 未注册工具 | **执行失败** |
| `permission_denied` | 403、Permission denied | **执行失败** |
| `infrastructure` | `[INTERNAL_ERROR]`、sandbox 未就绪 | **环境不可用** |
| `subagent_failure` | `task` 子图内有 tool error | **执行失败** |
| `cancelled` | 用户 stop | **已停止** |
| `unknown` | 其它 | **执行失败** |

用户文案常量（实现时集中定义，禁止散落拼接）：

```python
USER_TOOL_ERROR_MESSAGES = {
    "network_unreachable": "连接失败",
    "network_timeout": "连接失败",
    "execution_timeout": "执行超时",
    "invalid_arguments": "参数错误",
    "infrastructure": "环境不可用",
    "cancelled": "已停止",
    # tool_not_found / permission_denied / subagent_failure / unknown → 默认
}
DEFAULT_USER_TOOL_ERROR = "执行失败"
```

LLM 侧仍按分类给出可操作建议（检查 endpoint、缩小命令、修正字段等），见 §2 content 格式；**用户永不看到**英文模板或堆栈。

### 2. 单一模块 `domain/chat/streaming/tool_failure.py`

与 `failure_notice.py`、`langgraph_sse.py` 同目录，依赖方向：`agent/`、`services/` → `domain/chat/` → `common/`；**禁止** `domain/` import `agent/`。

```python
@dataclass
class ToolFailure:
    category: ToolFailureCategory
    message_for_llm: str      # 给模型的结构化说明（可含技术细节）
    message_for_user: str     # sanitize 后给用户 / SSE error 字段
    retryable: bool           # 是否建议模型重试
```

对外 API：

- `classify_tool_failure(exc: BaseException | None, *, raw: str = "", tool_name: str = "") -> ToolFailure`
- `build_error_tool_message(request, failure: ToolFailure) -> ToolMessage`
- `failure_to_sse_error_fields(failure: ToolFailure) -> dict`  # `error`, `errorCategory`

**LLM content 格式**（稳定可解析，英文键便于模型）：

```
[tool_error category=network_timeout retryable=true]
Tool 'bash' failed: connection timed out after 30s.
Suggestion: retry with a simpler command or check host reachability.
```

用户侧永不直接展示 `[tool_error ...]` 块，仅 `message_for_user`。

### 3. Middleware 行为

`ToolErrorHandlingMiddleware`：

1. `GraphBubbleUp` 原样抛出。
2. handler 返回 `ToolMessage(status="error")` 时：若 content 已是 `[tool_error` 前缀则透传；否则对 content 做 `classify_tool_failure(raw=content)` 并重写。
3. `Exception`：`classify_tool_failure(exc)` → `build_error_tool_message`。
4. 全量记录 `common.logging.logger.exception`；分类结果写入 `tool_failure_category` 日志字段。

工厂栈顺序不变（`agent/factory.py` → `build_noesis_runtime_middleware`）：`DanglingToolCallMiddleware` → `ToolErrorHandlingMiddleware` → …

### 4. SSE Bridge 与 Builder（`langgraph_sse.py` + `message_builder.py`）

`LangGraphSseBridge._on_tool_end` / `_on_tool_error`：

- `output_status == "error"` 时走 `classify_tool_failure(raw=clean_output)`。
- `_safe_append_tool_output` 失败必须 `logger.warning(..., tool_call_id=...)`，禁止 bare `pass`。
- `tool-output-available` 增加可选 `errorCategory`（字符串枚举值），`error` 为 `message_for_user`。

`on_tool_error`：与上相同，优先使用 event `data.error`。

`task` 工具结束：若结果为 `Task Failed` / `Task Succeeded` 包装文本，解析内嵌错误并映射为 `subagent_failure` 或 `success`。

### 5. 子 Agent 错误传播

- 子 Agent 内 tool 失败已由 middleware 转为 error ToolMessage；`task` 工具实现层在汇总子图结果时，若存在 error tool parts，前缀使用 `[tool_error category=subagent_failure]` 并附带首个失败工具名。
- 带 `parentTaskCallId` 的 SSE/落库 part 继承相同 `error` / `errorCategory`，前端 SubagentCollapse 无需新协议即可展示 error 状态。

### 6. 与流式失败说明的边界

| 场景 | 处理方 |
|------|--------|
| 单个 tool error，Agent 继续并产出正文 | 仅 tool part `status=error`；**不**追加 stream failure 尾注 |
| 整轮 abort（模型 API 超时、recursion） | 现有 `get_stream_failure_notice_text` |
| 用户 stop | `append_user_stop_notice_to_content` |
| 连接断开 | `append_disconnect_partial_content`，running → error |

`get_stream_failure_notice_text` 在已有 tool error part 时保持现有「（后续内容未能生成）」逻辑，不重复堆叠分类文案。

### 7. 前端（最小改动）

- `useSSEStream` / `messageParts`：解析 `errorCategory` 存入 tool part（可选，供调试或后续扩展）。
- `ToolCallCollapse`：**直接展示 SSE/落库的 `error` 短句**，不依赖前端再映射分类；无 `error` 时回退「执行失败」。
- 无 `errorCategory` 的旧历史消息：行为与现网一致。

## Risks / Trade-offs

- **[Risk] 正则误分类** → 单测覆盖典型样本；`unknown` 兜底；日志记录原始 exception。
- **[Risk] LLM content 格式变更影响续推** → 保持 `[tool_error category=...]` 可选解析，旧 plain text error 仍兼容。
- **[Risk] 子 Agent 解析脆弱** → 仅规范 `task` 工具输出格式，不解析任意嵌套 JSON。
- **[Trade-off] 不自动重试** → 降低复杂度；`retryable` 仅作模型提示。

## Migration Plan

1. 在 `domain/chat/streaming/tool_failure.py` 落地分类；改造 `tool_error_handling_middleware.py`、`langgraph_sse.py`；按需精简 `failure_notice.py` 中重复 marker。
2. 跑通单测与 SSE golden（import 路径 `domain.chat.streaming.*`）；`uv run app.py` 冒烟。
3. 前端 `useSSEStream.ts` / `messageParts.ts` 可选解析 `errorCategory`（向后兼容）。
4. 归档时合并 delta 至 `openspec/specs/agent-tool-failure-handling/spec.md` 与 `platform-chat`。

回滚：恢复 middleware 旧 `_build_error_message` 与 `langgraph_sse.py` 旧字段写入，删除 `errorCategory` 输出（前端忽略）。

## Open Questions

- MCP 客户端是否暴露结构化 error code（若后续有，可映射到 category 而非纯正则）。
- 是否在 `config.yaml` 增加 `tool_failure.expose_llm_detail_in_sse` 开关（默认 false，仅 user 文案进 SSE）。
