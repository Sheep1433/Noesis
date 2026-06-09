## Context

Noesis 在 `backend/services/qa_service.py` 按 `qa_type` 调度多条 LangGraph/DeepAgents 流水线。本设计以 **`LANGFUSE_TRACING_ENABLED`** 控制是否在 `config` 上挂载 `CallbackHandler` 与 `langfuse_session_id`，不再单独实现 OTLP/多后端抽象。

## Goals / Non-Goals

**Goals:**

- 开关关闭时：**零** Langfuse 模块加载（延迟 import），`uv run app.py` 不依赖 Langfuse 服务。
- 开关开启时：LangGraph/LangChain 执行链在 Langfuse UI 中可观测（链路、工具、耗时等），会话维度用 `chat_id` 或项目内与前端会话一致的 id 写入 `metadata`（`langfuse_session_id`）。
- Langfuse 客户端初始化失败、上报失败或网络错误：**降级**为日志，不中断 SSE 与持久化主流程。

**Non-Goals:**

- 不实现 OTel Collector、Jaeger、Tempo、Phoenix 等第二套观测后端集成。
- 不在本变更中定义「与 Langfuse 解耦的追踪抽象层」。
- 不要求改造前端必显式消费 Langfuse；前端可选忽略 SSE 增量字段。

## Decisions

1. **观测后端：仅 Langfuse**  
   - 采用 Langfuse 标准集成模式；用户明确以效果为准、不考虑未来扩展。

2. **注入点：集中在 Agent 调用 `config`**  
   - 在 `BaseAgent` 或各 Agent 构建 `config = {"configurable": {...}}` 之后，若 `AppConfig`（或等价）中 tracing 开启，则 `config["callbacks"] = [CallbackHandler()]`，`config["metadata"] = {"langfuse_session_id": <session>, ...}`，可附加 `qa_type` 等于 Langfuse 支持的 tags/metadata 字段（需查 SDK 文档，避免非法键）。  
   - DeepAgents / `create_agent` 若使用同一 `config` 透传，应与 LangChain 文档一致。

3. **配置字段命名**  
   - 环境变量：`LANGFUSE_TRACING_ENABLED`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_BASE_URL`，在 `env.py` 中映射为布尔与字符串，**禁止**在仓库中提交真实密钥。

4. **敏感与合规**  
   - 仓库与示例 env 不含真实 key；生产密钥只经环境变量注入。追踪负载内容由 **Langfuse SDK 默认行为**收集，不在应用层再实现一套 Prompt 剥离，除非后续独立安全需求单独立项。

5. **与 SSE**  
   - 首版可仅在服务端 structured log 中打 `session_id` +「Langfuse 已启用」；若规格要求 SSE 可选字段，再在桥接层首包或 debug 级事件中附带 Langfuse observation/session 引用（实现时以 SDK 暴露能力为准）。

## Risks / Trade-offs

- **[Risk] Langfuse 与 LangGraph 小版本不兼容** → **Mitigation**：锁定 `langfuse` 与 `langchain*` 版本并 `uv run app.py` 验证。  
- **[Risk] 延迟 import 遗漏某条 Agent 路径** → **Mitigation**：以 `qa_type` 四条主路径 checklist 在 `tasks.md` 收口。  
- **[Trade-off] 绑定 Langfuse** → 可接受；用户明确放弃多后端扩展。

## Migration Plan

1. 增加依赖与 pydantic 配置字段，默认 `LANGFUSE_TRACING_ENABLED=false`。  
2. 合并后未配置 Langfuse 的开发者无操作成本。  
3. **Rollback**：关开关并重启；无需数据迁移。

## Open Questions

- Noesis 各 Agent 的 `RunnableConfig` 是否全部从单一路径传入（需在实现时核对 `CaseCoordinator` 等是否需单独挂 callback）。  
- `langfuse_session_id` 使用 `chat_id` 还是会话表主键，以实现与历史消息同一视图为准（实现阶段与 `chat_service` 字段对齐）。
