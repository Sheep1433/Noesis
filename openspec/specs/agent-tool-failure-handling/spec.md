# agent-tool-failure-handling

工具失败分类、middleware 转换、LLM/用户双通道文案、子 Agent 错误传播约定。

## Requirements

### Requirement: 系统 SHALL 对工具失败进行统一分类

系统在 Agent 运行时（经 `create_noesis_agent` 工厂装配的全部 `qa_type` 路径）中，SHALL 将每一次工具失败（异常抛出或 `ToolMessage.status=error`）映射到下列互斥分类之一：`network_unreachable`、`network_timeout`、`execution_timeout`、`invalid_arguments`、`tool_not_found`、`permission_denied`、`infrastructure`、`subagent_failure`、`cancelled`、`unknown`。

分类逻辑 SHALL 集中在 `backend/domain/chat/streaming/tool_failure.py`，供 `agent/middlewares/tool_error_handling_middleware.py`、`domain/chat/streaming/langgraph_sse.py`（`LangGraphSseBridge`）与 `domain/chat/streaming/failure_notice.py` 共用；禁止各 Agent 或 `agent/tools/*` 自行维护平行正则表。

#### Scenario: 连接被拒绝映射为 network_unreachable

- **WHEN** 工具执行抛出包含 `Connection refused` 或 `ECONNREFUSED` 的异常
- **THEN** 系统 SHALL 将该失败分类为 `network_unreachable`

#### Scenario: 入参校验失败映射为 invalid_arguments

- **WHEN** 工具执行因参数校验失败（如 Pydantic `ValidationError` 或 MCP invalid params）而终止
- **THEN** 系统 SHALL 将该失败分类为 `invalid_arguments`

#### Scenario: 无法匹配时映射为 unknown

- **WHEN** 工具执行失败且不满足任何已定义分类规则
- **THEN** 系统 SHALL 将该失败分类为 `unknown`，用户侧 `error` SHALL 为「执行失败」

### Requirement: 工具失败 SHALL 转为可续推的 error ToolMessage

当工具调用在 `ToolErrorHandlingMiddleware` 作用域内抛出异常（`GraphBubbleUp` 除外）时，系统 SHALL 捕获异常并返回 `ToolMessage`，其 `status` SHALL 为 `error`，且 `tool_call_id` SHALL 与触发调用的 id 一致。

该 `ToolMessage` 的 content SHALL 面向后续模型推理提供结构化说明（含分类标识与可行建议），SHALL NOT 因未捕获异常而导致整轮 `POST /api/chat/sessions/stream` 请求崩溃。

#### Scenario: 网络超时后 Agent 可继续推理

- **WHEN** 某次 `bash` 工具因连接超时而失败，且 middleware 已将其转为 error `ToolMessage`
- **THEN** 同轮 Agent SHALL 可继续后续模型调用，而不因未处理异常而中断整个流

#### Scenario: GraphBubbleUp 不被吞掉

- **WHEN** 工具处理器抛出 `GraphBubbleUp`
- **THEN** `ToolErrorHandlingMiddleware` SHALL 原样重新抛出，SHALL NOT 将其转换为 error `ToolMessage`

### Requirement: 子 Agent 工具失败 SHALL 可向上归类

当主 Agent 通过 `task` 工具委派子 Agent，且子执行路径中存在已分类的工具失败时，系统 SHALL 将汇总后的 task 结果标记为 `subagent_failure`（或在其 error 语义中等价可识别），使主 Agent 与前端均能识别为子任务失败而非匿名文本错误。

#### Scenario: task 内嵌 tool error 汇总

- **WHEN** `task` 工具完成的子图包含至少一个 `status=error` 的工具结果
- **THEN** 返回给主 Agent 的 task 结果 SHALL 携带 `subagent_failure` 分类语义，且 SSE/落库中对应 task 的 tool part SHALL 为 `status=error`

### Requirement: LLM 可读详情与用户可见文案 SHALL 分离

对每个已分类的工具失败，系统 SHALL 同时生成：

- **模型侧**：含分类与行动建议的结构化说明（写入 `ToolMessage.content`）；
- **用户侧**：经脱敏与裁剪后的摘要（写入 SSE `tool-output-available.error` 与落库 tool part 的 `error` 字段）。

基础设施类失败（`infrastructure`）的用户侧文案 SHALL 为固定短句 **「环境不可用」**，SHALL NOT 暴露 Docker/MCP 内部细节。

#### Scenario: 基础设施错误脱敏

- **WHEN** 工具失败原始信息包含 `[INTERNAL_ERROR]` 或 sandbox 未就绪标记
- **THEN** 用户侧 `error` 字段 SHALL 为「环境不可用」，SHALL NOT 向用户展示原始 Docker 镜像名或内部堆栈

#### Scenario: 入参错误用户文案固定

- **WHEN** 工具失败分类为 `invalid_arguments`
- **THEN** 用户侧 `error` 字段 SHALL 为「参数错误」；字段级修正提示 SHALL 仅出现在模型侧 `ToolMessage.content`
