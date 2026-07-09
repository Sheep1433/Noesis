# OpenSpec 导航

主规格目录：`openspec/specs/<capability>/spec.md`。变更 delta 在 `openspec/changes/<name>/specs/`，归档后与主规格对齐。

**读 spec 时以 `openspec/specs/` 为主**；`changes/archive/` 为历史决策链。

## 能力目录（26 个有效 spec + 2 个合并占位）

| 域 | 能力 id | 一句话 |
|----|---------|--------|
| **平台聊天** | `platform-chat` | 会话、SSE、停止、LLM 工厂、chat 通用 UI |
| | `chat-session-attachments` | 会话附件 API、TTL、`file_dict` |
| | `chat-composer-send-upload` | 发送时上传（方案 B）前端行为 |
| | `chat-session-context-panel` | 上下文浏览 API + 右侧面板 |
| | `agent-reasoning-observability` | Langfuse 追踪（reasoning SSE 见 platform-chat） |
| **Agent 场景** | `agent-common-qa` | `COMMON_QA` / GeneralQAAgent |
| | `agent-fault-operation` | `FAULT_OPERATION_QA` / MCP |
| | `agent-test-case` | `TEST_CASE_QA` / CaseCoordinator |
| | `agent-super-agent` | `SUPER_AGENT_QA` / SuperAgent + Skills + 子 Agent |
| | `agent-user-memory` | 用户级 `AGENTS.md` / `USER.md` + `/memory/` |
| | `agent-web-tools` | web_search / web_fetch |
| | `agent-tool-failure-handling` | 工具错误 SSE 与 outcome |
| **运行时** | `agent-runtime-paths` | **权威** `.data/users/` 布局、删会话清理、迁移 |
| | `agent-sandbox` | AIO 沙箱、runner API |
| | `skills-filesystem` | Skills 只读挂载与用户 ZIP |
| | ~~`user-data-layout`~~ | → 已合并至 `agent-runtime-paths` |
| | ~~`agent-workspace`~~ | → 已合并至 `agent-runtime-paths` |
| **知识库** | `knowledge-base` | HTTP API、MySQL 集合配置 |
| | `kb-document-parse` | DeepDoc 解析、ParserFactory |
| | `kb-chunking` | DeepDocChunkAdapter、分块模板 |
| | `kb-retrieval` | hybrid + rerank 检索门面 |
| | `kb-evaluation` | 单集合评测 CLI（`evals/kb`） |
| **离线评测** | `test-case-agent-eval` | `evals.case` 两阶段 promptfoo |
| | `agent-offline-eval` | `evals.agent.*` benchmark |
| | `message-compression-eval` | `evals.compression` |
| **部署认证** | `container-deployment` | Docker Compose、Nginx、sandbox-runner |
| | `user-auth` | JWT 登录 |

## 活跃变更（`openspec/changes/`，非 archive）

| Change | 状态 | 说明 |
|--------|------|------|
| `kb-multimodal-retrieval` | **调研 / 设计** | 图片向量、跨模态召回（文搜图）；见 [research-report](./changes/kb-multimodal-retrieval/research-report.md) |
| `add-kb-citation-sources` | **规格完整，未开始** | KB 引用角标 + `citations-available` SSE |
| `extract-agent-runtime-harness` | **提案阶段** | `noesis_runtime` 与 Harness 拆分 |
| `refine-tool-outcome-handling` | **规格已入主 spec，实现未完成** | `tool_outcome.py` 与前端 ToolCallCollapse 待实现 |
| `fault-operation-agent-experience-learning` | **未开始** | 故障运维经验沉淀 |

已归档（2026-07-10）：`add-super-agent-user-memory`（`SuperAgent` + `SUPER_AGENT_QA` + 用户记忆 → 主 spec `agent-super-agent`、`agent-user-memory`）。

已归档（2026-07-09）：`enterprise-kb-retrieval-foundation`（知识库 RAG 底座 → 主 spec `knowledge-base` 等 5 项）。

已归档（2026-06-26）：`general-qa-file-upload`、`test-case-two-phase-eval`、`refactor-agent-eval-benchmarks`。

## 推荐阅读顺序

| 目标 | 先读 | 再读 |
|------|------|------|
| 聊天发消息、SSE | `platform-chat` Purpose + qa_type 表 | `agent-tool-failure-handling`（工具错误帧） |
| 上传文件 | `chat-composer-send-upload` | `chat-session-attachments` |
| 工作区 / 删会话磁盘 | `agent-runtime-paths` | `agent-sandbox` |
| 超级智能体 / 用户记忆 | `agent-super-agent` | `agent-user-memory` |
| 跑测试用例评测 | `test-case-agent-eval` | `backend/evals/case/README.md` |
| 跑 Agent benchmark | `agent-offline-eval` | `backend/evals/README.md` |
| 知识库入库/检索/调参 | [PRD 详细设计](../docs/prd/knowledge-base/知识库RAG底座详细设计.md) | `knowledge-base` → `kb-document-parse` → `kb-chunking` → `kb-retrieval` |
| 知识库多模态 / 文搜图 | [`kb-multimodal-retrieval` research-report](changes/kb-multimodal-retrieval/research-report.md) | 同 change 内 [design.md](changes/kb-multimodal-retrieval/design.md) |
| 单集合 KB 检索评测 | `kb-evaluation` | `evals/kb/`（与 `evals.case --phase rag` 互补） |

## qa_type 路由

| `qa_type` | Agent spec |
|-----------|------------|
| `COMMON_QA` | `agent-common-qa` |
| `FAULT_OPERATION_QA` | `agent-fault-operation` |
| `TEST_CASE_QA` | `agent-test-case` |
| `SUPER_AGENT_QA` | `agent-super-agent`（用户记忆见 `agent-user-memory`） |

## 与代码模块对齐

| OpenSpec 域 | 代码入口 |
|-------------|----------|
| 平台聊天 | `backend/domain/chat/`、`frontend/src/views/chat/` |
| Agent | `backend/agent/` |
| 运行时路径 | `backend/config/user_data_paths.py` |
| 评测 | `backend/evals/` |
| 知识库 | `backend/kb/`、`api/knowledge_base_api.py` |
| 部署 | `deploy/` |
