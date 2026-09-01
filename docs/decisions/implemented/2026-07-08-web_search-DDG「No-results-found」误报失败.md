# 决策：web_search DDG「No results found」误报失败

状态：implemented
日期：2026-07-08
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **现象**：日志 `DDG 搜索失败 … No results found.`，前端 `{"error":"搜索失败"}`；并非网络宕机，是 `ddgs` 在无命中时抛 `DDGSException`。
- **根因**：`mojeek,yandex` 对长中文 query（如定价表）常 0 条；原逻辑把该异常当基础设施失败。
- **修复**：`ddg.search_with_ddg` 将无结果视为 `total_results=0` 正常返回，并按 `配置 → mojeek → duckduckgo` 依次尝试；仅全链路超时/连接失败才返回 `error`。
- **建议**：国内稳定搜索配置 `TAVILY_API_KEY`；DDG 无结果时 Agent 仍可依赖图片 VLM 描述作答。
- **DDG 引擎实测（2026-07-08，本机网络）**：`ddgs` 文本源共 9 个——`mojeek` 3/4 命中（唯一天气失败）；`brave` 仅英文定价 1/4；`yandex/duckduckgo/google/startpage/wikipedia/yahoo/grokipedia` 基本超时或无结果。无百度/搜狗。默认改为 `ddg_backends: mojeek`，代码回退链 `mojeek → brave → duckduckgo`。
