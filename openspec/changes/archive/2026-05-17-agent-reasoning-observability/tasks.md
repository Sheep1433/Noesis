## 1. 依赖与配置

- [x] 1.1 在 `backend/pyproject.toml`（或项目等价依赖文件）增加 `langfuse`，版本与现有 LangChain/LangGraph 栈兼容，`uv lock` / `uv sync` 可解析
- [x] 1.2 在 `backend/config/env.py` 增加 `LANGFUSE_TRACING_ENABLED`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_BASE_URL`，默认关闭；禁止在仓库中提交真实密钥默认值

## 2. Agent 回调（对齐 Aix-DB）

- [x] 2.1 在 `backend/agent/base/base_agent.py`（或统一工厂）实现：仅当开关为真时延迟 `from langfuse.langchain import CallbackHandler`，构造 `config["callbacks"]` 与 `config["metadata"]["langfuse_session_id"]`
- [x] 2.2 对 `GeneralQAAgent`、`FaultOperationAgent`、`DeepResearchAgent`、`CaseCoordinator`（及任何独立 `create_agent`/`compile().ainvoke` 路径）逐一确认 `config` 透传，避免遗漏 `qa_type` 主路径
- [x] 2.3 在 `backend/services/qa_service.py`（或等价调用点）传入与聊天会话一致的 id 作为 `langfuse_session_id`，与 `chat_service` 字段对齐并在代码注释中标明选用 `chat_id` 还是会话主键

## 3. 降级与回归

- [x] 3.1 Langfuse 导入失败、连接失败、回调异常：捕获并打 `warning`，不冒泡至 SSE 生成器导致断开
- [x] 3.2 `uv run app.py` 在开关关闭、开关开启但 Langfuse URL 无效两种情况下均可启动并完成一次流式对话（后者允许无 UI 数据但不得 500）
- [x] 3.3 必要时在 `test_tdd_design.md` 写测试点，`backend/tests/` 轻量测「关闭开关不 import langfuse」（可用 monkeypatch 或进程级约定）

## 4. SSE 与日志（可选）

- [x] 4.1 若向 SSE 透出可选引用键：仅开关开启时发送，且 `useSSEStream` / 解析逻辑忽略未知字段；否则仅结构化日志满足规格即可（本实现未改 SSE，满足于日志 + Langfuse UI）

## 5. 文档

- [x] 5.1 在 `README.md` 或 `docs/` 增加「Langfuse 追踪（可选）」：四项环境变量、自托管链接（与 Aix-DB / Langfuse 官方文档一致即可）、Docker 访问宿主的提示
- [x] 5.2 OpenSpec 归档时将本变更 `specs/agent-reasoning-observability/spec.md` 与 `specs/chat-sessions-and-streaming/spec.md` 合并入 `openspec/specs/` 对应主规格
