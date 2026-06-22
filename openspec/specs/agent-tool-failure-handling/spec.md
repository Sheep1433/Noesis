# agent-tool-failure-handling

工具失败分类、middleware 转换、LLM/用户双通道文案、子 Agent 错误传播约定。

## Requirements

### Requirement: 系统 SHALL 对工具失败进行统一分类

系统在 Agent 运行时（经 `create_noesis_agent` 工厂装配的全部 `qa_type` 路径）中，SHALL 将每一次工具失败（异常抛出或 `ToolMessage.status=error`）映射到下列互斥分类之一：`network_unreachable`、`network_timeout`、`execution_timeout`、`invalid_arguments`、`tool_not_found`、`permission_denied`、`infrastructure`、`subagent_failure`、`cancelled`、`unknown`。

`ToolFailureCategory` 枚举与 `ToolFailureError` 异常层次 SHALL 定义于 `backend/domain/chat/streaming/tool_errors.py`；分类与文案组装 SHALL 集中于 `backend/domain/chat/streaming/tool_failure.py`。供 `ToolErrorHandlingMiddleware`、`LangGraphSseBridge` 与 `failure_notice.sanitize_tool_error` 共用；禁止各 Agent 或 `agent/tools/*` 自行维护平行正则表。

分类 SHALL 按下列优先级决策，**不得**对任意错误自由文本做正则推断 category：

1. `ToolFailureError`（含子类）→ 使用其 `category`；
2. 对 `exc` 及 `__cause__` / `__context__` 剥链（深度 ≤ 2）应用标准库 / httpx 类型映射表（仅 `type(node)` 与 `errno`，不解析 `str(exc)` 子串）；若映射非 `unknown` 则采用；
3. `raw` 以 `[tool_error category=...]` 开头 → 解析头；
4. `tool_name == "task"` 且 `raw` 以 `Task failed.` / `Task timed out` 开头 → `subagent_failure`（向后兼容）；
5. 其它 → `unknown`。

步骤 2 得到 `unknown` 时 SHALL 继续步骤 3～4；步骤 2 已得非 `unknown` 时 SHALL NOT 被 `raw` 覆盖 category。

#### Scenario: 连接被拒绝映射为 network_unreachable

- **WHEN** 工具执行抛出 `httpx.ConnectError`、`ConnectionRefusedError`，或 `OSError` 且 `errno` 为 `ECONNREFUSED`
- **THEN** 系统 SHALL 将该失败分类为 `network_unreachable`

#### Scenario: 包装异常经 cause 链映射

- **WHEN** 工具抛出 `RuntimeError("页面抓取失败")`，且 `__cause__` 为 `httpx.ConnectError`
- **THEN** 系统 SHALL 分类为 `network_unreachable`，SHALL NOT 因外层为 `RuntimeError` 而归为 `unknown`

#### Scenario: 入参校验失败映射为 invalid_arguments

- **WHEN** 工具执行因 Pydantic `ValidationError` 或 `ToolValidationError` 终止
- **THEN** 系统 SHALL 将该失败分类为 `invalid_arguments`

#### Scenario: 显式 ToolFailureError 优先于文本内容

- **WHEN** 工具抛出 `ToolInfrastructureError`，且其 `detail` 不含任何基础设施关键字
- **THEN** 系统 SHALL 分类为 `infrastructure`，SHALL NOT 因 detail 文本缺失关键字而降为 `unknown`

#### Scenario: 自由文本含敏感子串不得误分类

- **WHEN** 工具以 `status=error` 返回 content 为 `HTTP 403 Forbidden in response body`，且不含 `[tool_error category=...]` 头，且无类型化异常
- **THEN** 系统 SHALL 分类为 `unknown`，用户侧 `error` SHALL 为「执行失败」，SHALL NOT 分类为 `permission_denied`

#### Scenario: 无法确定时映射为 unknown

- **WHEN** 工具执行失败且不满足类型映射、剥链、显式头或 task 约定前缀
- **THEN** 系统 SHALL 分类为 `unknown`；LLM 侧 SHALL 包含 `format_tool_error_detail` 全文；用户侧 `error` SHALL 为「执行失败」

### Requirement: 工具边界 SHALL 通过 ToolFailureError 显式分类

`agent/tools/*`、MCP 包装层及基础设施调用方在**能够确定失败原因**时，SHALL 抛出 `ToolFailureError` 或其子类，SHALL NOT 依赖 middleware 从 `RuntimeError` 消息文本推断 category。允许 `raise RuntimeError("...") from <typed_exc>` 以保留剥链。

#### Scenario: 网络抓取超时

- **WHEN** `web_fetch` 因连接超时失败
- **THEN** 实现 SHALL 抛出 `ToolTimeoutError` / `ToolNetworkError`，或 `raise ... from httpx.ConnectTimeout`，SHALL NOT 抛出裸 `RuntimeError("timed out")` 且无 cause

#### Scenario: 沙箱不可用

- **WHEN** 沙箱或 MCP 环境未就绪
- **THEN** 实现 SHALL 抛出 `ToolInfrastructureError`；用户侧仅展示「环境不可用」

### Requirement: 整轮流错误与单 tool 错误 SHALL 分流脱敏

`failure_notice` SHALL 提供：

- `sanitize_tool_error(raw)` — 委托 `classify_tool_failure`，用于 tool part / `tool-output-available.error`；
- `sanitize_stream_error(raw)` — 用于整轮 SSE `error` 事件与 `get_stream_failure_notice_text`，SHALL NOT 委托 tool 文本正则分类器。

`sanitize_stream_error` SHALL 对 `[INTERNAL_ERROR]` 做前缀级基础设施脱敏（用户文案「环境不可用」），并保留模型 API 超时、recursion 等整轮专用判断。`LangGraphSseBridge` 处理 `__tw_error__` / `error` 业务帧 SHALL 调用 `sanitize_stream_error`。

#### Scenario: 整轮 infrastructure 脱敏不依赖 tool 分类器

- **WHEN** SSE bridge 发出整轮 `error`，detail 为 `[INTERNAL_ERROR] Docker image not found`
- **THEN** 用户可见文案 SHALL 为「环境不可用」，即使该字符串未经 `ToolInfrastructureError` 抛出

#### Scenario: 整轮连接错误不误走 tool 正则

- **WHEN** 整轮 abort detail 为 `connection refused`，且非单 tool `tool-output-available`
- **THEN** `sanitize_stream_error` SHALL 按 stream 规则处理，SHALL NOT 调用 `classify_tool_failure` 将自由文本标为 `network_unreachable`

### Requirement: 工具失败 SHALL 转为可续推的 error ToolMessage

当工具调用在 `ToolErrorHandlingMiddleware` 作用域内抛出异常（`GraphBubbleUp` 除外）时，系统 SHALL 捕获异常并返回 `status=error` 的 `ToolMessage`，`tool_call_id` 与触发调用一致，content 含 `[tool_error category=...]` 与技术 detail。

`ToolMessage(status=error)` 且 content 已含 `[tool_error` 前缀时 SHALL 透传；否则 SHALL 重写（通常 `unknown` + 全文 detail）。

#### Scenario: 网络超时后 Agent 可继续推理

- **WHEN** 工具因 `httpx.ConnectTimeout` 失败且 middleware 已转为 error `ToolMessage`
- **THEN** 同轮 Agent SHALL 可继续后续模型调用

#### Scenario: GraphBubbleUp 不被吞掉

- **WHEN** 工具处理器抛出 `GraphBubbleUp`
- **THEN** middleware SHALL 原样重新抛出

### Requirement: 子 Agent 工具失败 SHALL 可向上归类

主 Agent 通过 `task` 工具委派子 Agent 时，`subagent_failure` 的**主判定** SHALL 在 `LangGraphSseBridge._resolve_tool_failure`（或等价 bridge 逻辑）结合子图 `status=error` tool parts / `task_tool_call_stack` 完成，SHALL NOT 仅依赖 deepagents 输出固定文案。

兼容：`Task failed.` / `Task timed out` 行首前缀仍可解析为 `subagent_failure`。推荐向主 Agent 回写 `[tool_error category=subagent_failure retryable=false]` 开头的内容。

#### Scenario: task 子图含 error tool

- **WHEN** `task` 执行期间子 Agent 内至少一个 tool part 为 `status=error`
- **THEN** bridge SHALL 将对应 task 的 `errorCategory` 标为 `subagent_failure`，task tool part SHALL 为 `status=error`

### Requirement: LLM 可读详情与用户可见文案 SHALL 分离

对每个已分类的工具失败，系统 SHALL 同时生成模型侧结构化 `ToolMessage.content` 与用户侧固定短句（SSE / 落库 `error`）。`unknown` 时模型侧 SHALL 透传 `format_tool_error_detail` 全文；用户侧 SHALL 为「执行失败」。

基础设施类失败的用户侧文案 SHALL 为「环境不可用」。入参错误用户侧 SHALL 为「参数错误」。

#### Scenario: 基础设施错误脱敏（tool 路径）

- **WHEN** 工具抛出 `ToolInfrastructureError` 或解析到 `[tool_error category=infrastructure]`
- **THEN** 用户侧 `error` SHALL 为「环境不可用」，SHALL NOT 暴露 Docker/MCP 内部细节

#### Scenario: 入参错误用户文案固定

- **WHEN** 工具失败分类为 `invalid_arguments`
- **THEN** 用户侧 `error` SHALL 为「参数错误」；字段级修正提示 SHALL 仅出现在模型侧 content
