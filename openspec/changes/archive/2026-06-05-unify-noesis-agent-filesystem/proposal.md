# Proposal: 统一 create_noesis_agent，文件系统依赖 deepagents

## Why

故障运维、深度研究、通用问答三套 Agent 创建路径需统一；文件系统与 Skills **复用 PyPI `deepagents`**，不在仓库内 vendoring。

## What

- 所有非 case_generate 的 ReAct Agent 统一经 `create_noesis_agent` 创建
- 文件系统：`deepagents` 的 `FilesystemMiddleware` / `SkillsMiddleware` + `CompositeBackend` / `LocalShellBackend`
- 深度研究：工作区 + `/skills/` 只读路由
- 故障运维：MCP 工具 + `create_noesis_agent`（MCP URL 写死在 Agent 模块内）
- 用例生成（`case_generate`）保持 LangGraph StateGraph，不在本次范围

## Impact

- `backend/agent/factory.py`、各 Agent 模块
- 依赖：`deepagents>=0.5.0`（移除 vendored `agent/filesystem/`）
