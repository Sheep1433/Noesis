# Noesis（智枢）开发指南

仓库级导航、跨端约定与协作规则。前后端细则见 [frontend/AGENTS.md](frontend/AGENTS.md)、[backend/AGENTS.md](backend/AGENTS.md)；上手与部署见 [README.md](README.md)。

## 文档分工

| 文件 | 内容 |
|------|------|
| [README.md](README.md) | 项目介绍、演示、快速开始 |
| **本文件** `AGENTS.md` | 仓库导航、跨端技术要点、协作与 Bug 流转（**唯一权威来源**） |
| [frontend/AGENTS.md](frontend/AGENTS.md) | 前端目录地图、命令、流式/UI 约定 |
| [backend/AGENTS.md](backend/AGENTS.md) | 后端分层规范、配置、Service/API 模板 |
| `docs/prd/` | 产品需求 |
| `docs/bug/` | Bug 记录 |
| `docs/debugging/` | 疑难排查沉淀 |

## 仓库导航

```
Noesis/
├── frontend/          → frontend/AGENTS.md
├── backend/           → backend/AGENTS.md
├── scripts/run.sh     # dev | prod | docker
├── openspec/          # 变更提案与规格
└── docs/              # PRD、Bug、调试笔记
```

| 区域 | 入口 |
|------|------|
| 前端应用 | `frontend/src/main.ts`、`frontend/src/views/chat.vue` |
| 前端 SSE | `frontend/src/views/chat/useSSEStream.ts` |
| 后端启动 | `backend/app.py`、`backend/server.py` |
| 问答编排 | `backend/services/qa_service.py` |
| Agent 工厂 | `backend/agent/factory.py` |
| SSE 桥接 | `backend/utils/langgraph_sse_bridge.py` |
| 配置 | `backend/config/env.py` + `backend/config.yaml` |

## 跨端技术要点

### Agent 架构

| Agent | 实现 | 工具来源 | 场景 |
|-------|------|---------|------|
| GeneralQAAgent | `create_noesis_agent` | RAG hybrid 检索 | 智能问答 |
| FaultOperationAgent | `create_noesis_agent` | MCP | 故障运维 |
| DeepResearchAgent | `create_noesis_agent` | 文件系统 + Skills | 深度研究 |
| CaseCoordinator | LangGraph `StateGraph` | 自定义 workflow | 测试用例生成 |
| SimpleMCPAgent | `create_noesis_agent` | MCP | 本地调试 |

### 问答类型（`qa_type`）

`COMMON_QA`、`FAULT_OPERATION_QA`、`TEST_CASE_QA`、`DEEP_RESEARCH_QA`

### SSE 事件

`reasoning-start/delta/end`、`text-start/delta/end`、`tool-call-start`、`tool-output-available`、`token-details`、`error`、`finish-step`、`finish`、`[DONE]`

### 认证

Token 存 `sessionStorage`；路由 `meta.requiresAuth`；401 跳转登录。

## 开发验证

```bash
cd backend && uv run app.py     # 后端改动后必跑
cd frontend && pnpm lint        # 前端按影响范围 lint / build
```

- Python 统一 `uv run`，禁止裸 `python`
- 测试目录：`backend/tests/`、`frontend/tests/`
- 依赖链：`API → Service → Utils/Agent`；API 禁止直连数据库
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
- 产品：方案放 `docs/prd/`

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
- 方案变更同步更新 `docs/prd/`，单文件演进，不做版本对比
- 多次未解决的问题记录到 `docs/debugging/`（现象、根因、排查、方案）
- 高关注区：SSE 持久化、Qdrant 异常、配置硬编码、JWT/DB 默认密钥、MCP 远程执行

