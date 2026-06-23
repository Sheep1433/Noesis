## Context

后端已按 **`common/`（无业务语义）+ `domain/`（业务域）+ `agent/`（LangGraph 运行时）** 分层。与本 change 相关模块：

| 职责 | 路径 |
|------|------|
| 枚举与工具失败异常类型 | `domain/chat/streaming/tool_errors.py` |
| 工具失败分类与文案 | `domain/chat/streaming/tool_failure.py` |
| 用户可见流式文案 | `domain/chat/streaming/failure_notice.py` |
| SSE 桥接 | `domain/chat/streaming/langgraph_sse.py`（`LangGraphSseBridge`） |
| 消息 parts 构建 | `domain/chat/message_builder.py` |
| 工具异常 middleware | `agent/middlewares/tool_error_handling_middleware.py` |
| 运行时装配 | `agent/factory.py` |
| 问答编排与落库 | `services/qa_service.py` |
| 日志 | `common/logging.py` |

当前工具失败处理链路：

```
Tool 实现 / MCP / 子 Agent（agent/tools/*）
    → LangGraph ToolNode
    → ToolErrorHandlingMiddleware
    → astream_events（on_tool_end / on_tool_error）
    → LangGraphSseBridge
        → tool-output-available + AssistantMessageBuilder
    → QaService 落库 content.parts
    → 前端 ToolCallCollapse / SubagentCollapse
```

### 现状问题（需在本 change 内一次性修正）

初版实现已在 `tool_failure.py` 落地分类与用户文案，但 **`_classify_from_text()` 以正则匹配自由文本** 作为兜底主路径。**对任意工具输出做自由文本正则推断 category，误报不可避免**——例如 bash 输出中的 `permission denied`、文档里的 `tool not found`、响应体中的 `403`、配置说明里的 `timeout` 等会被错误归类，进而污染用户短句、`retryable` 提示与日志 `tool_failure_category`。

约束：不新增 SSE 事件类型；`GraphBubbleUp` / 用户 `stop_chat` 路径保持现有 `unify-backend-user-stop` 语义。

## Goals / Non-Goals

**Goals**

- **类型优先、一次到位**：分类在异常抛出边界或 middleware 结构化入口完成；**禁止**对任意错误字符串做全局正则推断 category。
- 保留 **ToolFailureCategory** 十类枚举、LLM/用户双通道、`[tool_error category=...]` 传播协议、SSE `errorCategory` 与落库对齐。
- 工具/MCP/子 Agent 边界统一使用 **`ToolFailureError`**（或子类）显式携带 category；middleware 对标准库/httpx 异常做有限类型映射。
- **`__cause__` / `__context__` 剥链**：对 `raise RuntimeError(...) from httpx.*` 等包装异常，仍按链上类型映射（不解析 message）。
- **整轮流错误与单 tool 错误分流**：`failure_notice` 不得把整轮 abort 细节强行走 tool 分类器。
- 无法确定 category 时 **一律 `unknown`**，LLM 侧仍透传完整技术细节，用户侧为「执行失败」。
- 单测覆盖类型映射、显式传播、**误报反例**（文本含敏感子串但 category 必须为 `unknown`）。

**Non-Goals**

- 不引入 per-tool 自定义错误渲染、工具失败后置 hook、专用错误标记协议、独立遥测正则分桶、Shell 命令语义解释层等 scope 外能力。
- 不实现工具调用时间线 API / 管理页。
- 不为每类错误做自动重试 / 熔断（`retryable` 仅作模型提示）。
- 不修改 LangGraph `recursion_limit` 或 loop detection 阈值。
- 不增加 `config.yaml` 开关（如 `expose_llm_detail_in_sse`）。
- 不假设 deepagents / SubAgentMiddleware 会输出 `Task failed.` 文本（该前缀仅作向后兼容解析）。

## Decisions

### 1. 模块划分与依赖

| 模块 | 内容 |
|------|------|
| `tool_errors.py` | `ToolFailureCategory` 枚举、`ToolFailureError` 及子类 |
| `tool_failure.py` | `ToolFailure` dataclass、`classify_tool_failure`、`format_tool_error_detail`、文案常量、SSE 字段映射 |

依赖方向：`tool_failure.py` → `tool_errors.py`；`agent/tools/*` → `tool_errors.py`；**禁止** `tool_errors` / `tool_failure` import `agent/`。

### 2. 分类原则：类型优先，unknown 兜底，禁止文本正则推断

**分类决策树**（`classify_tool_failure` 唯一入口；`exc` 与 `raw` 可同时传入）：

```
1. exc 为 ToolFailureError（含子类）
     → 使用 exc.category / detail / retryable

2. exc 非空：对 exc 及 __cause__ / __context__ 剥链（深度 ≤ 2）
     → _map_exception(node)：查类型映射表 + errno
     → 若任一节点映射到非 unknown，采用该结果

3. raw 以 [tool_error category=...] 开头
     → 解析头（子 Agent 显式传播 / middleware 透传）

4. tool_name == "task" 且 raw 以 Task failed. / Task timed out 开头
     → subagent_failure（向后兼容；见 §7）

5. 其它
     → unknown；detail = format_tool_error_detail(exc, raw)
```

**步骤 2 与 3 的关系**：步骤 2 若得到 `unknown`，**必须继续**步骤 3～4（例如 `RuntimeError` 包裹但 `raw` 含 `[tool_error category=infrastructure]`）。步骤 2 若已得到非 `unknown`，**不再**用 raw 覆盖 category，但 detail 可合并 `format_tool_error_detail(exc, raw)`。

**明确删除**：`_NETWORK_UNREACHABLE_MARKERS`、`_INVALID_ARGUMENTS_MARKERS`、`is_infrastructure_failure(raw)` 全文正则链，以及 `"timed out" in text` 兜底逻辑。

**基础设施（tool 路径）**：仅当 `isinstance(exc, ToolInfrastructureError)`、剥链命中该类型，或步骤 3 解析到 `category=infrastructure` 时归类；**不再**对任意 `RuntimeError` 消息搜索 `docker pull` / `sandbox.*not ready`。

### 3. 异常链剥链（`__cause__` / `__context__`）

常见代码模式：

```python
except httpx.HTTPError as e:
    raise RuntimeError("页面抓取失败") from e
```

`_map_exception` SHALL 对异常链做深度受限遍历（默认 **≤ 2** 层），对链上每个节点应用类型映射表。这是**结构化**识别，不是解析 `str(exc)`。

剥链顺序建议：当前异常 → `__cause__` → `__context__`（若与 `__cause__` 不同且尚未访问），去重后按深度优先。

### 4. 异常类型模块 `domain/chat/streaming/tool_errors.py`

```python
class ToolFailureCategory(str, Enum): ...  # 10 类，定义于此文件

class ToolFailureError(Exception):
    category: ToolFailureCategory
    detail: str
    retryable: bool

class ToolInfrastructureError(ToolFailureError): ...
class ToolTimeoutError(ToolFailureError): ...
class ToolNetworkError(ToolFailureError): ...  # category 由构造参数指定 unreachable / timeout
class ToolValidationError(ToolFailureError): ...
class ToolNotFoundError(ToolFailureError): ...
class ToolPermissionError(ToolFailureError): ...
class ToolCancelledError(ToolFailureError): ...
```

工厂辅助（可选）：

```python
def raise_tool_failure(category, detail, *, retryable: bool | None = None) -> NoReturn: ...
```

### 5. 标准异常映射表（middleware 兜底，非文本匹配）

对剥链上每个节点，仅按 **异常类型** 与 **`errno`** 映射，**不**读 `str(exc)` 子串：

| 异常类型 | Category | retryable |
|----------|----------|-----------|
| `pydantic.ValidationError` | `invalid_arguments` | false |
| `ToolValidationError` | `invalid_arguments` | false |
| `asyncio.TimeoutError`, `TimeoutError` | `execution_timeout` | true |
| `httpx.ConnectTimeout` | `network_timeout` | true |
| `httpx.ReadTimeout`, `httpx.WriteTimeout`, `httpx.PoolTimeout` | `execution_timeout` | true |
| `httpx.ConnectError`, `ConnectionRefusedError` | `network_unreachable` | true |
| `PermissionError` | `permission_denied` | false |
| `asyncio.CancelledError`, `KeyboardInterrupt` | `cancelled` | false |
| `ToolCancelledError` | `cancelled` | false |
| `OSError` / `ConnectionError` 且 `errno` ∈ `{ECONNREFUSED, ENETUNREACH, EHOSTUNREACH, ENETDOWN}` | `network_unreachable` | true |
| `OSError` 且 `errno == ETIMEDOUT` | `network_timeout` | true |
| `OSError` 且 `errno ∈ {EACCES, EPERM}` | `permission_denied` | false |
| LangGraph / LangChain 工具未找到（若暴露专用异常类） | `tool_not_found` | false |
| 其它 | （继续剥链或返回 unknown） | false |

**注意**：`OSError ENOENT`、`FileNotFoundError` **不**映射为 `tool_not_found`（该 category 保留给「Agent 调用了未注册的工具名」）。

### 6. Category 与用户文案（不变）

| Category | 用户可见 `error` |
|----------|------------------|
| `network_unreachable`, `network_timeout` | 连接失败 |
| `execution_timeout` | 执行超时 |
| `invalid_arguments` | 参数错误 |
| `infrastructure` | 环境不可用 |
| `cancelled` | 已停止 |
| `tool_not_found`, `permission_denied`, `subagent_failure`, `unknown` | 执行失败（`DEFAULT_USER_TOOL_ERROR`） |

常量集中在 `tool_failure.py`：`USER_TOOL_ERROR_MESSAGES` / `DEFAULT_USER_TOOL_ERROR`。

### 7. 子 Agent（`task`）失败：Bridge 汇总为主，前缀兼容为辅

**事实**：仓库内与 deepagents `SubAgentMiddleware` **未约定** `Task failed.` / `Task Succeeded.` 输出格式；该字符串目前仅存在于测试与解析器。

**主路径（实现落点：`langgraph_sse.py`）**：

- `_resolve_tool_failure` 在处理 `task` 工具时，除解析 output 文本外，SHALL 结合 **子图上下文**（如 `task_tool_call_stack`、子 tool 的 `status=error` parts）判定 `subagent_failure`。
- 判定成立后，SSE / 落库写入 `errorCategory=subagent_failure`；若需回写主 Agent 可见文本，content SHOULD 以 `[tool_error category=subagent_failure retryable=false]` 开头。

**兼容路径**：

- `classify_task_tool_output` 保留对 `Task failed.` / `Task timed out` **行首前缀**的解析（`tool_name=="task"`），不作为通用文本正则。

**非目标**：本 change 不修改 deepagents 源码；若框架日后自带汇总格式，解析器可扩展，但仍优先 Bridge 侧子图状态。

### 8. `tool_failure.py` 对外 API

```python
@dataclass(frozen=True)
class ToolFailure:
    category: ToolFailureCategory
    message_for_llm: str
    message_for_user: str
    retryable: bool

def classify_tool_failure(exc, *, raw="", tool_name="") -> ToolFailure
def format_tool_error_detail(exc, raw="") -> str   # 截断至 10k
def build_error_tool_message(request, failure) -> ToolMessage
def failure_to_sse_error_fields(failure) -> dict     # error, errorCategory
def classify_task_tool_output(raw_output: str) -> ToolFailure | None
```

**LLM content**（category 已知）：

```
[tool_error category=network_timeout retryable=true]
Tool 'web_fetch' failed: ConnectTimeout: ...
Suggestion: retry with a simpler request or verify network connectivity.
```

**LLM content**（`unknown`）：

```
[tool_error category=unknown retryable=false]
Tool 'grep' failed: <format_tool_error_detail 全文>
Suggestion: continue with available context or choose an alternative tool.
```

### 9. Middleware 行为

`ToolErrorHandlingMiddleware`：

1. `GraphBubbleUp` 原样抛出。
2. handler 抛出 `Exception` → `classify_tool_failure(exc)` → `build_error_tool_message`。
3. handler 返回 `ToolMessage(status="error")`：
   - content 已有 `[tool_error` 前缀 → **透传**；
   - 否则 → `classify_tool_failure(None, raw=content)`（通常 `unknown`）→ 重写 content。
4. 日志：`tool_failure_category` + `tool_failure_detail`（原始 detail）。

### 10. 工具 / MCP 边界

| 来源 | 要求 |
|------|------|
| `agent/tools/web_providers/*` | `ToolValidationError` / `ToolNetworkError` / `ToolTimeoutError`；禁止 `RuntimeError("connection refused")` |
| MCP（`langchain_mcp_adapters`） | `get_tools()` 外包 `invoke` 装饰器：连接/鉴权 → `ToolInfrastructureError` / `ToolPermissionError` / `ToolNetworkError` |
| 沙箱 / Docker | `ToolInfrastructureError(detail="[INTERNAL_ERROR] ...")` |
| 工具返回 `status=error` 且已知原因 | `build_error_tool_message` 或自写 `[tool_error category=...]` |

推荐模式：`raise ToolNetworkError(...)` **或** `raise RuntimeError("...") from httpx_exc`（依赖剥链）。

### 11. `failure_notice`：整轮流错误 vs 单 tool 错误分流

**问题**：初版将 `sanitize_user_facing_error` 统一委托 `classify_tool_failure`，会导致整轮 `error` 事件（如含 `connection refused` 的 abort detail）在去掉正则后退回**英文原文**。

**决策**：拆为两条 API，禁止混用：

| API | 用途 | 实现 |
|-----|------|------|
| `sanitize_tool_error(raw)` | 单 tool 失败、tool part `error` | 委托 `classify_tool_failure(None, raw=...).message_for_user`；未知时默认「执行失败」 |
| `sanitize_stream_error(raw)` | 整轮 SSE `error` 事件、`get_stream_failure_notice_text` 入口 | **独立**规则：模型 API 超时、recursion、连接断开；`[INTERNAL_ERROR]` **行首/整段前缀**精确匹配 →「环境不可用」；其余裁剪/默认文案 |

`langgraph_sse.py` 中 `__tw_error__` / `error` 业务帧 SHALL 调用 `sanitize_stream_error`，**不得**调用 `sanitize_tool_error`。

`is_internal_infrastructure_error` 保留于 `failure_notice`，仅用于 **stream** 路径；实现为 `[INTERNAL_ERROR]` 前缀检测，**不**使用 docker pull 等宽泛正则。

`sanitize_user_facing_error` 可保留为兼容别名，内部根据调用场景文档化；新代码应显式选用上述二者之一。

`get_stream_failure_notice_text` 保持现有模型超时 / recursion / 并行 tool error 尾注逻辑；与 tool 分类器解耦。

### 12. SSE Bridge 与 Builder

- `on_tool_end` / `on_tool_error`：`status=error` → `classify_tool_failure` → SSE `error` + `errorCategory`。
- `task` 工具：`_resolve_tool_failure` 增加子图 error 判定（§7）。
- `_safe_append_tool_output` 失败 → `logger.warning(..., tool_call_id=...)`。
- tool part 落库 `error` 使用 `failure.message_for_user`（来自 tool 分类器）。

### 13. 与流式失败说明的边界

| 场景 | 处理方 |
|------|--------|
| 单个 tool error，Agent 继续 | 仅 tool part `status=error` |
| 整轮 abort（模型 API 超时、recursion） | `get_stream_failure_notice_text` + `sanitize_stream_error` |
| 用户 stop | `append_user_stop_notice_to_content` |
| 连接断开 | `append_disconnect_partial_content` |

### 14. 前端（不变）

- 解析可选 `errorCategory`；展示后端 `error` 短句。

## 误报反例（单测必须覆盖）

以下输入经 `classify_tool_failure(None, raw=...)` **必须**为 `unknown`：

| 输入 | 不得误判为 |
|------|-----------|
| `HTTP 403 Forbidden in response body` | `permission_denied` |
| `chmod: permission denied` | `permission_denied` |
| `documentation: tool not found in registry` | `tool_not_found` |
| `request timeout is configured to 30s` | `execution_timeout` |
| `subscription canceled successfully` | `cancelled` |
| `ValidationError mentioned in log excerpt` | `invalid_arguments` |
| `RuntimeError("connection refused")`（无 cause） | `network_unreachable` |
| `RuntimeError("[INTERNAL_ERROR] docker pull ...")`（无 ToolInfrastructureError） | `infrastructure` |

以下 **必须**正确分类：

| 输入 | Category |
|------|----------|
| `ToolNetworkError(..., category=network_unreachable)` | `network_unreachable` |
| `httpx.ConnectTimeout(...)` | `network_timeout` |
| `RuntimeError("页面抓取失败") from httpx.ConnectError(...)` | `network_unreachable`（剥链） |
| Pydantic `ValidationError` | `invalid_arguments` |
| `[tool_error category=infrastructure] sandbox not ready` | `infrastructure` |
| `sanitize_stream_error("[INTERNAL_ERROR] ...")` | 用户文案「环境不可用」（stream 路径，非 tool 分类器） |

## Risks / Trade-offs

- **[Risk] unknown 增多** → 同步改 web_providers、MCP 包装、Bridge task 判定；unknown 优于误报。
- **[Risk] 旧 ToolMessage 无 `[tool_error` 头** → `unknown` + LLM 全文 detail。
- **[Risk] stream/tool sanitize 混用** → 通过 §11 分流与单测锁定调用点。
- **[Trade-off] 不做 bash 语义层** → 无 BashTool；未来在工具内抛 `ToolFailureError`。

## Implementation（一次性交付）

1. 新增 `tool_errors.py`（枚举 + 异常层次）。
2. 重写 `tool_failure.py`：决策树、剥链、删正则表；保留头解析与 task 前缀兼容。
3. 改造 `failure_notice.py`：`sanitize_tool_error` / `sanitize_stream_error` 分流；`langgraph_sse` 整轮 error 改调 stream 路径。
4. 增强 `langgraph_sse._resolve_tool_failure`：task 子图 error → `subagent_failure`。
5. 改造 middleware（日志字段）、`web_providers`、MCP invoke 包装。
6. 单测：误报反例、剥链、stream/tool sanitize 分流、middleware、SSE contract。
7. `uv run pytest` + `uv run app.py` 冒烟。

回滚：恢复 `_classify_from_text` 版 `tool_failure.py`（不推荐）。
