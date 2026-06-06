## Context

- 流式入口：`POST /api/chat/sessions/stream` → `chat_api._event_generator` → `QaService.exec_query` → 各 Agent `run_agent` 异步生成器 → `LangGraphSseBridge.process_item` / `finalize` 产出 SSE 字符串。
- 现状：`exec_query` 在 `async for item in agent_generator` 上阻塞时，可能长时间无任何 `yield`，中间代理按空闲断开；`CancelledError` 分支已有 partial 落库；前端 `useSSEStream` 已具备 `\n\n` 分帧与尾缓冲 flush。
- 约束：不引入 `langgraph-sdk`、不改变业务 `event:`/`data:` JSON  schema；配置走 `pydantic-settings` + `.env`。

## Goals / Non-Goals

**Goals:**

- 在两次业务 SSE 帧间隔超过配置阈值时，服务端自动发送 **SSE 注释行**（`: …\n\n`），降低代理静默断连概率。
- 提供可关闭/可调的保活间隔配置项，并在文档中写清与 Nginx `proxy_read_timeout` 的关系。
- 对向已断开客户端 `yield` 时常见的连接类错误做 **可预期日志级别**（INFO/WARNING），与业务异常区分。
- 增加最小契约测试，锁定关键帧格式。

**Non-Goals:**

- 不实现 SSE `id:` / `Last-Event-ID` 断线续传（deer-flow 能力，与当前 `fetch` 客户端不匹配）。
- 不把 Noesis 业务帧改为 deer-flow 的 LangGraph Platform 多 mode 事件名。
- 不替换 WebSocket；不改动 stop 接口语义路径。

## Decisions

### D1：保活与业务流合并于同一协程循环（优先）

**做法**：用 `asyncio.wait(..., return_when=FIRST_COMPLETED)` 在「等待下一帧 `agent_generator.__anext__()`」与「固定间隔 `asyncio.sleep(keepalive_interval)`」之间复用循环；超时时若尚未收到业务帧则 `yield` 注释行，再继续等待。**同一 `__anext__` 任务在保活触发时不取消**，仅取消未触发的 `sleep` 任务，避免向 Agent 异步生成器注入 `CancelledError` 导致尾包丢失。

**理由**：单协程、无额外线程/守护任务，取消语义与现有 `exec_query` 的 `CancelledError` 一致；避免独立任务与 `async for` 争用 `bridge`/`builder` 状态。

**备选**：独立 `asyncio.create_task` 定时 `yield` 保活——需严格锁与取消，复杂度高，仅当 D1 与某 Agent 内部阻塞模型冲突时再评估。

### D2：保活配置挂在独立或扩展现有 Settings

**做法**：在 `backend/config/env.py` 增加例如 `StreamSettings` 或并入 `AppSettings`：`sse_keepalive_interval_seconds: float`（`0` 表示关闭；默认建议 `25` 与 PRD §6 对齐）。

**理由**：符合仓库「禁止硬编码」；运维可按环境调小/调大。

### D3：连接类错误处理位置

**做法**：优先在 `chat_api._event_generator` 内对 `yield sse_str.encode(...)` 包一层 `try/except`，捕获 `BrokenPipeError`、`ConnectionResetError` 及 OSError 子类中与 reset 相关的 errno；记录 INFO 后 `return` 停止消费生成器。

**理由**：断开发生在 HTTP 写路径时此处最先感知；`exec_query` 内也可二次防御，但避免重复日志需约定只在一层打。

### D4：契约测试范围

**做法**：pytest 直接调用 `LangGraphSseBridge.process_item` / `_format_sse` 或对 `format_done` 拼接结果做快照；不强制启动全 LLM。

**理由**：与 holmesgpt / deer-flow 的帧形状测试同阶，CI 稳定。

## Risks / Trade-offs

- **[Risk] 保活间隔过短** → 流量与日志噪声略增 → Mitigation：默认 25s，可配置；注释行不写业务日志。
- **[Risk] `wait` 与部分 Agent 生成器兼容性** → 若某 `run_agent` 非标准 async gen → Mitigation：仅包装 `exec_query` 主循环；单测覆盖 COMMON_QA 路径 mock。
- **[Risk] 双处捕获连接异常导致重复日志** → Mitigation：只选 `_event_generator` 或 `exec_query` 之一打连接类日志（见 D3）。

## Migration Plan

1. 部署：发布前检查 Nginx `proxy_read_timeout` ≥ 预期最长「无业务帧」间隔；若小于 LLM 工具耗时，调大或依赖保活。
2. 配置：在 `.env` / 部署模板中增加 `sse_keepalive_interval_seconds`（名称以最终实现为准）。
3. 回滚：将间隔设为 `0` 关闭保活，行为与当前主干一致。

## Open Questions

- 是否所有 `qa_type` 共用同一保活间隔，或故障运维/深度研究需更长默认（可后续再拆配置，首版单一字段即可）。
