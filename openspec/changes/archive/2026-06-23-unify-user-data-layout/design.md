## Context

### 现状

| 数据类型 | 当前磁盘路径 | 问题 |
|----------|-------------|------|
| 会话产物 | `.data/agent_workspace/users/{uid}/sessions/{sid}/workspace/` | 与其它用户数据根分离 |
| 附件 | `.data/chat_attachments/sessions/{sid}/` | 路径无 `user_id`；合规删除需查 DB |
| 用户 Skills | `.data/user_skills/users/{uid}/` | UI 不展示；Agent 未挂载 |
| 平台 Skills | `extensions/skills` | 管理页只展示此项；上传 API 与用户目录脱节 |

对话页无右侧上下文面板；Skills 页文案仍暗示上传到全局根目录。

### 约束

- 平台 Skills **不迁入** `.data/users/`；继续随仓库/镜像部署。
- 路径段校验沿用 `[A-Za-z0-9_-]`（`validate_segment`）。
- API 层 `ResponseUtil`；鉴权 JWT + 会话归属校验。
- 不新增 SSE 事件类型；上下文面板通过 REST 轮询/手动刷新（流式结束时可触发刷新）。
- `CHAT_ATTACHMENT_*` 配置项保留语义，但磁盘根改为用户统一树下的会话子目录。

---

## Goals / Non-Goals

**Goals:**

- 单一用户数据根：`.data/users/{user_id}/`。
- 会话子树统一：`sessions/{session_id}/workspace|uploads|attachments`。
- 用户 Skills：`.data/users/{user_id}/skills/`；Agent 只读挂载 `/user-skills/`。
- Skills UI/API：合并平台 + 用户，节点带 `source`。
- 对话右侧：当前会话「产物 + 附件」只读面板。
- 提供迁移脚本与删会话整棵 `sessions/{sid}/` 清理。

**Non-Goals:**

- 右侧面板编辑/删除 workspace 文件。
- 跨会话产物归档、用户级全局文件浏览器。
- 知识库、checkpoint、Qdrant 路径调整。
- 将平台 Skills ZIP 上传到用户目录（用户上传仅进 `skills/`）。

---

## Decisions

### D1：统一目录布局

```
{REPO_ROOT}/.data/users/{user_id}/
├── skills/                              # 用户上传 Skills（跨会话）
└── sessions/{session_id}/
    ├── workspace/                       # Agent 可写产物
    ├── uploads/                         # 附件原文件
    └── attachments/                     # 解析 Markdown 副本
```

**模块**：新增 `config/user_data_paths.py` 为权威来源：

| 函数 | 返回 |
|------|------|
| `get_user_root(user_id)` | `.data/users/{uid}/` |
| `get_user_skills_dir(user_id)` | `.../skills/` |
| `get_session_root(user_id, session_id)` | `.../sessions/{sid}/` |
| `get_workspace_dir(user_id, session_id)` | `.../workspace/` |
| `get_session_uploads_dir(...)` | `.../uploads/` |
| `get_session_attachments_dir(...)` | `.../attachments/` |
| `delete_session_data(user_id, session_id)` | 删除 `sessions/{sid}/` 整树 |

`agent_workspace_paths.py`、`user_skills_paths.py` 改为薄封装委托，避免调用方大面积改动。

**备选（否决）**：保留三个独立根、仅文档对齐 → 无法解决沙箱 mount、合规删除与心智模型问题。

### D2：附件路径与 DB 元数据

- `ChatAttachmentService._session_dir(user_id, session_id)` → `get_session_root(...)`.
- `t_chat_attachment.original_path` / `markdown_path` 仍存**相对 `DATA_DIR/users/{uid}/sessions/{sid}/` 的路径**（如 `uploads/foo.pdf`），迁移时批量更新前缀。
- `CHAT_ATTACHMENT_DIR` yaml：**废弃独立根**；若仍配置非空，启动时打 warning 并忽略（或仅作迁移源路径别名，见 D6）。

虚拟路径（Agent 工具）保持 `/sessions/{session_id}/uploads/...` 不变，避免改 Middleware 提示词。

### D3：Skills 双源与 Agent 挂载

**API 契约**（扩展 `SkillFsTreeNode`）：

```json
{
  "key": "user:my-skill/SKILL.md",
  "label": "SKILL.md",
  "source": "user",
  "isLeaf": true
}
```

平台节点 `source=platform`，`key` 前缀 `platform:`；用户节点 `source=user`，`key` 前缀 `user:`。

| 操作 | 行为 |
|------|------|
| `GET /api/skills/fs/tree` | 返回 `{ platform: {...}, user: {...} }` 或合并树 + `source` 字段 |
| `GET /api/skills/fs/file?path=...&source=platform\|user` | 按源读文件 |
| `POST /api/skills/fs/upload-zip` | 仅写入 `.data/users/{uid}/skills/` |

**Agent CompositeBackend**（深度研究、故障运维等使用 Skills 的场景）：

```python
CompositeBackend(
    default=workspace_backend,
    routes={
        "/skills/": platform_skills_backend,       # extensions/skills
        "/user-skills/": user_skills_backend,     # .data/users/{uid}/skills/
    },
)
SkillsMiddleware(sources=["/skills/", "/user-skills/"])
```

平台路径前缀保持 `/skills/` 以兼容现有 skill 包内引用；用户技能用 `/user-skills/` 避免与平台同名冲突。

### D4：会话上下文面板 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/chat/sessions/{session_id}/context` | 聚合：`workspace` 树 + `attachments` 列表（未过期） |
| GET | `/api/chat/sessions/{session_id}/workspace/file?path=` | 读 workspace 下文本（≤512KB，与 Skills 读限制一致） |
| GET | 已有 `.../attachments`、`.../artifacts/...` | 附件预览复用 |

鉴权：JWT + 会话归属；404 对外统一「不存在」。

**前端**（`chat.vue`）：

- `n-layout` 右侧 `n-layout-sider`（可折叠，默认宽 320px）。
- Tab「产物」：`NTree` + `NCode` 预览；Tab「附件」：列表 + 点击预览/下载。
- `session_id` 变化时重新拉取；SSE `finish` 事件后 debounce 刷新产物树。
- `COMMON_QA` 无 workspace 时产物 Tab 显示空态；附件 Tab 仍可用。

**备选（否决）**：SSE 推送文件树增量 → 复杂度高，首版 REST 足够。

### D5：删会话与生命周期

`ChatService` 软删会话后调用 `delete_session_data(user_id, session_id)`，删除 `sessions/{sid}/`（含 workspace + uploads + attachments）。

用户 Skills **不**随删会话删除。

TTL 清理附件：扫描路径改为 `.data/users/*/sessions/*/uploads` 或通过 DB `expires_at` + 新相对路径删除（保持现有 lazy delete 策略）。

### D6：迁移策略

脚本 `scripts/migrate_user_data_layout.py`（`uv run`）：

1. `.data/agent_workspace/users/{uid}/sessions/{sid}/` → `.data/users/{uid}/sessions/{sid}/workspace/`（若目标已存在则跳过并 log）。
2. `.data/chat_attachments/sessions/{sid}/` → 根据 `t_chat_attachment.user_id` 迁到 `.data/users/{uid}/sessions/{sid}/`。
3. `.data/user_skills/users/{uid}/` → `.data/users/{uid}/skills/`。
4. 更新 `t_chat_attachment` 中 `original_path`、`markdown_path` 前缀。
5. `--dry-run` 与 `--user-id` 过滤。

**不回滚**旧目录自动删除；迁移成功后运维手动归档旧目录。

新部署空库：直接写新路径，无需迁移。

---

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| 升级后旧附件路径失效 | 发布说明要求跑迁移；启动时检测遗留目录打 warning |
| `CHAT_ATTACHMENT_DIR` 自定义部署路径 | 迁移脚本支持 `--legacy-attachment-root`；下个大版本移除配置 |
| 平台与用户 skill 同名 | UI 分开展示 `source`；Agent 路径前缀 `/skills/` vs `/user-skills/` 隔离 |
| 右侧面板频繁刷新性能 | 仅在 `finish` 与用户点击刷新时拉树；目录深时限制深度或分页（首版全量小目录） |
| 与 `add-agent-sandbox-isolation` 冲突 | sandbox change 若合并，AIO mount 改为 `users/{uid}/` 单盘，与本设计一致 |

---

## Migration Plan

1. 合并本 change 代码至 `dev`。
2. 停写流量或维护窗口执行 `uv run scripts/migrate_user_data_layout.py`。
3. 抽检：随机会话附件可读、深度研究 workspace 文件存在、Skills 页双源展示。
4. 确认无进程写旧路径后，可选删除 `.data/agent_workspace`、`.data/chat_attachments`、`.data/user_skills` 遗留空壳。

**Rollback**：保留旧目录备份；回滚代码版本；DB 路径字段需从备份恢复或反向脚本（首版不自动化反向）。

---

## Open Questions

1. Skills 合并树 UI：左右分栏（平台 | 我的）还是单树用图标区分？**建议**：单树顶层两个根节点「平台技能」「我的技能」。
2. 故障运维 Agent 是否首版挂载 `/user-skills/`？**建议**：与深度研究一致，凡使用 `SkillsMiddleware` 的场景统一挂载。
3. 产物面板是否对 `COMMON_QA` 默认折叠？**建议**：无 workspace 文件时侧栏默认收起，有产物时自动展开（可后续迭代）。
