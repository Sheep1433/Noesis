## ADDED Requirements

### Requirement: 会话 context 树 SHALL 可作为 `@` 文件候选来源

系统 SHALL 允许 Composer `@` mention picker 复用 `GET /api/chat/sessions/{session_id}/context` 返回的 `tree`（及既有工作区只读文件 API）作为文件/文件夹候选数据源。右侧上下文面板的展示、刷新与编辑行为 SHALL 保持既有规格不变。

当面板因 SSE `finish` 或手动刷新更新树后，Composer 侧缓存 MUST 可失效并在下次打开 `@` 时使用新树（允许与面板共享同一前端缓存模块）。

#### Scenario: Picker 使用同一 context API

- **WHEN** 用户在会话中首次打开 `@` picker 且本地无有效缓存
- **THEN** 系统 SHALL 通过会话 context API（或共享缓存的一次请求）获取树，且候选文件路径与面板可见会话 workspace/uploads 范围一致

#### Scenario: 面板刷新后 picker 可见新文件

- **WHEN** 上下文面板刷新后出现新的 workspace 文件，且用户随后打开 `@` picker（缓存已失效或过期）
- **THEN** 候选列表 SHALL 能包含该新文件
