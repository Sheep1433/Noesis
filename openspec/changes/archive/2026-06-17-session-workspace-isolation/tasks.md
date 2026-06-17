## 1. 路径模块

- [x] 1.1 新增 `backend/config/agent_workspace_paths.py`：`validate_segment`、`get_workspace_dir`、`ensure_workspace_dir`、`delete_session_workspace`
- [x] 1.2 根路径固定为 `common.paths.DATA_DIR / "agent_workspace"`（**不**增加 yaml 段）
- [x] 1.3 **不**新增环境变量或 `config/env.py` 覆盖项
- [x] 1.4 新增 `backend/tests/test_agent_workspace_paths.py`

## 2. DeepResearchAgent

- [x] 2.1 `_build_research_backend(user_id, session_id)` → 会话 `workspace/`
- [x] 2.2 缺 `session_id`/`user_id` 时 abort
- [x] 2.3 并行 session 写入测试
- [x] 2.4 `summary_offload` 落在会话 `workspace/summary_offload/`

## 3. FaultOperationAgent

- [x] 3.1 会话级 backend，移除 `fault_ops` 全局目录
- [x] 3.2 与深度研究对齐 `user_id` + `session_id`
- [x] 3.3 `test_fault_operation_workspace.py`

## 4. 会话删除联动

- [x] 4.1 `ChatService.delete_session` 软删后 **始终** 调用 `delete_session_workspace`
- [x] 4.2 `test_chat_session_workspace_cleanup.py`：删会话后目录不存在

## 5. 文档与收尾

- [x] 5.1 `backend/AGENTS.md` 注明 `.data/agent_workspace/`
- [x] 5.2 归档：已合并主规格并新增 `openspec/specs/agent-workspace/spec.md`
- [x] 5.3 pytest 与 `uv run app.py` 验证

## 6. 安全与回归

- [x] 6.1 路径穿越校验
- [x] 6.2 `QAService` 传入 `session_id` + `current_user`
- [x] 6.3 Skills 只读挂载未变
- [x] 6.4 附件目录未误删
