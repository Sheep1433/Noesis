## 1. 依赖与 LLM 工厂

- [x] 1.1 `pyproject.toml` / lockfile 增加 `langchain-deepseek`，`uv sync`
- [x] 1.2 `backend/core/llm_util.py`：`deepseek` → `ChatDeepSeek`；`qwen` → `NoesisChatQwen`（流式 `reasoning_content` 子类）；其它 → `ChatOpenAI`
- [x] 1.3 `backend/.env.example` 补充 `MODEL_TYPE=deepseek` 说明与示例
- [x] 1.4 `uv run app.py` 验证进程可拉起

## 2. 思考提取与 SSE 桥接

- [x] 2.1 新增 `backend/core/reasoning_chunk_extractor.py`：`extract_reasoning_delta(chunk)`
- [x] 2.2 `LangGraphSseBridge`：reasoning 状态机、`reasoning-start/delta/end` 发射、`show_thinking_process` 开关
- [x] 2.3 `on_chat_model_stream`：先 reasoning 后 text；`on_tool_start` / 新轮 LLM 关闭 reasoning
- [x] 2.4 `AssistantMessageBuilder` / 持久化路径确认 `reasoning` part 写入
- [x] 2.5 pytest golden：reasoning chunk → reasoning-delta → content → reasoning-end + text-delta

## 3. 前端

- [x] 3.1 `useSSEStream.ts`：`reasoning-start` / `reasoning-end` 回调
- [x] 3.2 `chat.vue`：`nativeReasoningSeen` 互斥；有原生 reasoning 时禁用 redacted 拆标签
- [x] 3.3 `messageParts.ts`：必要时 `openReasoningPart` / `completeReasoningPart` 与 start/end 对齐
- [x] 3.4 冒烟：Qwen / DeepSeek 思考块流式；无 reasoning 模型仍走 `<think>` 兜底

## 4. 文档

- [x] 4.1 更新 `docs/prd/platform/SSE流式数据设计.md`：厂商字段表、MODEL_TYPE 路由、原生 vs 兜底优先级
- [x] 4.2 更新 `docs/dev/redacted-thinking-inline-parsing.md`、`docs/dev/langchain-stream-demo-implementation.md` 实现状态
- [x] 4.3 `docs/test/test_tdd_design.md` 补充 bridge / 前端互斥测试点

## 5. 验证

- [x] 5.1 运行相关 pytest 与 `pnpm lint`（受影响前端文件）
- [ ] 5.2 手动：`MODEL_TYPE=qwen` + 思考模型，`MODEL_TYPE=deepseek` + reasoner，确认 ReasoningBlock 与正文分离
- [ ] 5.3 手动：`SHOW_THINKING_PROCESS=false` 时不出现 `reasoning-*` 帧
