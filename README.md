# Noesis（智枢）

Noesis 是一个基于 **Vue 3 + FastAPI + LangGraph** 的全栈 AI 对话平台，面向多场景智能体协作：通用问答、故障运维、深度研究与测试用例生成。前端通过 SSE 流式展示推理与工具调用过程；后端统一 Agent 运行时、知识库检索与消息持久化。

## 核心能力

| 场景 | 说明 |
|------|------|
| 智能问答 | 基于 RAG 的知识库检索与多轮对话 |
| 故障运维 | 通过 MCP 连接运维工具（读文件、执行命令、查日志等） |
| 深度研究 | 文件系统 + Skills 驱动的自主调研与报告产出 |
| 测试用例生成 | LangGraph 自定义工作流，分阶段生成与确认测试点/用例 |

## 功能演示

### 深度研究

Agent 自主加载 Skills、创建研究目录、联网检索，并通过 SSE 流式展示推理与工具调用过程。

![深度研究示例](assets/deep-research-example.png)

### 测试用例生成

基于需求文档分阶段生成测试场景与测试点，支持脑图可视化与人工勾选确认后批量产出用例。

![测试用例生成示例](assets/test-case-example.png)

## 技术栈

- **前端**：Vue 3、Vite、TypeScript、Naive UI
- **后端**：FastAPI、LangChain、LangGraph
- **存储**：MySQL（会话与消息）、Qdrant（向量检索）
- **模型**：DashScope / OpenAI 兼容接口；可选 Langfuse 观测

## 架构概览

```
┌──────────── 前端（Vue 3）────────────────────────┐
│  对话 · 知识库 · MCP 客户端 · Skills 管理        │
└────────────────────┬───────────────────────────┘
                     │ SSE / REST
┌────────────────────▼───────────────────────────┐
│  后端（FastAPI + LangGraph）                    │
│  通用问答 · 故障运维 · 深度研究 · 测试用例 Agent │
└────────────┬──────────────────┬────────────────┘
             │                  │
        MySQL（业务数据）    Qdrant（向量库）
```

## 快速开始

**前置条件**：Node.js 18+、pnpm 9.x、Python 3.11+（[uv](https://github.com/astral-sh/uv)）、Docker（用于 Qdrant / 推荐生产部署）。

```bash
# 克隆后，一键本地开发（Qdrant + 后端热重载 + 前端 :2048）
./scripts/run.sh dev
```

首次运行若缺少配置文件，脚本会从模板生成 `backend/.env` 与 `backend/config.yaml`，按提示修改后重新执行。

其他启动方式：

```bash
./scripts/run.sh help          # 查看 dev / prod / docker 说明
./scripts/run.sh prod          # 裸机生产形态验收
./scripts/run.sh docker        # Docker Compose（nginx + backend + qdrant）
START_MCP=1 ./scripts/run.sh dev   # 本地开发并启动 extensions/mcp/docker-ssh（故障运维）
```

仅启动单端时：

```bash
cd frontend && pnpm i && pnpm dev    # http://localhost:2048
cd backend && uv run app.py          # 验证后端能否拉起
```

## 文档索引

| 文档 | 内容 |
|------|------|
| **本文件** `README.md` | 项目介绍、演示、快速开始 |
| `AGENTS.md` | 仓库导航、跨端约定、协作与 Bug 流转 |
| `frontend/AGENTS.md` | 前端目录地图、命令、流式/UI 约定 |
| `backend/AGENTS.md` | 后端分层规范、配置、开发流程 |
| `frontend/README.md` | 前端安装与构建说明 |
| `scripts/run.sh help` | 部署模式、端口与环境变量 |
| `deploy/` | Docker Compose、镜像定义（`backend/`、`frontend/`、`mcp/`）、生产配置 |
| `docs/` | PRD、Bug 记录、调试笔记 |

## 可选：Langfuse 观测

设置以下环境变量即可启用 Agent 追踪（未配置时不影响正常运行）：

- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`
- `LANGFUSE_HOST`
- `LANGFUSE_ENABLED=true`

自托管部署可参考 [Langfuse 官方文档](https://langfuse.com/docs/deployment/self-host)。
