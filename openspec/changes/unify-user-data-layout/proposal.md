## Why

Noesis 用户数据目前散落在三个根目录：`.data/agent_workspace/users/...`（会话产物）、`.data/chat_attachments/sessions/...`（附件，路径无 `user_id`）、`.data/user_skills/users/...`（用户 Skills）。Skills 管理页只展示平台公共目录 `extensions/skills`，与用户上传目录脱节，Agent 也未挂载用户 Skills。用户难以在对话中感知「本次会话产生了什么」，产品与 Cursor 类「右侧上下文」体验差距明显。

本变更将**用户级运行时数据**统一到 `.data/users/{user_id}/` 下，Skills 界面展示**平台 + 用户**两套可用技能并区分来源，对话页新增**当前会话上下文产物**右侧面板。

## What Changes

- 新增集中式用户数据路径模块：`DATA_DIR / "users" / {user_id} / ...`（**BREAKING**：附件与工作区磁盘布局变更）。
- 用户 Skills 迁至 `.data/users/{user_id}/skills/`；平台 Skills 仍位于 `extensions/skills`，**不迁入**用户目录。
- 会话附件迁至 `.data/users/{user_id}/sessions/{session_id}/uploads|attachments/`。
- 会话工作区迁至 `.data/users/{user_id}/sessions/{session_id}/workspace/`（取代 `.data/agent_workspace/...`）。
- Agent `CompositeBackend` 只读挂载：平台 `/skills/` + 用户 `/user-skills/`（或等价路由）。
- Skills API/UI：合并展示当前用户可用技能，节点标注 `source=platform|user`；上传 ZIP 写入用户目录。
- 对话页右侧 **会话上下文面板**：Tab「产物」（workspace 树 + 预览）与「附件」（本会话上传列表）；随 `session_id` 切换。
- 提供一次性迁移脚本：旧路径 → 新路径（含 `t_chat_attachment.original_path` 相对路径更新策略）。
- 软删会话时删除 `.data/users/{uid}/sessions/{sid}/` 整棵子树（含 workspace + attachments）。

**非目标：**

- 将 `extensions/skills` 迁入 `.data/` 或按用户复制平台 Skills。
- 附件/Skils 跨会话共享、用户级「全历史产物库」汇总页。
- 右侧面板内联编辑 workspace 文件（首版只读浏览 + 下载）。
- 修改知识库（Qdrant）存储布局。

## Capabilities

### New Capabilities

- `user-data-layout`：`.data/users/{user_id}/` 统一路径解析、校验、会话子树删除与迁移约定。
- `chat-session-context-panel`：对话页右侧当前会话上下文产物面板（workspace + 附件只读浏览 API 与前端）。

### Modified Capabilities

- `agent-workspace`：工作区根从 `.data/agent_workspace/` 改为 `.data/users/{user_id}/sessions/{session_id}/workspace/`。
- `chat-session-attachments`：磁盘路径纳入用户目录；`CHAT_ATTACHMENT_DIR` 语义调整或废弃为兼容别名。
- `skills-filesystem`：API 返回平台+用户合并树；区分 `source`；Agent 可读用户 Skills。
- `platform-chat`：新增会话上下文浏览 API；删会话清理路径对齐新布局。
- `agent-deep-research`：`CompositeBackend` 增加用户 Skills 只读路由。
- `agent-fault-operation`：工作区路径对齐 `user-data-layout`（与 deep-research 一致）。

## Impact

| 区域 | 影响 |
|------|------|
| `backend/config/user_data_paths.py`（新） | 统一路径 API，取代分散的 workspace/attachment/user_skills 根 |
| `backend/config/agent_workspace_paths.py` | 委托至 `user_data_paths` 或薄封装 |
| `backend/config/user_skills_paths.py` | 路径改为 `.data/users/{uid}/skills/` |
| `backend/services/chat_attachment_service.py` | `_session_dir` 含 `user_id` |
| `backend/services/skill_fs_service.py` | 双源扫描、合并树、`source` 元数据 |
| `backend/api/skill_api.py` | 树/文件/上传契约扩展 |
| `backend/api/chat_api.py`（或新 router） | `GET .../context/tree`、`GET .../workspace/file` |
| `backend/agent/deep_research_agent.py`、`fault_operation_agent.py` | CompositeBackend 路由 |
| `frontend/src/views/chat.vue` | 右侧上下文面板 |
| `frontend/src/views/skills/SkillsManagement.vue` | 平台/用户分区展示 |
| `frontend/src/api/skills.ts`、`api/chat.ts` | 新契约 |
| `scripts/migrate_user_data_layout.py`（新） | 旧目录迁移 |
| `openspec/specs/*` | 归档时合并 delta |
| **部署** | 升级后须执行迁移脚本；**BREAKING** 旧磁盘路径不再写入 |
