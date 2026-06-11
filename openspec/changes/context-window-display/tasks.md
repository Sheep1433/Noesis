## 1. 配置

- [x] 1.1 在 `backend/config/yaml_config.py` 新增 `ContextYamlSection`（`max_input_tokens`、`display_enabled`），并更新 `config.example.yaml` / `config.prod.example.yaml`
- [x] 1.2 在 `backend/config/env.py` 的 `ModelSettings` 暴露 `context_max_input_tokens`、`context_display_enabled`，合并 yaml 与可选 env 覆盖
- [x] 1.3 修改 `SummarizationOffloadMiddleware._get_profile_limits()` 优先读 `context_max_input_tokens`；移除回退 `generation.max_tokens`；`summarization.max_input_tokens` 非 0 时打 deprecation 日志

## 2. 后端中间件与计数

- [x] 2.1 提取共享 `get_agent_token_counter()`（与摘要中间件同源），供上下文指标复用
- [x] 2.2 新增 `backend/agent/middlewares/context_metrics_middleware.py`：`before_model` 计数并经 `get_stream_writer()` 发出 `context-update` custom 事件
- [x] 2.3 在 `build_noesis_runtime_middleware()` 挂载 `ContextMetricsMiddleware`（SessionClock 之后、SummarizationOffload 之前）；`display_enabled=false` 时跳过 emit
- [x] 2.4 为 `ContextMetricsMiddleware` 补单测：计数、百分比取整、display 开关

## 3. SSE 桥接与持久化

- [x] 3.1 `LangGraphSseBridge` 处理 custom stream / `context-update`，输出标准 SSE 帧（含 `messageId`、`context` 字段）
- [x] 3.2 扩展 `backend/tests/test_langgraph_sse_bridge_contract.py` golden 断言 `context-update`
- [x] 3.3 `qa_service` 在流式 checkpoint 或回合结束将最新 `context` 写入会话 `extra.context`
- [x] 3.4 确认会话加载 API 将 `extra.context` 透传至前端（缺字段时补 VO/序列化）

## 4. 前端

- [x] 4.1 `useSSEStream.ts` 解析 `context-update`，新增 `onContextUpdate` 回调
- [x] 4.2 新增 `ContextWindowIndicator.vue`：环形弧 + 百分比；`n-tooltip` hover 显示 `{formatTokenCount(current)} / {formatTokenCount(max)}`
- [x] 4.3 `chat.vue` composer 下方接入指示器；维护 session 级 `context` ref；从 `extra.context` 初始化；`TEST_CASE_QA` 与无数据时隐藏
- [x] 4.4 按 `used_percentage` 应用颜色语义（<60 / 60–84 / ≥85）；确认与 assistant `usage` 行并存不冲突

## 5. 文档与验收

- [ ] 5.1 更新 `docs/prd/platform/SSE流式数据设计.md`（或等价 PRD）补充 `context-update` 字段说明
- [ ] 5.2 手动验收：`COMMON_QA` 长对话 + 工具大结果时百分比上升；hover 见 `87K / 128K`；刷新后会话指示器恢复
- [x] 5.3 运行 `uv run app.py` 与 `pnpm lint`（影响范围）确认无回归

## 6. 高风险检查

- [x] 6.1 确认新增 SSE 事件不破坏 `useSSEStream` 对未知 `type` 的忽略策略
- [x] 6.2 确认 `extra.context` 写入不覆盖会话 `extra` 其它键（merge 而非整包替换）
- [x] 6.3 确认 `context.display_enabled=false` 时零 DB 写入与零 SSE 开销
