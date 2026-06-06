## 1. Web 工具模块

- [x] 1.1 新增 `backend/agent/tools/web_search_tool.py` 与 `web_providers/resolver.py`、`tavily.py`（主）、`ddg.py`（search 回退）、`local_fetch.py`（fetch 回退）。
- [x] 1.2 实现 `build_web_search_tools()`，导出至 `backend/agent/tools/__init__.py`。
- [x] 1.3 在 `backend/config/env.py` 增加 Web 工具配置段；同步 `config.example.yaml` / `.env.example` 字段说明（无真实密钥）。

## 2. DeepResearchAgent 接入

- [x] 2.1 `deep_research_agent.py`：主 Agent `tools=build_web_search_tools()`。
- [x] 2.2 `research-worker` 子 Agent 同步挂载相同 `web_tools`。
- [x] 2.3 更新 `agent/prompts/deep_research.py` 与 `backend/skills/deep-research-v2/SKILL.md` 工具表。

## 3. 依赖与安全

- [x] 3.1 `pyproject.toml` 添加 `tavily-python`（主）与 `ddgs`（回退）；实现 resolver 的 Tavily→DDG/local 回退与 `provider` 字段。
- [x] 3.2 `web_fetch` local Provider：URL 校验 + 私网段 SSRF 拒绝。
- [x] 3.3 Provider 异常统一为 JSON `error` 返回，不泄漏堆栈给 LLM。

## 4. 测试与验证

- [x] 4.1 `backend/tests/test_web_search_tool.py`：Tavily 有 Key / 缺 Key 回退 DDG、Tavily 失败回退、fetch 回退 local、`provider` 字段断言。
- [x] 4.2 `test_tdd_design.md` 补充 Web 工具测试点（仅测试点，无步骤）。
- [x] 4.3 `uv run app.py` 验证进程拉起；受影响路径 `pnpm lint` 若改前端则执行。

## 5. 规格归档准备

- [x] 5.1 实现完成后运行 `openspec validate add-deep-research-web-tools`。
- [ ] 5.2 归档时合并 delta 至 `openspec/specs/agent-web-tools/spec.md`（新建）与 `openspec/specs/agent-deep-research/spec.md`。
