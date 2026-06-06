# Web Search / Web Fetch 实现调研报告

> **归档位置**：`openspec/changes/add-deep-research-web-tools/research-report.md`  
> **关联变更**：`add-deep-research-web-tools`（proposal / design / specs）  
> **调研方式**：本地源码（deer-flow、`cloud-code` Claude Code 还原）、GitHub 远程源码（OpenCode、Hermes、OpenClaw）、Anthropic 官方 API 文档  
> **日期**：2026-06-06

---

## 1. 执行摘要

| 结论 | 说明 |
|------|------|
| Noesis 当前缺口 | 深度研究无通用 `web_search`；仅有 OpenAlex + 已知 URL 抓取（baoyu skill） |
| 推荐路线 | **deer-flow / Hermes 模式**：本地 LangChain Tool + 可插拔 Provider |
| 不推荐 | Tavily MCP（过重）、Anthropic Server Tool（需 Claude API，与 DashScope 不兼容） |
| 首版 Provider | **Tavily 优先**；无 Key 时 `web_search` → DDG、`web_fetch` → local |
| 互补能力 | 保留 `baoyu-url-to-markdown`（复杂页/CDP）、OpenAlex（学术） |

---

## 2. Noesis 现状

### 2.1 能力分层

```
DeepResearchAgent
├── FilesystemMiddleware → ls/read/write/grep/execute
├── SkillsMiddleware → /skills/ 只读指引
└── tools=[]  ← 无 LangChain 联网工具
```

| 来源 | 能力 | 限制 |
|------|------|------|
| `deep-research-v2` skill | 7 阶段研究协议 | 不执行搜索，仅指引 |
| `execute + curl` | OpenAlex API | 需已知关键词，非通用搜索 |
| `baoyu-url-to-markdown` | 已知 URL → Markdown | 不能从 query 发现 URL |
| `web-access` skill（Cursor 本地） | WebSearch/WebFetch | **Noesis 运行时不可用** |

### 2.2 相关文件

- `backend/agent/deep_research_agent.py`
- `backend/skills/deep-research-v2/SKILL.md`
- `openspec/specs/agent-deep-research/spec.md`

---

## 3. Claude Code（`cloud-code` 还原源码）

**源码路径**：`/Users/zzq/Desktop/code/cloud-code/claude-code-source/src/tools/`

> 非 Anthropic 官方开源；从 `@anthropic-ai/claude-code` npm 包 source map 还原。

### 3.1 WebSearch — 客户端包装 + Anthropic Server Tool

| 层级 | 可见性 | 说明 |
|------|--------|------|
| 客户端 | ✅ 完整 | `WebSearchTool/WebSearchTool.ts` |
| 搜索执行 | ❌ 黑盒 | Anthropic `web_search_20250305` Server Tool |

**流程**：

1. 用户侧工具名 `WebSearch`，参数 `query`、`allowed_domains`、`blocked_domains`
2. `call()` 内 `queryModelWithStreaming` + `extraToolSchemas: [{ type: 'web_search_20250305', max_uses: 8 }]`
3. 解析流中 `server_tool_use` / `web_search_tool_result`
4. `mapToolResultToToolResultBlockParam` 格式化回主对话，强制 Sources 引用

**关键文件**：

- `WebSearchTool/WebSearchTool.ts`
- `WebSearchTool/prompt.ts`
- `utils/messages.ts`（`web_search_tool_result` 处理）

### 3.2 WebFetch — 纯客户端实现

与 Anthropic API 文档中的 `web_fetch` **Server Tool 不是同一实现**。

| 步骤 | 实现 |
|------|------|
| 抓取 | `getURLMarkdownContent()` → axios HTTP GET |
| 安全 | `checkDomainBlocklist()` → `api.anthropic.com/api/web/domain_info` |
| 转换 | turndown HTML→Markdown |
| 提取 | `applyPromptToMarkdown()` → **Haiku** 按 `prompt` 从正文提取 |
| 缓存 | 15min LRU，50MB |

**参数**：`url` + `prompt`（与 deer-flow 仅 `url` 不同）

**权限**：`WebFetch(domain:example.com)` 按域名授权

---

## 4. deer-flow

**仓库**：https://github.com/bytedance/deer-flow  
**核心路径**：`backend/packages/harness/deerflow/community/`

### 4.1 架构

```
config.yaml → tools[].use: deerflow.community.<provider>.tools:<fn>
           → get_available_tools() → LangChain BaseTool
           → Lead Agent / Subagent
```

- 同一工具名（`web_search` / `web_fetch`）**择一 Provider**，无运行时自动 fallback
- 切换 Provider：改 `use` 路径，注释冲突条目

### 4.2 Provider 矩阵

**web_search**：DDG（默认）、Tavily、Exa、Firecrawl、InfoQuest  
**web_fetch**：Jina（默认）、Tavily、Exa、Firecrawl、InfoQuest  
**image_search**：DDG、InfoQuest

### 4.3 典型实现

**DDG**（`ddg_search/tools.py`）：

```python
@tool("web_search")
def web_search_tool(query: str, max_results: int = 5) -> str:
    results = DDGS().text(query, max_results=max_results)
    return json.dumps({ "query", "total_results", "results": [...] })
```

**Jina fetch**（`jina_ai/tools.py`）：

```python
@tool("web_fetch")
async def web_fetch_tool(url: str) -> str:
    html = await JinaClient().crawl(url)  # POST https://r.jina.ai/
    article = ReadabilityExtractor().extract_article(html)
    return article.to_markdown()[:4096]
```

**配置示例**（`config.example.yaml`）：

```yaml
tools:
  - name: web_search
    use: deerflow.community.ddg_search.tools:web_search_tool
  - name: web_fetch
    use: deerflow.community.jina_ai.tools:web_fetch_tool
```

---

## 5. OpenCode

**仓库**：https://github.com/anomalyco/opencode  
**文档**：https://opencode.ai/docs/tools/

### 5.1 websearch — 远程 MCP

**文件**：`packages/opencode/src/tool/websearch.ts`、`mcp-websearch.ts`

| Provider | 端点 | 协议 |
|----------|------|------|
| Exa | `https://mcp.exa.ai/mcp` | JSON-RPC `tools/call` |
| Parallel | `https://search.parallel.ai/mcp` | 同上 |

- 启用：`OPENCODE_ENABLE_EXA=1` 或 OpenCode 官方 Provider
- Provider 选择：`OPENCODE_WEBSEARCH_PROVIDER` 或 sessionID hash 在 exa/parallel 间分配
- 参数：`query`、`numResults`、`livecrawl`、`type`(auto/fast/deep)

### 5.2 webfetch — 本地 HTTP

**文件**：`packages/opencode/src/tool/webfetch.ts`

- HttpClient GET + htmlparser2 去 script/style + turndown
- 5MB 上限，Cloudflare 403 换 UA 重试
- 与 Claude Code WebFetch 类似，但**无 Haiku 二次提取**

---

## 6. Hermes Agent

**仓库**：https://github.com/NousResearch/hermes-agent  
**文档**：https://hermes-agent.nousresearch.com/docs/user-guide/features/web-search

### 6.1 架构（PR #25182 插件化）

```
tools/web_tools.py
  → web_search_tool() / web_extract_tool()
  → agent/web_search_registry.py
  → plugins/web/<name>/provider.py  (WebSearchProvider ABC)
```

### 6.2 工具

| 工具 | 用途 |
|------|------|
| `web_search` | 关键词搜索，返回 title/url/description |
| `web_extract` | 多 URL 正文提取（含 crawl 模式） |

### 6.3 Provider（8 个）

| Provider | 搜索 | 抓取 | Key |
|----------|------|------|-----|
| ddgs | ✅ | ❌ | 无 |
| brave-free | ✅ | ❌ | BRAVE_SEARCH_API_KEY |
| searxng | ✅ | ❌ | SEARXNG_URL |
| tavily | ✅ | ✅ | TAVILY_API_KEY |
| exa | ✅ | ✅ | EXA_API_KEY |
| firecrawl | ✅ | ✅ | FIRECRAWL_API_KEY |
| parallel | ✅ | ✅ | PARALLEL_API_KEY |
| xai | ✅（Grok Server Tool） | ❌ | OAuth/XAI_API_KEY |

### 6.4 独立 search/extract 配置

```yaml
# ~/.hermes/config.yaml
web:
  search_backend: "searxng"
  extract_backend: "firecrawl"
```

优先级：`web.search_backend` → `web.backend` → 环境变量自动探测

---

## 7. OpenClaw

**仓库**：https://github.com/openclaw/openclaw  
**文档**：https://docs.openclaw.ai/tools/web

### 7.1 工具

| 工具 | 说明 |
|------|------|
| `web_search` | 13+ Provider 扩展 |
| `web_fetch` | 本地 Readability + 可选 Firecrawl |
| `x_search` | X/Twitter（xAI） |

### 7.2 核心文件

- `src/agents/tools/web-search.ts` → `src/web-search/runtime.ts`
- `src/agents/tools/web-fetch.ts`
- `extensions/{brave,duckduckgo,exa,firecrawl,google,xai,...}/`

### 7.3 特性

- **自动探测 Provider**：按 API Key 优先级 + 无 Key 回退 DDG/SearXNG
- **运行时 fallback 链**：非显式指定时依次尝试候选 Provider（`runWebSearch`）
- **search/fetch 独立配置**：`tools.web.search` / `tools.web.fetch`
- OpenAI Codex 可用原生 Responses `web_search`；Gemini/Grok/Kimi 为 LLM-grounded 搜索

### 7.4 web_fetch 本地路径

- SSRF 防护 + Readability + htmlToMarkdown
- 15min 缓存，默认 max 20K chars
- Firecrawl 插件可选

---

## 8. Anthropic API Server Tools（参考，Noesis 不可用）

| 工具 | 类型 | 定价（文档） |
|------|------|--------------|
| `web_search_20250305` / `20260209` | Server Tool | $10/千次 + token |
| `web_fetch_20250910` / `20260209` | Server Tool | 按 token |

- 搜索后端：公开信息指向 Brave
- `20260209` 版支持 dynamic filtering（需 code execution）
- Claude Code `WebSearch` 包装此 Server Tool；`WebFetch` 则为独立客户端实现

---

## 9. 横向对比

| 维度 | Claude Code | deer-flow | Hermes | OpenClaw | OpenCode |
|------|-------------|-----------|--------|----------|----------|
| 语言 | TS | Python | Python | TS | TS |
| 搜索执行 | Anthropic 云端 | 本地 SDK | 本地插件 | 本地插件 + 部分 LLM | Exa/Parallel MCP |
| 抓取执行 | 本地+Haiku | Jina/Tavily 等 | Provider 插件 | 本地+Firecrawl | 本地 turndown |
| Provider 数 | 0（黑盒） | 5~7 | 8 | 13+ | 2 |
| search/extract 分离 | N/A | 可独立选 use | ✅ | ✅ | N/A（双工具） |
| 无 Key 方案 | 不适用 | DDG | DDGS/SearXNG | DDG/SearXNG | 需 OpenCode Provider |
| Fallback 链 | 无 | 无 | 配置唯一 | ✅ 自动 | session hash |

---

## 10. Noesis 选型建议（已写入 design.md）

### 10.1 首版实现（2026-06-06 修订）

1. `agent/tools/web_search_tool.py` + `build_web_search_tools()`
2. Provider：**Tavily 主路径**（`tavily-python`）；无 `TAVILY_API_KEY` 或 Tavily 失败时回退：`web_search` → DDG，`web_fetch` → local
3. 参考 deer-flow `community/tavily/tools.py`
4. 挂载：`DeepResearchAgent` + `research-worker`
5. 更新 `deep-research-v2` skill

### 10.2 不采用

| 方案 | 原因 |
|------|------|
| Tavily MCP | 轻量同步调用，MCP 多一层进程 |
| Anthropic Server Tool | 绑定 Claude API |
| OpenCode MCP 搜索 | 依赖 Exa/Parallel 托管 MCP，与 Noesis 配置体系不一致 |
| OpenClaw 13 Provider | 首版过度工程；可后续借鉴 fallback 链 |

### 10.3 能力互补

| 能力 | 来源 |
|------|------|
| 关键词 → URL | 新建 `web_search` |
| 已知 URL 快速正文 | 新建 `web_fetch` |
| 复杂/反爬/YouTube | `baoyu-url-to-markdown` |
| 学术论文 | `execute + curl` OpenAlex |
| 企业知识库 | `search_knowledge_base`（COMMON_QA） |

---

## 11. 参考链接

| 项目 | URL |
|------|-----|
| deer-flow | https://github.com/bytedance/deer-flow |
| Hermes | https://github.com/NousResearch/hermes-agent |
| OpenClaw | https://github.com/openclaw/openclaw |
| OpenCode | https://github.com/anomalyco/opencode |
| Anthropic web_search | https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool |
| Anthropic web_fetch | https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool |
| Claude Code 还原 | `/Users/zzq/Desktop/code/cloud-code/claude-code-source/` |

---

## 12. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-06-06 | 初版：汇总 Noesis 缺口、五方实现、Noesis 选型；纳入 OpenSpec change `add-deep-research-web-tools` |
| 2026-06-06 | 修订：首版改为 Tavily 优先，无 Key 回退 DDG/local |
