## Purpose

本能力规定 Noesis 在后端通过 **Langfuse** 对 LangGraph/LangChain Agent 推理与工具链路进行可选追踪的验收标准：配置项与 Aix-DB 对齐、延迟加载 SDK、主业务流程在遥测失败时仍可完成，且不在仓库中要求记录生产密钥。

## ADDED Requirements

### Requirement: Langfuse 开关与配置

系统 SHALL 通过 `backend/config/env.py`（或等价全局配置）提供 `LANGFUSE_TRACING_ENABLED` 布尔开关及 `LANGFUSE_SECRET_KEY`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_BASE_URL` 字符串配置。当开关为关闭时，应用 SHALL NOT 在启动阶段导入 `langfuse` 包；当开关为开启时，应用 SHALL 能使用上述配置初始化 Langfuse 客户端以供 Callback 上报。

#### Scenario: 默认关闭启动

- **WHEN** `LANGFUSE_TRACING_ENABLED` 为关闭或未配置为真
- **THEN** 系统 SHALL 在无 Langfuse 服务、`uv run app.py` 仍可启动，且主路径与变更前行为一致

#### Scenario: 开启但 Langfuse 不可达

- **WHEN** 开关开启但 `LANGFUSE_BASE_URL` 不可达或上报失败
- **THEN** 系统 SHALL 完成流式问答与消息持久化主流程，不得因 Langfuse 抛出未处理异常导致 500；允许记录 warning 级别日志

### Requirement: LangChain/LangGraph 回调注入

当 `LANGFUSE_TRACING_ENABLED` 为真时，系统 SHALL 在调用 LangGraph 或 LangChain Agent 所使用的 `config`（含 `configurable`）上挂载 `langfuse.langchain.CallbackHandler`，并 SHALL 在 `metadata` 中设置 `langfuse_session_id`，其值与会话或 `chat_id` 等前端可复现的会话标识一致（具体字段名以实现与 `chat_service` 对齐为准）。

#### Scenario: 一次流式问答产生可查询链路

- **WHEN** 用户完成一次 `POST /api/chat/sessions/stream` 且开关开启、Langfuse 服务可用
- **THEN** Langfuse 控制台 SHALL 能检索到与该 `langfuse_session_id` 关联的追踪数据，且包含该次调用中的 LLM/工具相关事件（以 Langfuse 产品展示为准）

### Requirement: 密钥与仓库安全

系统 SHALL NOT 在仓库提交的源码、示例 env 或可合并的配置文件中包含真实的 `LANGFUSE_SECRET_KEY` / `LANGFUSE_PUBLIC_KEY` 生产值；文档 SHALL 仅使用占位符说明如何注入环境变量。

#### Scenario: 代码审查

- **WHEN** 审查者检查默认配置与文档示例
- **THEN** 不得发现可直连生产 Langfuse 项目的明文密钥

### Requirement: 部署文档

项目 SHALL 在 `README.md` 或 `docs/` 中说明如何自托管或接入 Langfuse（可引用官方 Docker 指引）、以及如何设置四项 `LANGFUSE_*` 变量以启用 Noesis 侧追踪；**无需**描述 OTel Collector、Jaeger、Tempo 等替代后端。

#### Scenario: 新部署按文档启用

- **WHEN** 运维已部署 Langfuse 并按文档设置环境变量
- **THEN** 启用开关后应能在 Langfuse UI 中看到来自 Noesis 的追踪数据
