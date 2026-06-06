## 1. 配置与文档

- [x] 1.1 在 `backend/config/env.py` 增加流式 SSE 保活配置项（名称与 `Field(description=...)` 说明.env 键名；默认秒数与 PRD §6 一致，`0` 关闭），并接入 `GetConfig` 若项目已有统一出口
- [x] 1.2 更新 `docs/prd/platform/SSE流式数据设计.md` §6：将保活实现从「未编码」改为指向具体模块/配置项；补充部署检查表（Nginx `proxy_read_timeout` 与配置关系）

## 2. 保活与流式循环（`POST /api/chat/sessions/stream`）

- [x] 2.1 在 `backend/services/qa_service.py` 的 `exec_query` 主路径上，按 `design.md` D1 用 `asyncio.wait`/`FIRST_COMPLETED` 合并「下一上游事件」与 `sleep(间隔)`，在无业务输出超时点 `yield` SSE 注释行（`: keepalive` 或约定前缀 + `\n\n`）
- [x] 2.2 确保保活 `yield` 与现有 `bridge.process_item`/`finalize` 产出路径不交叉写同一缓冲状态；`CancelledError` 与 `task_cancelled` 分支仍能 partial 落库
- [x] 2.3 未知 `qa_type` 早退路径与异常路径同样不长时间无字节（若间隔内无帧则同样适用保活，或明确文档说明该短路径豁免）

## 3. 连接断开可观测性

- [x] 3.1 在 `backend/api/chat_api.py` 的 `_event_generator`（或经评审后唯一一层）对 `yield ...encode` 捕获 `BrokenPipeError`/`ConnectionResetError` 等，按 `design.md` D3 打 INFO/WARNING 并结束消费
- [x] 3.2 避免与 `exec_query` 内 `CancelledError` 日志重复；必要时收敛为单点记录

## 4. 测试与 TDD 记录

- [x] 4.1 在 `docs/test/test_tdd_design.md` 增加本变更测试点（保活帧出现、间隔关闭、连接类异常不记为未处理 exception）
- [x] 4.2 新增 pytest：对 `LangGraphSseBridge`（或 `langgraph_sse_bridge` 导出函数）做最小 golden / JSON 键断言，满足 delta spec「桥接层最小 golden」场景
- [x] 4.3 （可选）慢 mock：`async` 生成器故意延迟，断言在超过短间隔时响应序列中出现注释行（可挂 `@pytest.mark.asyncio`）

## 5. 验证

- [x] 5.1 执行 `uv run app.py` 确认进程可拉起
- [x] 5.2 若改动前端解析或类型：`pnpm lint` 受影响范围

## 6. 归档前

- [x] 6.1 已执行 `openspec archive sse-stream-reliablity -y`；delta 条款已合并入 `openspec/specs/chat-sessions-and-streaming/spec.md`（并移除误生成的 `chat-sessions-and-and-streaming` 目录）
