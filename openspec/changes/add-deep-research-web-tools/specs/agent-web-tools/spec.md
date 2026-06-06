## Purpose

本能力规定 Noesis Agent 可复用的 **Web 检索工具**（`web_search`、`web_fetch`）的实现、Provider 选择、配置与返回契约。工具以 LangChain `StructuredTool` 形式由 `build_web_search_tools()` 工厂函数提供，供各 Agent 按需挂载；首版消费者为 `agent-deep-research`，**SHALL NOT** 经 MCP 或 HTTP API 单独暴露。

## ADDED Requirements

### Requirement: build_web_search_tools SHALL 提供固定双工具并由指定 Agent 挂载

系统 SHALL 在 `backend/agent/tools/web_search_tool.py`（或等价路径）提供 `build_web_search_tools()`，固定返回 `web_search` 与 `web_fetch` 两个 `StructuredTool`。

- **SHALL** 由 `GeneralQAAgent`（`COMMON_QA`）与 `DeepResearchAgent`（`DEEP_RESEARCH_QA`）挂载；**SHALL NOT** 由故障运维、测试用例生成等其它 `qa_type` 默认挂载。
- `web_search` 与 `web_fetch` 的执行 Provider SHALL 由运行时解析器决定：**Tavily 优先**；无 `TAVILY_API_KEY` 或 Tavily 调用失败时，`web_search` 回退 `ddg`，`web_fetch` 回退 `local`。

#### Scenario: 工厂返回双工具

- **WHEN** 调用 `build_web_search_tools()`
- **THEN** SHALL 返回长度为 2 的列表，工具名分别为 `web_search` 与 `web_fetch`

#### Scenario: 通用问答挂载 Web 工具

- **WHEN** `GeneralQAAgent` 完成初始化
- **THEN** 工具集 SHALL 包含 `web_search` 与 `web_fetch`（可与 `search_knowledge_base` 并存）

### Requirement: web_search SHALL 以 Tavily 为主并在无 Key 时回退 DDG

`web_search` 工具 SHALL：

- 接受参数 `query`（必填字符串）与可选 `limit`（整数，默认 8，范围 1–20）。
- 当 `TAVILY_API_KEY` 已配置且可用时，SHALL 使用 `tavily-python` 的 `TavilyClient.search()` 执行检索（参考 deer-flow `community/tavily/tools.py`）。
- 当 `TAVILY_API_KEY` 未配置，或 Tavily 调用抛出可恢复异常时，SHALL 自动回退至 `ddgs` 执行检索，并记录 `warning` 日志。
- 成功时返回 JSON 字符串，结构含 `query`、`provider`（`"tavily"` 或 `"ddg"`）、`total_results`、`results[]`，每项含 `title`、`url`、`snippet`（或等价字段）。
- 当 Tavily 与 DDG 均失败时，返回含 `error` 字段的 JSON 字符串，**SHALL NOT** 因未捕获异常中断 Agent 整轮执行。

#### Scenario: 已配置 Tavily Key 时使用 Tavily

- **WHEN** 环境已配置 `TAVILY_API_KEY` 且 Agent 调用 `web_search`
- **THEN** 系统 SHALL 使用 Tavily 执行检索，且返回 JSON 中 `provider` 为 `"tavily"`

#### Scenario: 未配置 Tavily Key 时回退 DDG

- **WHEN** 环境未配置 `TAVILY_API_KEY` 且 Agent 调用 `web_search`
- **THEN** 系统 SHALL 使用 `ddgs` 执行检索，返回 JSON 中 `provider` 为 `"ddg"`，**SHALL NOT** 返回仅含缺 Key 错误的 JSON

#### Scenario: Tavily 失败后回退 DDG

- **WHEN** `TAVILY_API_KEY` 已配置但 Tavily API 调用失败（如限流、超时）
- **THEN** 系统 SHALL 尝试 DDG 回退；若 DDG 成功，返回 `provider="ddg"`；若均失败，返回 `error`

### Requirement: web_fetch SHALL 以 Tavily extract 为主并在无 Key 时回退 local

`web_fetch` 工具 SHALL：

- 接受参数 `url`（必填，仅 `http://` 或 `https://`）。
- 拒绝私有/保留 IP 与非法 scheme 的请求（基础 SSRF 防护）。
- 当 `TAVILY_API_KEY` 已配置且可用时，SHALL 使用 `TavilyClient.extract()` 获取正文，截断至 `fetch_max_chars`（默认 4096）。
- 当 `TAVILY_API_KEY` 未配置，或 Tavily extract 失败时，SHALL 回退至 `local` Provider（HTTP GET + Readability/Markdown 提取），并记录 `warning` 日志。
- 成功返回 SHALL 标明实际 `provider`（`"tavily"` 或 `"local"`）（可在正文前缀或工具元数据中体现）。
- 工具描述 SHALL 指引：JS 重度渲染或反爬页面应改用 `/skills/baoyu-url-to-markdown`。

#### Scenario: 未配置 Tavily Key 时 web_fetch 回退 local

- **WHEN** 环境未配置 `TAVILY_API_KEY` 且 Agent 调用 `web_fetch` 抓取公网 HTTPS URL
- **THEN** 系统 SHALL 使用 local Provider 完成抓取，**SHALL NOT** 因缺 Key 直接失败

#### Scenario: 合法 HTTPS URL 抓取成功

- **WHEN** Agent 调用 `web_fetch` 且 `url` 为可访问的公网 HTTPS 页面
- **THEN** 返回 SHALL 为截断后的 Markdown 或文本，包含可辨识标题或 URL 前缀

#### Scenario: 内网 URL 被拒绝

- **WHEN** `url` 指向 RFC1918 私有地址或 localhost
- **THEN** 工具 SHALL 返回错误信息，**SHALL NOT** 发起请求

### Requirement: Web 工具配置 SHALL 经 env.py 管理

系统 SHALL 在 `backend/config/env.py` 声明 Web 工具相关配置字段（可挂载于 `ModelConfig` 或独立 settings 段），并通过 `.env` / yaml 合并加载；API Key **SHALL NOT** 硬编码于源码。

首版配置项 SHALL 至少包含：`max_search_results`、`fetch_max_chars`、`fetch_timeout_seconds`；`TAVILY_API_KEY` 经环境变量或 `.env` 注入。**SHALL NOT** 提供 `search_enabled` / `fetch_enabled` 开关。

#### Scenario: 配置从环境变量读取 Tavily Key

- **WHEN** `.env` 含 `TAVILY_API_KEY` 且 Agent 调用 `web_search` 或 `web_fetch`
- **THEN** 系统 SHALL 优先使用 Tavily 客户端，**SHALL NOT** 在无异常时直接使用 DDG/local

### Requirement: agent-web-tools SHALL 不定义 REST 或 SSE 扩展

本能力 **SHALL NOT** 新增 `/api/*` 路由或 SSE 事件类型；工具输出经现有 `LangGraphSseBridge` 的 `tool-call-*` / `tool-output-available` 帧透传至前端 `ToolCallCollapse`。

#### Scenario: 深度研究会话中展示 web_search

- **WHEN** `DEEP_RESEARCH_QA` 流式响应出现 `toolName=web_search`
- **THEN** 前端 SHALL 按 `platform-chat` 既有工具块规则平铺或嵌套展示，**SHALL NOT** 要求本能力定义新 UI 组件
