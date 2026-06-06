## 1. Runtime 装配

- [x] 1.1 新增 `backend/agent/factory.py`（或等价路径），统一封装 `create_agent(...)` 的 model、checkpointer、middleware 装配。
- [x] 1.2 将 `backend/agent/common_react_agent.py` 迁移到 runtime factory，去掉内联 middleware 拼装。
- [x] 1.3 评估并接线 `FaultOperationAgent`、`DeepResearchAgent` 的公共 runtime guard 开关；暂不适配的路径需在代码注释或 design 中写明原因。

## 2. Summarization offload

- [x] 2.1 在 `backend/llm.py` / `config/env.py` 增加摘要模型配置读取，支持“摘要模型独立于主模型”。
- [x] 2.2 新增 Noesis 自定义 summarization middleware 封装，默认复用现有 `SummarizationMiddleware` 语义，但改为从摘要模型句柄发起摘要。
- [x] 2.3 为摘要关闭、摘要独立模型、摘要回退主模型三种情形补单测或最小验证。

## 3. Loop detection

- [x] 3.1 新增 `backend/agent/middlewares/loop_detection.py`，实现重复工具集合检测与同类工具空转检测。
- [x] 3.2 为 warning 阈值与 hard-stop 阈值增加可配置项，并定义稳定的用户可见收敛文案。
- [x] 3.3 为 loop detection 编写单测：正常多步工具链不过早触发、重复 read/search/bash 触发 warning、持续重复触发 hard-stop。

## 4. Dangling tool repair

- [x] 4.1 新增 `backend/agent/middlewares/dangling_tool_call.py`，在模型调用前扫描并补 synthetic `ToolMessage`。
- [x] 4.2 覆盖 `tool_calls` 来源差异：`msg.tool_calls` 与 `additional_kwargs.tool_calls`。
- [x] 4.3 为中断后恢复场景补测试：构造缺失 `ToolMessage` 的历史，断言下一轮模型输入已被修复。

## 5. 流式与持久化回归

- [x] 5.1 在 `backend/services/qa_service.py` 相关测试中补“页面刷新/CancelledError 后继续提问”的回归用例。
- [x] 5.2 验证 synthetic repair 不破坏现有 `partial` / `completed` / `error` 落库语义。
- [x] 5.3 检查 `LangGraphSseBridge` 与前端 SSE 契约无需新增事件类型，确保兼容。

## 6. 文档与规格

- [x] 6.1 更新 `docs/readings/sse-dangling-tool-call-analysis.md` 或对应 PRD，引入已实施方案与非目标说明。
- [x] 6.2 更新 `docs/test/test_tdd_design.md`，加入 summarization offload、loop detection、dangling repair 的测试清单。
- [x] 6.3 补齐本 change 的 delta spec，并运行 `openspec validate`（或仓库约定的等价校验）确认 proposal 可进入实现。

## 7. 高风险检查项

- [x] 7.1 检查消息持久化：不得因 synthetic repair 破坏 `content.parts` 兼容格式。
- [x] 7.2 检查 Langfuse / tracing：新增摘要模型调用后，追踪配置不能泄漏密钥，也不能让 tracing 失败阻塞主流程。
- [x] 7.3 检查测试用例 Agent 与故障运维 Agent：如果暂未全面接入 loop detection，必须保留现有行为且不引入回归。
