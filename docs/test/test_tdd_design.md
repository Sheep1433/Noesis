# 测试与评测设计索引

本文件只记录跨模块测试入口。具体断言与 fixtures 以测试代码和 OpenSpec Scenario 为准。

## 后端回归

```bash
cd backend
uv run pytest tests/ -q
```

高关注区域：

- SSE 事件顺序、断连与终态持久化；
- assistant skeleton/checkpoint/terminal 单行更新；
- Qdrant 404、hybrid 检索和 collection scope；
- HITL pending/resume 与消息身份；
- harness 包边界和独立 import；
- auth domain ports、repository 事务和越权访问。

## 前端回归

```bash
cd frontend
pnpm lint
pnpm build
```

聊天相关改动优先覆盖：live SSE、历史 parts 归一化、停止生成、HITL、工具嵌套展示和会话切换。

## 离线评测

| 目录 | 目的 |
|------|------|
| `backend/evals/agent/` | Agent benchmark / Harbor / 记忆召回 / Agentic RAG |
| `backend/evals/case/` | 测试用例生成与 RAG 两阶段评测 |
| `backend/evals/compression/` | 摘要与上下文压缩评测 |
| `backend/evals/kb/` | 单集合 retrieval 评测 |

评测集怎么设计（压缩五维 probe、记忆召回断言、Agentic RAG dataset）见 [eval-set-design.md](eval-set-design.md)。

默认测试不得依赖外部模型。需要真实模型、Qdrant 或 Langfuse 的评测必须使用显式环境开关并输出可保存的结果。
