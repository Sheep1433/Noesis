## Context

- `DeepResearchAgent` 经 `create_noesis_agent(tools=[], ...)` 创建，文件系统工具由 `FilesystemMiddleware` 注入；Skills 由 `SkillsMiddleware` 只读挂载 `/skills/`。
- `GeneralQAAgent` 已有 `build_kb_search_tools()` 条件挂载先例（`agent-common-qa` 规格）。
- 外部调研（Claude Code、`cloud-code` 还原源码、Hermes、OpenClaw、OpenCode 等）结论：**Noesis 应采纳本地 LangChain Tool + 可插拔 Provider 模式**，详见 `research-report.md`。

## Goals / Non-Goals

**Goals**

- 为深度研究补齐「关键词 → URL 列表」能力（`web_search`）与「已知 URL → 正文摘要」（`web_fetch`）。
- 工具模块可被未来其它 `qa_type` 复用，但首版仅接入 `DeepResearchAgent`。
- 首版 **Tavily 优先**：有 `TAVILY_API_KEY` 时 search/fetch 均走 Tavily；无 Key 或 Tavily 失败时回退 DDG（search）/ local（fetch）。
- 工具返回 JSON 字符串（与 `search_knowledge_base` 一致），便于 LLM 解析与 SSE 桥接。

**Non-Goals**

- MCP 网关、Anthropic Server Tool、OpenCode 远程 MCP 搜索。
- 前端专用 UI；复杂页浏览器自动化（仍用 `baoyu-url-to-markdown` skill）。

## Decisions

### 1. 模块布局（对齐 `kb_search_tool.py`）

```
backend/agent/tools/
├── __init__.py              # 导出 build_web_search_tools
├── web_search_tool.py       # web_search + web_fetch StructuredTool
└── web_providers/
    ├── __init__.py
    ├── resolver.py          # Tavily 优先 + 回退选择
    ├── tavily.py            # Tavily search + extract（主路径）
    ├── ddg.py               # DuckDuckGo 回退（search）
    └── local_fetch.py       # httpx + Readability 回退（fetch）
```

- `build_web_search_tools() -> list`：读取 `ModelConfig`，若 `web_search_enabled` 为假则返回 `[]`；否则返回 `[web_search_tool, web_fetch_tool]`（`web_fetch` 受 `web_fetch_enabled` 控制）。
- 函数签名与错误返回风格对齐 `search_knowledge_bases_all`：`json.dumps(..., ensure_ascii=False)`。

### 2. Provider 选择与回退（首版）

**解析顺序**（`web_providers/resolver.py`）：

```
web_search:
  1. 若 TAVILY_API_KEY 存在 → TavilyClient.search()
  2. 否则或 Tavily 抛错 → ddgs.DDGS().text()，logger.warning 记录回退

web_fetch:
  1. 若 TAVILY_API_KEY 存在 → TavilyClient.extract()
  2. 否则或 Tavily 抛错 → local_fetch（httpx GET + Readability），logger.warning 记录回退
```

| 能力 | 主 Provider | 回退 Provider | 参考实现 |
|------|-------------|---------------|----------|
| `web_search` | Tavily | DDG（`ddgs`） | Tavily SDK、`ddgs` 官方示例 |
| `web_fetch` | Tavily extract | local（httpx + Readability） | 同上 + OpenCode `webfetch.ts` |

工具返回 JSON **SHALL** 含 `provider` 字段（`"tavily"` | `"ddg"` | `"local"`），便于日志与调试。

配置（`config/env.py` → `ModelConfig` 或独立 `WebToolsConfig`）：

```yaml
# config.example.yaml 段（示意）
web_tools:
  search_enabled: true
  fetch_enabled: true
  max_search_results: 8
  fetch_max_chars: 4096
  fetch_timeout_seconds: 30
  # 首版不暴露 search_provider/fetch_provider 手动切换；统一走 resolver 回退链
```

环境变量：`TAVILY_API_KEY`（主路径）；未配置时自动 DDG/local，**SHALL NOT** 因缺 Key 导致工具不可用。

### 3. Agent 挂载点

```python
# deep_research_agent.py
web_tools = build_web_search_tools()
agent = create_noesis_agent(
    tools=web_tools,
    ...
)
# research-worker 子 Agent 同步传入 tools=web_tools
```

- 主 Agent 与子 Agent **SHALL** 使用相同工具列表，避免委派后子任务无法搜索。
- **SHALL NOT** 在 `FilesystemMiddleware` 之外重复注册同名工具。

### 4. `web_search` 工具契约

- **name**：`web_search`
- **参数**：`query: str`（必填）、`limit: int`（可选，默认 8，范围 1–20）
- **返回 JSON**：

```json
{
  "query": "...",
  "total_results": 5,
  "results": [
    { "title": "...", "url": "...", "snippet": "..." }
  ]
}
```

- 失败时返回 `{"error": "...", "query": "..."}`，**SHALL NOT** 抛未捕获异常中断整轮对话。

### 5. `web_fetch` 工具契约

- **name**：`web_fetch`
- **参数**：`url: str`（必填，http/https）
- **返回**：Markdown 文本（标题 + 正文），截断至 `fetch_max_chars`（默认 4096）。
- **安全**：校验 URL scheme；拒绝 `file://`、内网/metadata IP（基础 SSRF 防护，首版可用 URL 解析 + 私有网段 blocklist，对齐 OpenClaw 简化版）。
- 复杂/反爬页：工具 description 指引 Agent 改用 `/skills/baoyu-url-to-markdown`。

### 6. Skill 与 Prompt 更新

- `deep-research-v2/SKILL.md` Phase 2 工具表增加 `web_search` / `web_fetch`。
- `agent/prompts/deep_research.py` 的 `<skills>` 段补充：行业/竞品检索优先 `web_search`，正文抓取优先 `web_fetch`，学术仍可用 OpenAlex。

### 7. 与现有能力的关系

| 能力 | 关系 |
|------|------|
| `baoyu-url-to-markdown` | 互补：复杂页、CDP、YouTube |
| `execute + curl` OpenAlex | 保留：学术论文 API |
| `search_knowledge_base` | 独立：仅 COMMON_QA + Qdrant |
| Tavily MCP（故障运维） | 不混用：搜索走 LangChain Tool |

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| Tavily 限流/故障 | 单次调用失败后回退 DDG/local；日志 warning |
| DDG 不稳定/限流 | 返回结构化 error；建议配置 `TAVILY_API_KEY` |
| `web_fetch` 无法渲染 JS | skill 指引 + 工具 description 指向 baoyu |
| SSRF | URL 校验 + 私网段拒绝；首版不做全量 egress proxy |
| Token 膨胀 | `fetch_max_chars` 截断 + 现有 `SummarizationOffloadMiddleware` |

## Migration Plan

1. 实现 `agent-web-tools` 模块与配置（默认 `search_enabled=false` 可灰度）。
2. 启用配置后 `DeepResearchAgent` 挂载工具；更新 skill。
3. `uv run app.py` 验证启动；补单测。
4. 归档 change 时合并 delta 至 `openspec/specs/agent-web-tools/spec.md` 与 `agent-deep-research/spec.md`。

## Open Questions

- `web_fetch` local 回退是否依赖 `readability-lxml` 或仅用 `httpx` + 简单 strip（实现时按依赖体积选型）。
- Tavily 调用失败（非缺 Key）是否同样触发 DDG/local 回退，或仅返回 error（首版建议：**同样回退**，与缺 Key 行为一致）。
