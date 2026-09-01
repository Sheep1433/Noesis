# 决策：converge-agent-runtime：单一 runtime owner 收敛

状态：implemented
日期：2026-08-04
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** runtime 曾有多套 owner（旧 middleware 栈 + 新装配并行），状态归属不清；目标是单一 owner、删旧装配、可评测。

**How to apply：**
- 五类 owner：`RunGovernor` / `ContextLifecycle` / `ModelExecution` / `ToolExecution` / `Telemetry`；统一 `RuntimeOutcome`、`ToolResultEnvelope`，内部 detail 不泄漏到 SSE。
- `middlewares/` 按职责分层：`kernel/`（公共 runtime）`capabilities/`（Skills/Memory）`observability/`（context metrics）`legacy/`（已退出装配，明确标注不参与 runtime）；顶层不再平铺 20 个文件。
- Memory 语义：**每用户 turn 加载一次、同一 run 内固定**（`TurnMemoryMiddleware`，只挂 `before_agent`）；删除 `memory/revision.py`、`VersionedMemoryMiddleware`、`before_model` 动态刷新和所有写入口 revision bump。理由：run 内保持 system prompt 稳定，用户/Agent 改记忆下一条消息生效即可，无需同轮刷新。
- 附件改独立 input adapter（`AttachmentInputResolver`），不再依赖 `ChatAttachmentsMiddleware`；压缩走 LangChain `SummarizationMiddleware`，不生成旧 `summary_offload` artifact。
- 清理纪律：旧 middleware 先保留为未挂载 legacy、迁走有效断言到统一 runtime 契约测试，再物理删除源码与旧测试；不恢复旧接口让测试变绿。
- LangChain 当前版本 hook 顺序：`before` 正序、`wrap` 嵌套、`after` 逆序。
- `code-review` 双轴收尾发现 6 项待修：Profile 矩阵未生效、子 Agent 未显式继承父 Governor、Telemetry 去重/完整消费、model call 超限误报为 tool limit、ContextVar 可能重复上报上一轮工具结果、sync/async 重复实现。
- 验证基线：backend 801 passed、memory 23-25 passed、frontend 21 passed + lint/build；OpenSpec strict validation。
