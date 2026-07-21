## Context

Composer 已有 `ChatComposerToolbar`（`+` 菜单）按会话勾选 Models / Skills / MCP，并预拉 `getSkillsFsTree`。会话上下文面板经 `GET /api/chat/sessions/{session_id}/context` 暴露 workspace / uploads / skills 树。缺的是输入框内 Cursor 式 `/`、`@`：Web 无本机索引，必须靠「轻量 catalog API + 前端缓存过滤 + 结构化 mentions」，避免按键打满后端。

相关模块：`frontend/src/views/chat.vue`、`ChatComposerToolbar.vue`、`api/skills.ts`、`api/chat.ts`；`backend/schemas/qa_vo.py`、`services/qa_service.py`、`services/session_context_service.py`、`agent/super_agent.py`、`agent/fault_operation_agent.py`。

## Goals / Non-Goals

**Goals:**

- Composer 输入触发 `/`（Skills）与 `@`（文件/文件夹、subagent）picker，体感接近桌面端。
- Catalog 预取 + TTL 缓存 + 本地 fuzzy；选中后写入结构化 `mentions`，随本轮问答请求提交。
- 后端解析 mentions：skill → 约束/提示优先读对应 `SKILL.md`；file → 注入路径（必要时短文本）；subagent → 提示优先 `task` 委派该类型。
- 与 `+` 菜单并存；旧客户端不传 `mentions` 行为不变。

**Non-Goals:**

- `@Codebase` 语义检索 / 向量索引。
- 自定义 slash 脚本（`.cursor/commands` 任意 md 执行）。
- 按键远程搜索全量用户跨会话文件。
- 替换附件上传或右侧上下文面板。

## Decisions

### D1：不新增聚合 catalog API（首期）

- **选择**：并行复用 `GET /api/skills/fs/tree` + `GET /api/chat/sessions/{id}/context`；subagent 由前端按 `qa_type` 静态表（或后续从 prompt 配置导出的只读常量）提供。
- **理由**：`ChatComposerToolbar` 已预取 skills；context 树已服务面板；新聚合端点增加耦合，收益仅一次 RTT。
- **备选**：`GET .../mention-catalog` 一次返回。留作树过大时的优化，首期不做。

### D2：`mentions` 挂在问答请求与 user `extra`，不改 SSE 事件集

载荷形状（示意）：

```json
{
  "mentions": [
    { "type": "skill", "id": "deep-research-v2", "source": "platform" },
    { "type": "file", "path": "workspace/notes.md", "virtual_path": "/research/notes.md" },
    { "type": "folder", "path": "workspace/research", "virtual_path": "/research/" },
    { "type": "subagent", "id": "task-worker" }
  ]
}
```

- 进入 `QaQueryRequest`（或 stream 体等价字段）与 user 消息 `extra.mentions` 落库，便于历史回显 chip。
- **不**新增 SSE 事件；注入发生在 Agent 调用前（system/human 附加块或 `enabled_skills` 收窄）。

### D3：文件默认「引用优先」，不全文预取

- Picker 确认后只记 path / virtual_path。
- 后端注入短提示：「用户 @ 了 `{virtual_path}`，请先 `read_file`」。
- 仅当文件 ≤ 可配置阈值（建议对齐 context 读 API，如 512KiB 下限更小，如 32KiB）且为文本时，MAY 内联摘要；超限只引用。
- **理由**：对齐 Cursor「选中再读」；避免 Web 往返塞大文件拖慢首 token。

### D4：`/` skill 与会话 `enabled_skills` 的关系

- `/` 插入 skill mention = **本轮强提示**使用该 skill。
- 可选：同时把该 skill 并入本轮 `enabled_skills`（若当前为「全部启用」则不改会话持久化；若用户已收窄勾选，则本轮 union）。
- `+` 菜单仍管会话级默认启用集；二者互补。

### D5：触发与 UI

- 输入框：行首或空白后的 `/`、`@` 打开 picker；Esc / 点击外侧关闭；Enter 选中；继续输入本地过滤。
- Chip 展示在输入区上方或内嵌 token；发送后可从 `extra.mentions` 在气泡旁只读展示。
- 适用：优先 `SUPER_AGENT_QA`（skills + files + subagent）；`FAULT_OPERATION_QA`（files + subagent）；其它 `qa_type` 可仅 `@` 附件区/uploads 文件或隐藏 `/`。

### D6：缓存策略

| 源 | 预取时机 | TTL | 失效 |
|----|----------|-----|------|
| skills tree | 进入 SUPER 会话 / 首次 `/` | ≥ 60s | skills 上传/删除后手动 invalidate |
| context tree | 进入会话 / 首次 `@` / 面板刷新后 | ≥ 30s | SSE `finish` 后 debounce 刷新（与面板一致） |
| subagents | 模块常量 | 进程级 | `qa_type` 切换即换表 |

过滤：前端 fuzzy（名称、路径后缀）；**禁止** query 参数远程搜。

### D7：路径映射

`@` 候选来自 context 树 key（如 `users/{uid}/sessions/{sid}/workspace/...`）。注入时映射为 Agent 虚拟路径（`/research/...`、uploads 规则见 `agent-runtime-paths`）。映射逻辑集中在后端 Service（如 `MentionResolveService`），前端只传稳定 `path`/`key` + 可选 `virtual_path` 提示。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| context 树过大导致首拉慢 | 预取 + 缓存；必要时后续加扁平 path 索引 API |
| 用户以为 @file 已读入全文 | UI 标注「引用」；prompt 明确要求 Agent read |
| skill mention 与 enabled_skills 冲突 | 规范：mention 本轮强制并入；文档写清 |
| 错误 virtual_path 越权 | 后端校验 path 属于当前 session/user，拒绝穿越 |
| 与 `composer-session-tools` 并行开发冲突 | 共用 skills API；picker 独立组件，toolbar 不改核心 |

## Migration Plan

1. 后端先接受可选 `mentions` 并做校验/注入；无字段 = 旧行为。
2. 前端上线 picker + 缓存；feature 可按 `SUPER_AGENT_QA` 灰度。
3. 回滚：前端停发 `mentions` 即可；无需 DB migration。

## Open Questions

1. COMMON_QA / TEST_CASE_QA 是否首期开放 `@`（仅 uploads）？建议首期仅 SUPER + FAULT，其它隐藏。
2. `/` 是否扩展 MCP server 快捷开关？建议二期，首期仅 Skills。
3. 历史气泡是否可点击 mention 重新打开文件？建议只读 chip，不做跳转首期。
