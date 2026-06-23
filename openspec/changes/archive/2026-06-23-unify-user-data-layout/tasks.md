## 1. 用户数据路径模块

- [x] 1.1 新增 `backend/config/user_data_paths.py`：`get_user_root`、`get_user_skills_dir`、`get_session_root`、`get_workspace_dir`、`get_session_uploads_dir`、`get_session_attachments_dir`、`delete_session_data`
- [x] 1.2 `agent_workspace_paths.py`、`user_skills_paths.py` 改为委托 `user_data_paths`（保持旧 import 兼容）
- [x] 1.3 更新 `backend/AGENTS.md` 与根 `AGENTS.md` 中 `.data/` 目录表

## 2. 附件与会话清理迁移

- [x] 2.1 `ChatAttachmentService`：`_session_dir(user_id, session_id)` 使用新路径；更新 `_rel_path` / artifact URL 逻辑
- [x] 2.2 `ChatService` 删会话/批量删：调用 `delete_session_data` 替代仅删 workspace
- [x] 2.3 ~~`CHAT_ATTACHMENT_DIR` 废弃 warning~~（无历史用户，不保留迁移逻辑）
- [x] ~~2.4 迁移脚本~~（已删除，绿场部署直接用 `.data/users/`）
- [x] 2.5 单测：附件读写新路径、删会话清 `sessions/{sid}/` 子树

## 3. Skills 双源 API 与 Agent 挂载

- [x] 3.1 扩展 `skill_vo`：`source` 字段；树响应支持 platform/user 分组
- [x] 3.2 `SkillFsService`：双源扫描；`read_file(rel, source)`；上传仅 `user` 目录
- [x] 3.3 `skill_api.py`：`/fs/tree`、`/fs/file?source=`、`/fs/upload-zip` 契约对齐规格
- [x] 3.4 `DeepResearchAgent`：`CompositeBackend` 增加 `/user-skills/`；`SkillsMiddleware` 双 source
- [x] 3.5 `frontend/src/views/skills/SkillsManagement.vue`：平台/我的分区展示；刷新后可见用户上传
- [x] 3.6 `frontend/src/api/skills.ts`：适配 `source` 参数与树结构
- [x] 3.7 单测：用户 skill 上传后 Agent 可读 `/user-skills/`（mock backend）

## 4. 会话上下文 API

- [x] 4.1 新增 `SessionContextService`（或扩展现有 Service）：workspace 树扫描 + 附件列表聚合
- [x] 4.2 `chat_api.py`：`GET .../context`、`GET .../workspace/file`；鉴权与路径穿越防护
- [x] 4.3 单测：越权 404、workspace 文件读取上限

## 5. 对话页右侧上下文面板

- [x] 5.1 新增 `SessionContextPanel.vue`（或内联于 `chat.vue`）：产物 Tab + 附件 Tab
- [x] 5.2 `frontend/src/api/chat.ts`：`getSessionContext`、`getWorkspaceFile`
- [x] 5.3 `chat.vue`：右侧 `n-layout-sider` 集成；`session_id` 切换与 SSE `finish` 后刷新
- [x] 5.4 空态：COMMON_QA 无 workspace 时产物 Tab 提示；附件 Tab 正常

## 6. Agent 工作区路径对齐

- [x] 6.1 `fault_operation_agent.py`：经新 `ensure_workspace_dir` 写入 `.data/users/...`
- [x] 6.2 更新 eval/offline runner 中 `workspace_path` 断言（若有硬编码旧路径）
- [x] 6.3 回归：`DeepResearchAgent` / `FaultOperationAgent` 集成测试路径

## 7. 验证与部署

- [x] 7.1 `uv run pytest tests/ -q`（附件、skills、workspace 相关用例）
- [x] 7.2 `uv run app.py` 启动无报错
- [x] 7.3 `pnpm lint`（`chat.vue`、`SkillsManagement.vue`、新组件）
- [x] 7.4 ~~文档：迁移脚本~~（无历史数据，不需要）
