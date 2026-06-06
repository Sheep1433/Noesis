## Why

深度研究 Agent（`DEEP_RESEARCH_QA`）当前仅依赖 `FilesystemMiddleware`（含 `execute`）与 `backend/skills` 中的协议指引：`deep-research-v2` 通过 OpenAlex `curl` 做学术检索、`baoyu-url-to-markdown` 抓取已知 URL，但**缺少通用 Web 关键词搜索**，无法完成行业/竞品/政策类「从 query 发现 URL」的多源检索（Phase 2）。对标 deer-flow、Hermes、OpenClaw 等成熟 Agent  harness，联网检索应采用**本地 LangChain Tool + 可插拔 Provider**，而非 Tavily MCP 或 Anthropic Server Tool（Noesis 使用 DashScope/Qwen，不具备后者运行时）。

调研结论见同目录 `research-report.md`。

## What Changes

- 新增共享 Web 工具模块 `backend/agent/tools/web_search_tool.py`（及可选 `web_fetch`），提供 `build_web_search_tools()` 工厂函数，对齐 `build_kb_search_tools()` 模式。
- 首版 Provider 策略：**Tavily 优先**（`tavily-python`，`TAVILY_API_KEY`）；无 Key 或 Tavily 调用失败时 **自动回退**：`web_search` → DuckDuckGo（`ddgs`），`web_fetch` → 本地 httpx + Readability。
- `DeepResearchAgent` 与 `research-worker` 子 Agent 挂载 `web_search`（及 `web_fetch`）工具；`tools` 参数由空列表改为 `build_web_search_tools()` 返回值。
- 更新 `backend/skills/deep-research-v2/SKILL.md` 工具表：声明 `web_search` / `web_fetch` 为首选检索路径，保留 OpenAlex 与 `baoyu-url-to-markdown` 作为学术/复杂页补充。
- 配置项写入 `config/env.py`（`WebSearchConfig` 或 `ModelConfig` 扩展段）：`web_search_enabled`、`web_search_provider`、`web_fetch_enabled`、`TAVILY_API_KEY` 等；**禁止硬编码 API Key**。
- 补充单元测试与 `backend/tests/` 下 Provider mock 用例；**不新增** SSE 事件类型、**不新增** REST API。

## Non-Goals

- 不引入 Tavily MCP 进程或独立搜索微服务。
- 不为 `COMMON_QA` / `FAULT_OPERATION_QA` 默认挂载 Web 搜索（本 change 仅深度研究；后续可复用 `agent-web-tools` 模块）。
- 不替代 `baoyu-url-to-markdown`（反爬、CDP、YouTube 等复杂页仍走 skill）。
- 不实现 OpenClaw 式 13 Provider 插件体系；首版仅支持 **Tavily → DDG/local** 单级回退，不做多 Provider 链式探测。

## Capabilities

### New Capabilities

- `agent-web-tools`：Noesis Agent 可复用的 `web_search` / `web_fetch` LangChain 工具、Provider 选择与配置契约。

### Modified Capabilities

- `agent-deep-research`：深度研究 Agent 与子 Agent 必须挂载 Web 工具；`deep-research-v2` skill 协议与工具依赖表同步更新。

## Impact

- **后端**：`backend/agent/tools/`、`backend/agent/deep_research_agent.py`、`backend/config/env.py`、`backend/skills/deep-research-v2/SKILL.md`、`backend/pyproject.toml`（`ddgs`、可选 `tavily-python`）。
- **API/SSE**：无破坏性变更；`POST /api/chat/sessions/stream` 在 `qa_type=DEEP_RESEARCH_QA` 时 Agent 工具集扩展，前端 `ToolCallCollapse` 按现有 `toolName` 规则展示 `web_search` / `web_fetch`。
- **依赖**：新增 `tavily-python`（主）、`ddgs`（搜索回退）；参考 deer-flow `community/tavily/tools.py`。
- **文档**：`research-report.md`（本 change 内调研归档）；实现后更新 `AGENTS.md` 深度研究工具说明（归档时）。
