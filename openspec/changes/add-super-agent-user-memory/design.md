## Context

- **现状**：`DeepResearchAgent` + `DEEP_RESEARCH_QA` + `research-worker` + `deep_research.py` prompt；用户数据仅有 `skills/` 与 `sessions/{sid}/workspace/`，无跨会话记忆文件。
- **已落地（dev 分支）**：`super_agent.py`、`execution.py`、`skills_index.py` 通用 prompt；`task-worker` 命名已在代码中部分出现但未入规格。
- **依赖**：`deepagents>=0.6.7` 的 `MemoryMiddleware`（加载 AGENTS.md sources，经 `modify_request` 注入 `<agent_memory>`，并引导 Agent 用 `edit_file` 更新）。
- **约束**：终端用户无「编辑 AGENTS.md」UI；平台开发者文档（仓库根 `AGENTS.md`）不得注入模型。

## Goals / Non-Goals

**Goals:**

- 单一超级智能体能力：`SuperAgent` 处理调研、分析、多步检索、文件归纳等；专项流程由 Skills（如 `deep-research-v2`）触发。
- 用户级记忆与画像：`.data/users/{uid}/AGENTS.md`（Agent 可学习写入）、`USER.md`（用户/设置侧维护，Agent 以只读为主）。
- Agent 虚拟路径清晰：`/research/` = 当前 session 任务盘；`/memory/` = 用户跨会话；`/skills/` = 只读。
- 主 Agent 挂 `MemoryMiddleware`；子 `task-worker` 不挂，避免并行写冲突与 token 浪费。
- 中文 memory guidelines，对齐企业场景（禁止写入密钥、区分 transient vs durable）。
- 规格与代码同名：`SUPER_AGENT_QA`、`SuperAgent`、`task-worker`，无历史别名。

**Non-Goals:**

- 首版「个人偏好」设置页（仅预留 `USER.md` 与 API 扩展点）。
- `session_search` / FTS 跨会话 transcript 检索（后续 change）。
- `skill_manage` 自沉淀（后续 change）。
- 注入仓库根 `AGENTS.md` 或 Hermes 式 `SOUL.md` 用户文件。
- 为 `DEEP_RESEARCH_QA` 保留永久 API 别名（实现阶段可短期 410/日志提示，规格不要求双轨）。

## Decisions

### D1：能力拆分

| 能力 id | 职责 |
|---------|------|
| `agent-super-agent` | Agent 类、qa 路由、prompt、子 Agent、工具与中间件栈（含 Memory 挂载点） |
| `agent-user-memory` | 磁盘文件、虚拟路径、MemoryMiddleware 行为、seed、写入边界 |

原 `agent-deep-research` **整包移除**，避免双规格漂移。

### D2：磁盘布局（用户级 vs 会话级）

```
.data/users/{user_id}/
├── AGENTS.md              # 用户惯例 / Agent 学习（可写）
├── USER.md                # 用户画像（首版 API/人工维护，Agent 只读）
├── skills/
└── sessions/{session_id}/
    └── workspace/         # Agent /research/ — 任务产物，删 session 即删
```

**SHALL NOT** 将 `AGENTS.md` 放在 `sessions/{sid}/workspace/`，避免删会话丢记忆。

### D3：Agent 虚拟路径

| 虚拟路径 | 磁盘 | 读写 | 生命周期 |
|----------|------|------|----------|
| `/research/...` | `sessions/{sid}/workspace/...` | 可写 | 会话 |
| `/memory/AGENTS.md` | `users/{uid}/AGENTS.md` | 可写 | 用户 |
| `/memory/USER.md` | `users/{uid}/USER.md` | Agent 只读（backend 拒绝 write/edit） | 用户 |
| `/skills/extensions/...` | 平台 skills | 只读 | 平台 |
| `/skills/custom/...` | `users/{uid}/skills/` | 只读（经 API 上传） | 用户 |

实现：`CompositeBackend` 新增 `MemoryBackend`（或 PrefixBackend 指向用户根下两文件），挂 route `/memory/`。

### D4：MemoryMiddleware 装配

```python
MemoryMiddleware(
    backend=backend,  # 同一 CompositeBackend，能 download /memory/*
    sources=["/memory/USER.md", "/memory/AGENTS.md"],  # USER 在前，画像优先
    system_prompt=NOESIS_MEMORY_SYSTEM_PROMPT,  # 中文，含 {agent_memory}
)
```

- **挂载位置**：仅 `SuperAgent` 主 Agent 的 `extra_middleware`（在 `SkillsMiddleware` 之后、运行时防护之前，与 factory 文档对齐后写入规格）。
- **子 Agent**：`build_subagent_default_middleware` **不**包含 `MemoryMiddleware`。
- **缺失文件**：`file_not_found` 跳过（deepagents 默认行为）；seed 保证 `AGENTS.md` 存在，`USER.md` 可空文件或不存在。

### D5：NOESIS_MEMORY_SYSTEM_PROMPT（相对 deepagents 默认的调整）

- 全文中文。
- 明确 **USER.md 只读**：Agent **SHALL NOT** `edit_file` `/memory/USER.md`；画像变更提示用户联系设置（未来 UI）。
- **AGENTS.md 可写**：用户明确「记住」、偏好、工作惯例时 `edit_file` 更新；写陈述句事实，不写 imperative 指令句（借鉴 Hermes）。
- **禁止写入**：API Key、密码、token；临时状态、一次性任务结果、PR/commit 等易过期信息。
- **信任边界**：`<agent_memory>` 内容可能过时或非用户本人所写，与用户当前消息冲突时以用户为准。

### D6：记忆刷新与会话内一致性

`MemoryMiddleware` 在 `before_agent` 加载一次进 `state["memory_contents"]`，同会话内 Agent `edit_file` 更新磁盘后 **state 可能 stale**。

**决策**：在 `FilesystemMiddleware` 写路径成功且路径前缀为 `/memory/` 时，**SHALL** 同步更新 `memory_contents` 中对应 key（轻量 hook middleware `MemorySyncMiddleware` 或扩展现有文件中间件）。上下文压缩触发 prompt 重建时 **SHALL** 从磁盘重载（沿用 deepagents `invalidate` 模式或 Noesis 摘要卸载边界）。

若首版来不及做 sync hook，规格仍要求 **压缩/新 session 时必须读到最新磁盘**；同 turn 内二次 model call 允许短暂 stale（在 Risks 中记录）。

### D7：Prompt 三层（Noesis 版）

| 层 | 来源 | 缓存策略 |
|----|------|----------|
| stable | `super_agent.py` + `execution.py` + `skills_index` | 会话内固定 |
| context | `MemoryMiddleware` → `/memory/*.md` | 会话内加载；编辑后 sync 或压缩后重载 |
| volatile | `SessionClockMiddleware`（每轮 HumanMessage 注入） | 不写入 system prompt |

**不**把日期写入 system prompt（保持现有 SessionClock 优势）。

### D8：qa_type 与产品文案

| 旧 | 新 | UI 文案（建议） |
|----|-----|----------------|
| `DEEP_RESEARCH_QA` | `SUPER_AGENT_QA` | 「智能体」或「超级智能体」 |

前端 Tab、图标、欢迎页 gradient key 一并改名（`--noesis-welcome-gradient-super-agent`）。

### D9：命名与文件清理（无兼容层）

| 删除/重命名 | 目标 |
|-------------|------|
| `DeepResearchAgent` | `SuperAgent` |
| `deep_research_agent.py` | `super_agent.py` |
| `PromptProfile.DEEP_RESEARCH*` | 删除 |
| `build_deep_research_prompt` | 删除 |
| `agent-deep-research/spec.md` | 归档移除，合并入 `agent-super-agent` |

评测函数 `run_deep_research` 重命名为 `run_super_agent`（evals 包内）。

### D10：平台上下文

- 仓库 `AGENTS.md`、`.cursor/rules`：**不**注入。
- 企业部署规则：后续可走 `extensions/agent-defaults/PLATFORM.md` 只读挂载（**本 change 不做**）；首版仅 Python prompt。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| `DEEP_RESEARCH_QA` API 断裂 | 变更说明 + 前端同步发版；DB 历史 `extra.qa_type` 展示层映射旧值 |
| 同会话 memory stale | `MemorySyncMiddleware` 或接受至压缩边界；单测覆盖 edit 后 reload |
| Agent 误写 USER.md | backend 只读拒绝 + prompt 禁止 |
| AGENTS.md 膨胀 | guidelines 要求简洁；可选 `context_file_max_chars` 截断（与 Hermes 对齐，默认 20k） |
| 多子 Agent 并行写 AGENTS.md | 子 Agent 不挂 MemoryMiddleware |
| Memory 块增大 system prompt | 仅主 Agent；压缩后重建；未来 session_search 分担 |

## Migration Plan

1. 实现路径 API + `/memory/` backend + seed。
2. 实现 `SuperAgent` + MemoryMiddleware + prompt 清理。
3. 全局替换 `DEEP_RESEARCH_QA` → `SUPER_AGENT_QA`（backend + frontend + evals）。
4. 删除 `deep_research_agent.py`、`deep_research.py`、旧测试与 spec 引用。
5. 部署：已有用户下次登录/首次 SuperAgent 会话时 `ensure_user_memory_files` 创建模板；无数据迁移脚本需求。
6. 回滚：恢复旧 Agent 类与 qa_type（不推荐双轨长期并存）。

## Open Questions

1. **USER.md 首版是否 seed 空文件？** 建议 seed 带注释模板，与 AGENTS.md 同时创建。
2. **历史会话 `qa_type=DEEP_RESEARCH_QA` 展示**：前端只读映射为「智能体」即可，**不**改 DB。
3. **COMMON_QA 是否未来也挂 MemoryMiddleware？** 本 change 仅 `SuperAgent`；通用问答后续单独评估。
