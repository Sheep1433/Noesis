## Why

深度研究（`DeepResearchAgent`）与故障运维（`FaultOperationAgent`）曾共用全局 `backend/.agent_workspace`（故障运维为 `fault_ops` 子目录），多用户、多会话并行时 Agent 写入会互相覆盖。仓库已将本地运行时数据统一迁至 **`{REPO_ROOT}/.data/`**（`common/paths.py`），工作区隔离落在 **`.data/agent_workspace/`** 下，与 checkpoint、附件等同属隐藏数据目录。

本变更落地 **用户 + 会话** 双层路径隔离：`.data/agent_workspace/users/{user_id}/sessions/{session_id}/workspace/`。

## What Changes

- 新增 `config/agent_workspace_paths.py`，根路径固定为 `DATA_DIR / "agent_workspace"`（**不可 yaml/env 覆盖**）。
- `DeepResearchAgent` / `FaultOperationAgent` 按 `user_id` + `session_id` 构建 `LocalShellBackend`；Skills 仍只读挂载 `/skills/`。
- 缺 `session_id` 或 `current_user` 时拒绝可写 backend。
- 软删会话时 **始终** 删除对应 `.data/agent_workspace/users/.../sessions/{session_id}/` 子树（**无清理开关**）。
- 遗留 `backend/.agent_workspace` **不自动迁移**。

**非目标：**

- yaml / 环境变量配置工作区根路径或清理开关。
- 容器沙箱、用户级 `_user/` 目录、前端工作区面板。
- 修改 `.data/chat_attachments/` 布局。

## Capabilities

### New Capabilities

- `agent-workspace`：固定 `.data/agent_workspace/` 下的会话工作区路径与生命周期。

### Modified Capabilities

- `agent-deep-research`、`agent-fault-operation`：会话级 workspace 路径。
- `platform-chat`：软删会话同步清工作区。

## Impact

| 区域 | 影响 |
|------|------|
| `backend/common/paths.py` | `DATA_DIR` → `.data/` |
| `backend/config/agent_workspace_paths.py` | 路径解析与删除 |
| `backend/agent/deep_research_agent.py`、`fault_operation_agent.py` | 会话级 backend |
| `backend/services/chat_service.py` | 软删时 `delete_session_workspace` |
| `backend/AGENTS.md` | `.data/agent_workspace/` 说明 |
