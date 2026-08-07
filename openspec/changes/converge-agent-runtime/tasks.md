## 1. Runtime 契约与反馈测试

- [x] 1.1 为当前 LangChain/DeepAgents 版本补契约测试，确认 `before_*` 正序、`wrap_*` 嵌套、`after_*` 逆序，以及 async model hook 可取得 finish reason、可见 token 边界、tool/HITL 副作用边界及工具 dispatch 包装点
- [x] 1.2 在 `noesis.runtime` 定义稳定的 `RuntimeOutcome`、stop reason、`ToolResultEnvelope` 与 run-scoped governor state，保持内部技术详情不进入用户事件
- [x] 1.3 建立 `ContextLifecycleMiddleware`、`ModelExecutionMiddleware`、`ToolExecutionMiddleware`、`RunGovernorMiddleware` 与 `RuntimeTelemetryMiddleware` 装配骨架，并区分 Profile capability 与公共 runtime kernel
- [x] 1.4 建立可枚举 middleware inventory 与 Profile 矩阵测试，验证直接采用的第三方类型、自定义 adapter、公共 kernel 及最终顺序

## 2. Capability Adapter 收敛

- [x] 2.1 将 `RevisableSkillsMiddleware` 重构为 `VersionedSkillsMiddleware`，保留 DeepAgents Skills 扫描/解析/注入，仅统一 revision state 与缓存失效
- [x] 2.2 将 DeepAgents `MemoryMiddleware` 与 `MemorySyncMiddleware` 收敛为 `TurnMemoryMiddleware`：每次顶层 Agent invocation 通过 DeepAgents 加载一次，本 run 内固定；删除 revision marker、写入口联动和 `before_model` 刷新
- [x] 2.3 将 `ChatAttachmentsMiddleware` 迁为调用前 `AttachmentInputResolver`，让 COMMON_QA / SUPER_AGENT_QA 直接构造最终 HumanMessage，验证 HITL resume 不重复注入
- [x] 2.4 删除三个旧 adapter 文件和 factory/profile 旧装配，补 Skills 未变化/变更、Memory 同轮固定/下一 turn 刷新及附件 multimodal/VLM 回归测试

## 3. Tool Execution 与有界输出

- [x] 3.1 将现有 typed tool failure 分类和 outcome 解析接入统一 Tool Execution owner，保持 `status` / `errorCategory` / `outcome` 现有语义
- [x] 3.2 直接采用 DeepAgents `FilesystemMiddleware` 的通用 tool-result offload；Tool Execution 将其处理后的 ToolMessage content 原样纳入 envelope，不解析第三方提示文本；对无 backend 或仍未有界结果仅执行一次 head/tail fallback
- [x] 3.3 从 `SummarizationOffloadMiddleware` 删除工具结果扫描/转存职责，停止生成旧 `summary_offload/` artifact，并补旧文件清理/兼容说明
- [x] 3.4 增加防二次处理测试：DeepAgents 已 offload 的 ToolMessage 不再创建 Noesis artifact、不再次截断，且 status/outcome 保持不变

## 4. Model Execution

- [x] 4.1 将 `ModelRetryMiddleware` 迁入统一 Model Execution owner，沿用 `model_attempt` 的可见输出和副作用边界，确保 Provider sampling retry 不重复
- [x] 4.2 解析并测试 `length_stop`、`safety_stop`、`context_exhausted` 与 `partial_output`，保留已产生正文且不重放 step
- [x] 4.3 在 Model Execution 内实现 `empty_after_tools`：以不落库的瞬时提示最多再次调用 handler 一次，再次为空时返回固定可见 fallback 与稳定 stop outcome；覆盖至少两个已配置 Provider 的响应形态
- [x] 4.4 删除被 Model Execution 取代的 retry/terminal 补丁分支和临时日志，确保同步与异步路径使用相同 outcome

## 5. Run Governor 与子 Agent Registry

- [x] 5.1 将 loop window、全局/per-tool call limit 迁入 run-scoped Governor，统一计数、stop reason 与恢复语义
- [x] 5.2 在同步和异步子 Agent spawn/完成/中断边界实现 active slot、total 与 depth 的原子预留和释放，并继承父 run 总预算
- [x] 5.3 在 `add-agent-context-usage-attribution` 的 Provider usage collector 落地后接入可选累计 token budget；其完成前保持禁用，禁止另建 collector 或用 context 估算冒充实际 cost
- [x] 5.4 删除旧 LoopDetection registry、重复 `ToolCallLimitMiddleware` 装配及被替代配置，补并发、循环、恢复和跨会话隔离测试

## 6. Context Lifecycle

- [x] 6.1 在 `ContextLifecycleMiddleware` 建立两阶段路径：`before_model` 负责 normalization/compaction，innermost `wrap_model_call` 基于 Skills/Memory 处理后的最终 request 构造唯一 `ContextSnapshot`
- [x] 6.2 将 dangling call/output normalization、预算判断和 compaction 迁入 Context Lifecycle，保证 Provider 请求前配对合法
- [x] 6.3 把 system prompt、Skills、Memory、SessionClock 建模为可重建 context sources，验证 compaction 后长期上下文恢复且瞬时提示不重复
- [x] 6.4 采用 LangChain `SummarizationMiddleware` 的摘要 engine/消息处理能力，由 `ContextLifecycleMiddleware` 独占触发与重建决策
- [x] 6.5 删除独立 `SessionClockMiddleware`、`DanglingToolCallMiddleware`、`ContextBudgetGuardMiddleware` 和旧 summarization 决策 owner；Telemetry 改为只读 `ContextSnapshot`

## 7. Factory、流式与持久化

- [x] 7.1 重构 `create_noesis_agent` / `build_subagent_default_middleware` 使用单一 inventory builder，固定 Telemetry → ToolExecution → capabilities → HITL → Governor → ContextLifecycle → ModelExecution 的 outer-to-inner 顺序，子 Agent 继承父 Governor scope
- [x] 7.2 将 RuntimeOutcome 映射到共享 stream 的 `run-status` / `finish` / `error`，并在 assistant 终态保存稳定 `finish_reason`
- [x] 7.3 更新前端类型和兼容解析：旧事件正常收尾，新 stop reason 不导致重复 part 或错误终态

## 8. 评测、回归与收尾

- [x] 8.1 更新 Harbor、BrowseComp 与 Agentic RAG collector 消费统一 outcome/event，确认 adapter 不包含 retry、compaction、tool bounding 或 governor 分支
- [x] 8.2 为超大工具结果、dangling call、length stop、可见 token 后断流、工具后空响应、重复工具和 subagent 限制建立确定性回归 fixture
- [ ] 8.3 跑 harness/runtime/stream/tool failure/assistant persistence 相关后端测试及前端 lint/build；执行一条 Harbor Agent E2E 和一条线上 SuperAgent 流式冒烟
- [x] 8.4 使用 `code-review` 按本 spec 与仓库规范审查改动，再对确认存在的浅 wrapper、重复计算、旧兼容分支使用 `code-simplification`
- [x] 8.5 更新 `docs/architecture/` 当前 runtime 架构，删除研究建议中已落地后的过时描述，并确认 OpenSpec 所有任务和规格场景可追溯
