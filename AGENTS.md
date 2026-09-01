# Noesis（智枢）开发指南

仓库级导航、跨端约定与协作规则。前后端细则见 [frontend/AGENTS.md](frontend/AGENTS.md)、[backend/AGENTS.md](backend/AGENTS.md)；上手与部署见 [README.md](README.md)。

## 文档分工

| 文件 | 内容 |
|------|------|
| [README.md](README.md) | 项目介绍、演示、快速开始 |
| **本文件** `AGENTS.md` | 仓库导航、跨端技术要点、协作与 Bug 流转（**唯一权威来源**） |
| [frontend/AGENTS.md](frontend/AGENTS.md) | 前端目录地图、命令、流式/UI 约定 |
| [backend/AGENTS.md](backend/AGENTS.md) | 后端分层规范、配置、Service/API 模板 |
| `docs/research/` | 项目现状与外部技术调研 |
| `docs/architecture/` | 当前长期架构与数据流 |
| `docs/engineering/` | 高难度实现与工程经验 |
| `docs/decisions/` | 决策记录：为什么这么定、否了什么、代价是什么 |
| `docs/bug/` | Bug 记录 |
| `docs/debugging/` | 疑难排查沉淀 |

## 仓库导航

```
Noesis/
├── frontend/          → frontend/AGENTS.md
├── backend/           → backend/AGENTS.md
├── extensions/        → Skills 包 + MCP 服务（见 extensions/README.md）
├── .noesis/           → 本地运行时数据（gitignore：Qdrant、checkpoint、附件、工作区、日志）
├── deploy/            → Docker Compose、镜像定义、生产配置
├── scripts/run.sh     # dev | prod | docker
├── openspec/          # 变更提案与规格
└── docs/              # PRD、Bug、调试笔记
```

| 区域 | 入口 |
|------|------|
| 容器部署 | `deploy/docker-compose.yml`、`deploy/backend/Dockerfile`、`deploy/frontend/Dockerfile` |
| 前端应用 | `frontend/src/main.ts`、`frontend/src/views/chat.vue` |
| 前端 SSE | `frontend/src/views/chat/useSSEStream.ts` |
| 后端启动 | `backend/app.py`、`backend/server/main.py` |
| 后端核心包 | `backend/packages/noesis-core/src/noesis/`（distribution：`noesis-core`） |
| 问答编排 | `backend/packages/noesis-core/src/noesis/services/qa/` |
| Agent 工厂 | `backend/packages/noesis-core/src/noesis/factory.py` |
| 场景入口 | `backend/packages/noesis-core/src/noesis/agents/`（Super / QA / 故障 / MCP / Case Generate） |
| SSE 桥接 | `backend/packages/noesis-core/src/noesis/chat/event_mapping/langgraph_bridge.py` |
| 配置 | `backend/packages/noesis-core/src/noesis/config/env.py` + `backend/config.yaml` |

## 跨端技术要点

### Agent 架构

| Agent | 实现 | 工具来源 | 场景 |
|-------|------|---------|------|
| GeneralQAAgent | `create_noesis_agent` | RAG hybrid 检索 | 智能问答 |
| FaultOperationAgent | `create_noesis_agent` | MCP | 故障运维 |
| SuperAgent | `create_noesis_agent` | 文件系统 + Skills + 子 Agent | 深度研究 / 通用复杂任务 |
| CaseCoordinator | LangGraph `StateGraph` | 自定义 workflow | 测试用例生成 |
| SimpleMCPAgent | `create_noesis_agent` | MCP | 本地调试 |

### 问答类型（`qa_type`）

`COMMON_QA`、`FAULT_OPERATION_QA`、`TEST_CASE_QA`、`SUPER_AGENT_QA`

### SSE 事件

run 内容流事件清单（message-start、reasoning/text/tool-input 系列、stats-update、hitl-required、finish、`[DONE]` 等）的**唯一权威**在 `docs/architecture/platform/chat-streaming.md` §4.2b，由契约测试钉住（漂移即 CI 红）。另有两条轻量信令流（hint 语义）：`session-signal`（`/sessions/{id}/events`，跨窗口发现活跃 run）与 `user-signal`（`/events/stream`，会话列表实时刷新），详见同文档 §4.2a。

**assistant 落库（服务端 authoritative，不依赖客户端收到 `[DONE]`）**：同一轮 SSE 对应 DB **一行**（`message_id` = `assistant_message_id`），经骨架 → 检查点 → 终态 UPDATE；终态互斥见 `openspec/specs/platform-chat/spec.md`「流式 assistant 消息 SHALL 按骨架—检查点—终态单次落库」与 `docs/architecture/platform/chat-streaming.md` §3.3。

### 认证

认证使用 Cookie Session + CSRF；路由 `meta.requiresAuth`；401 跳转登录。CSRF token 由 `GET /api/auth/session` 轮换，旧 token 保留一代有效（`prev_csrf_digest`，多窗口互不失效）。

## 开发验证

```bash
cd backend && uv run app.py     # 后端改动后必跑
cd frontend && pnpm lint        # 前端按影响范围 lint / build
python3 scripts/change-scope.py # 任何 diff 审查/选检查的起点（影响面 + 各层 owning checks）
```

- Python 统一 `uv run`，禁止裸 `python`（`scripts/` 下校验脚本例外，纯标准库）
- 测试目录：后端 `backend/tests/`（`api_contract/` = TestClient 级契约；`api/` = 真实服务级，`-m integration` 手动跑）；前端 `frontend/__tests__/`（vitest）与 `frontend/e2e/`（Playwright，`pnpm test:e2e`）
- 文档改动跑 `python3 scripts/verify-md-links.py` 与 `python3 scripts/verify-decision-format.py`（CI 同款 gate，本地先红先修）
- 每次测试完成后必须停止由 Agent 启动的后端、前端 dev/preview server 及临时测试进程，释放占用端口，避免与用户后续执行冲突
- 依赖链：`API → Service → Domain / Agent`；API 禁止直连数据库
- SSE、Agent、Qdrant、消息持久化相关改动优先补回归测试

### 后端硬性约定（摘要）

完整模板见 [backend/AGENTS.md](backend/AGENTS.md)：`ResponseUtil` 封装、禁止硬编码配置、禁止手写 JWT、统一 logger。

| 场景 | HTTP | 业务 code |
|------|------|-----------|
| 成功 | 200 | 200 |
| 不存在 | 404 | 404 |
| 冲突 | 409 | 409 |
| 未预期错误 | 500 | 500 |

外部服务（如 Qdrant）404 须单独处理，勿笼统捕获为 500。

## Git 分支流程

> **分支与合并规则以本节为准**；Agent / 开发者在改代码前须先确认当前分支符合下表。

### 分支职责

| 分支 | 用途 | 是否允许直接 commit |
|------|------|---------------------|
| `main` | 稳定发布分支，与线上/演示环境对齐 | **禁止**（仅接收自 `dev` 的合并） |
| `dev` | 日常集成分支：小修复、文档、依赖微调、已验收功能的合并入口 | **允许**（小改动） |
| `feat/*` | 大功能、跨模块重构、OpenSpec 变更等 | **允许**（大改动须在此开发） |

### 合并方向（单向，不得跳级）

```
feat/<name>  ──merge──▶  dev  ──merge──▶  main
```

| 场景 | 操作 |
|------|------|
| **大改动**（新 Agent、前端架构调整、多文件重构等） | 从最新 `dev` 拉 `feat/<name>` → 开发 & 自测 → 合并到 `dev` → 再合并 `dev` → `main` |
| **小改动**（单点 Bug、文案、配置、测试补齐等） | 直接在 `dev` commit → 合并 `dev` → `main` |
| **同步基线** | 开 `feat/*` 前先 `git checkout dev && git pull`，避免基于过旧提交 |

### 禁止事项

- **禁止**在 `main` 上直接开发或 commit（历史例外须尽快合回 `dev` 对齐）
- **禁止**`feat/*` 直接 push / merge 到 `main`（须经 `dev` 集成）
- **禁止**未经合并就把大段未提交改动长期留在 `main` 工作区
- **禁止**为开发 `feat/*` 在共享工作区直接 `git checkout` 切走分支——必须用 `git worktree add`（如 `git worktree add ../noesis-<feat> feat/<name>`）在独立目录开发，共享工作区保持用户当前分支（常为 `dev`）不被占用，否则会阻塞用户的联调测试
- 合并到 `main` 前：`backend` 跑 `uv run pytest tests/ -q`，`frontend` 按影响范围 `pnpm lint` / `pnpm build`

## 协作约定

> **角色职责与 Bug 状态流转以本节为准**；子目录 `AGENTS.md` 不重复本节内容。

### 角色职责

| 角色 | 职责 | 触发方式 |
|------|------|---------|
| 测试 | 发现 Bug、记录问题、维护状态 | 主动审查代码 |
| 开发 | 审查 Bug 是否属实、实现修复 | 仅当明确要求处理 Bug 清单时 |
| 产品 | 撰写需求、更新 PRD | 提出功能需求时 |

- 测试：问题记入 `docs/bug/`
- 开发：属实则修复并标「✅ 已修复」，不属实标「❌ 非 Bug」并说明原因；**默认不主动处理 Bug**
- 产品：可验收行为写 OpenSpec；关键调研、架构和工程设计按 `docs/README.md` 分类

### Bug 状态流转

```
🆕 新增 → 👀 待审查 → ⏳ 待修复 → ✅ 已修复
              ↓
           ❌ 非 Bug
```

| 状态 | 含义 | 执行角色 |
|------|------|---------|
| 🆕 新增 | 新发现的问题 | 测试 |
| 👀 待审查 | 待开发确认 | 开发 |
| ⏳ 待修复 | 已确认，待实现 | 开发 |
| ✅ 已修复 | 修复完成 | 开发 |
| ❌ 非 Bug | 确认非问题 | 开发 |
| 🗑️ 已删除 | 已清理的无效项 | 测试/开发 |

### 开发原则

- 先解决根因，再考虑容错；安全问题禁止吞异常或扩大权限绕过
- **禁止**多套方案并行（v2 / 备选）；废弃方案立即删除；遇到兼容方案的代码主动向用户提问是否要保留
- 方案变更同步更新对应 `docs/architecture/` 或 `docs/engineering/` 文档，单文件演进，不做版本对比
- 多次未解决的问题记录到 `docs/debugging/`（现象、根因、排查、方案）
- 高关注区：SSE 持久化、Qdrant 异常、配置硬编码、JWT/DB 默认密钥、MCP 远程执行
- **非平凡改动同提交附决策记录**（`docs/decisions/`，含被否方案）；实现 proposed 记录时将其改写为 implemented 并核实事实
- **审查经济学**（见 `code-review` skill）：CI/gate 已证明的属性不进 review 发现；blocker 与 suggestion 分离；收到 review 逐条技术性验证或反驳，禁止表演性认同
- **写作卫生**（见 `noesis-prose-hygiene` skill）：注释与文档以仓库当前状态为视角，不留「原先/被 review 否决/见讨论稿」类会话残留

### 代码质量 Skills

以下 skill 全部位于仓库 `.agents/skills/`，随仓库对任何 Agent 实例生效；**仓库 skill 禁止在用户级目录保留同名副本**（单一归属，冲突以仓库版本为准）。仓库只收开发纪律与工作流 skill，个人工具类留在用户级、不进版本管理。

| 场景 | 必须使用 | 约束 |
|------|----------|------|
| 修复 Bug、性能回退或偶发故障 | `diagnosing-bugs` | 先建立能捕获原始问题的稳定反馈，再定位根因；禁止先加 fallback、兼容分支或笼统 `try/except` |
| 功能或重构完成、准备提交或合并 | `code-review` | 同时检查项目规范与原始 spec；重点检查需求遗漏、范围扩大及 Fowler code smells；遵守审查经济学三条 |
| review 已确认存在冗余、浅 wrapper、无需求支撑的抽象或复杂控制流 | `code-simplification` | 仅修改本次范围；保持输入、输出、异常和副作用顺序不变；测试通过不是简化成立的唯一依据，还要证明更易理解 |
| 注释/文档写作或怀疑存在过期注释 | `noesis-prose-hygiene` | 会话视角残留审计，报告制执行（默认只报告不修改） |
| 写 spec / design / 决策记录 / 架构文档，或文档「难读 / AI 味重」 | `noesis-prose-standard` | 结论先行、现算态、表格只放可枚举事实、AI 味症状清单；探针只发现候选，判断靠语义 |

- 简单逻辑默认直接表达。只有概念需要命名、存在多个真实调用方、需要隔离变化或形成有效测试 seam 时才提取函数、类或接口。
- Bug 修复必须删除被新方案取代的补丁、临时日志和不可达分支，不保留“以后可能有用”的兼容实现。
- `code-simplification` 不自动扩大到无关文件，也不以减少行数为目标；无法说明原实现为何存在时先停止修改并查历史或调用方。
