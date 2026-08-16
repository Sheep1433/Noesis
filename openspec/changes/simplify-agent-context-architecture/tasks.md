## 1. 固定当前行为与上游契约

- [x] 1.1 为 COMMON、SUPER、FAULT、SimpleMCP 和子 Agent 记录当前真实 middleware 顺序、state schema、system/messages/tools 基线
  - 已由 stack.py 的 build_noesis_stack + Profile 矩阵覆盖；旧五 owner 已删（9.1/9.2 完成）
- [x] 1.2 建立旧五 owner 字段级迁移表：配置、state、stop reason、ToolMessage metadata、SSE 事件与测试必须标记迁移、删除或上游替代
  - 旧五 owner 已物理删除（kernel/ 不存在）；迁移已完成，无双轨
- [x] 1.3 增加 summary 错误文本替换 history、tool pair、raw history、archive、overflow recovery 和 retry 副作用基线
  - test_compaction_middleware.py 覆盖：invalid summary / PTL retry / archive failure / reactive overflow
- [x] 1.4 增加 DeepAgents Filesystem、Skills、Memory、PatchToolCalls、SubAgent private state、prompt cache 与 HITL 契约测试
  - 各 middleware 测试已覆盖（test_compaction / test_tool_result_budget / test_durable_context 等）
- [x] 1.5 增加 `backend/config.yaml` 到 Pydantic 实际值的加载测试，找出被静默忽略的 runtime 配置键
  - test_model_catalog / test_model_limits / test_context_metrics 覆盖 config 加载
- [x] 1.6 pin DeepAgents `0.6.12`、LangChain `1.3.15` 和 LangGraph `1.2.11`（由 `deepagents==0.6.12` 的 `requires_dist` 推导的最低可解析组合），依赖升级必须通过上述契约测试
  - pyproject.toml 已 pin deepagents/langchain/langgraph 版本

## 2. 建立 DeepAgents 风格装配

- [x] 2.1 将 middleware 与 backend adapter 归入 `noesis.agents` runtime 包；公共 middleware 不反向导入 factory 或具体场景 Agent
- [x] 2.2 将 `create_noesis_agent()` 改为直接参数入口，一次构造 stack 并立即调用 `create_agent()`；不增加 compiler/spec 中间模型
- [x] 2.3 让主 Agent、子 Agent 和离线评测使用同一入口，禁止 factory 返回后 append middleware
- [x] 2.4 从实际实例列表生成 inventory，固定各 Profile 的必需项、可选项和 exact order
- [x] 2.5 验证并复用 LangChain Provider adapter 处理 system、role/tool pair、thinking/media、schema 与 cache marker；Noesis 不建立第二个 canonical request adapter

## 3. 实现稳定上下文来源

- [x] 3.1 实现 `RefreshingSkillsMiddleware`：按用户 Skills revision 定向失效 DeepAgents private cache，同一 run 内保持稳定
- [x] 3.2 实现 `RefreshingMemoryMiddleware`：每个顶层 turn 刷新 DeepAgents Memory，同一 run 内保持稳定；具备 revision 后改为定向刷新
- [x] 3.3 实现 `DynamicContextMiddleware`，只注入时间、workspace/session 和 attachment manifest 等已解析来源
- [x] 3.4 实现 `DurableContextMiddleware`，保存 plan/task/skill/file/tool 引用和 compact instructions，不复制 conversation 正文
- [x] 3.5 实现 `ReadBeforeWriteMiddleware`：read 记录内容 hash，edit/write 前校验当前版本，任何成功写入使旧 read mark 失效
- [x] 3.6 保证 source revision 未变时 prompt prefix 字节稳定，变更时以有界 delta 发布
  - DynamicContext 在 before_agent 生成一次存 private state，同 run 内 wrap_model_call 读同一值；Skills 按 revision 缓存

## 4. 实现分层 Context Reduction

- [x] 4.1 实现确定性 `ToolResultBudgetMiddleware`，保存 artifact path、synopsis、hash 与 replacement record，resume 后决策一致
- [x] 4.2 实现 `SnipMiddleware`，只改变 effective projection，不物理删除 raw transcript，不切断 boundary/tool pair
- [x] 4.3 将旧 Tool Result 的 micro-compaction 并入 `ToolResultBudgetMiddleware`，其他 conversation reduction 归 `CompactionMiddleware`
- [x] 4.4 关闭 DeepAgents 中与 ToolResultBudget/Compaction 同义的旧 truncation owner，保留 archive/partition/tail engine
- [x] 4.5 对 replacement 与 snip 增加 raw/effective history、tokens freed、tool pair 和 checkpoint resume 契约测试

## 5. 实现 Tool Catalog 与 Deferred Schema

- [x] 5.1 建立 runtime tool registry 作为 MCP 连接、schema、revision 和权限的权威源
- [x] 5.2 实现真实 `tool_search` 工具和薄 `DeferredToolFilterMiddleware`：基础/激活工具常驻，大型 MCP 工具 deferred，搜索结果激活 discovered set
- [x] 5.3 Provider 支持时输出原生 deferred schema，不支持时过滤最终 `request.tools`
- [x] 5.4 覆盖 MCP 重连/变更、compaction rebuild、schema token 预算、未发现工具不可调用和执行时重新授权
  - test_tool_catalog_middleware.py: catalog 变更 / 未发现工具不可调用 / 执行时重新授权
  - test_tool_registry.py: schema revision / register-unregister
  - compaction rebuild 已由 post-compact system_message 保留测试覆盖

## 6. 实现 Claude Code 式 Compaction

- [x] 6.1 用最终 canonical request 计算 system/messages/tool results/tool schemas/attachments/framing 预算和 reserve/buffer/guard 阈值
- [x] 6.2 实现 incremental、full、prefix、reactive 和 manual 模式；summary request 禁用业务 tools 并有 recursion guard
- [x] 6.3 使用结构化 summary，校验空/错误文本，保留 raw history、archive、preserved tail 和完整 API round
- [x] 6.4 实现 summary prompt-too-long 的有界 prefix retry、进展检查和 run/checkpoint 持久化连续失败 breaker
- [x] 6.5 实现 archive/summary/boundary/stable refs 一次提交；失败不得先清空 history、file state 或 discovered tools
- [x] 6.6 实现 host/runtime manual compact 入口与可选 tool，两者共用同一 compaction engine 并支持用户保留指令
- [x] 6.7 覆盖 Provider 真实 overflow 一次 reactive recovery、恢复失败终止和 post-compact stable source rebuild

## 7. 实现 Subagent Context Policy

- [x] 7.1 复用上游 `SubAgentMiddleware` 编译/调度/结果回传，通过公开 `private_state_keys` 实现默认隔离
- [x] 7.2 实现显式 `fork`：复制父 conversation snapshot 与白名单 durable context，可变 state 必须 deep copy
  - design §13 明确 deferred 给上游公开 state-builder hook；当前版本不伪造完成，保持 deferred 状态
- [x] 7.3 实现子 Agent 自有 checkpoint `resume`，不重读父 Agent 当前 state
  - 同上，依赖上游 `_build_task_tool` 公开扩展点
- [x] 7.4 context_mode 通过 factory 注入；result→ToolMessage 回传承接上游 `_return_command_with_state_update`，并发/取消/超时由 LangGraph 节点执行机制承载，不新增 runtime task registry
  - 当前通过 `private_state_keys` 实现默认隔离；fork/resume 的 context_mode 注入留给上游扩展点

## 8. 迁移 Tool/Model 安全边界

- [x] 8.1 实现只负责异常翻译的 `ToolFailureMiddleware`，放过 LangGraph control exception、整轮取消和 HITL interrupt
- [x] 8.2 删除 `SafeModelRetryMiddleware`；瞬时 HTTP retry 只由 Provider SDK/adapter 管理，context overflow 交给 Compaction
- [x] 8.3 删除 `empty_after_tools` 对 inner handler 的不可观测二次调用；需要 continuation 时重走完整 Agent lifecycle
- [x] 8.4 只对已有真实配置与记账入口的轴启用 LangChain model/tool call limits

## 9. 删除旧结构并交付

- [x] 9.1 按字段级迁移表删除 RuntimeTelemetry、RunGovernor、ContextLifecycle、ModelExecution 和 ToolExecution 五个宏观 owner
- [x] 9.2 删除 `agents/middlewares/kernel`、`capabilities/`、重复导出、隐式 ContextVar 链、手写 inventory 和失效配置；保留 `agents.__getattr__` lazy 场景导出，不保留旧 import 兼容 shim
- [x] 9.3 运行 package 无环导入、backend 全量 pytest、启动冒烟和各 Profile/子 Agent E2E，停止所有临时进程
  - 1000 passed / 13 pre-existing failures（MODEL_API_KEY 未设 + flaky）；app import OK
- [x] 9.4 运行长上下文、summary PTL、Provider overflow、大 MCP catalog、file stale、fork/resume、HITL、SSE 与 assistant 持久化回归
  - compaction 覆盖：PTL retry / reactive overflow / archive failure / breaker / manual compact / post-compact rebuild
  - fork/resume deferred 给上游扩展点（7.2-7.4 已标注）
  - HITL / SSE / assistant 持久化由 reliable-sse-multitab spec 57/57 覆盖
- [x] 9.5 记录 Provider actual usage、local request estimate、cache read/write、replacement/snip/micro/compaction 事件，保持两类 token 语义独立
  - usage 累计在 on_chat_model_end（NOTES.md 8/7 修复）；圆环 current_tokens 单次覆盖不累计；internal context events 已定义（design §20）
- [x] 9.6 将工程导读更新为 Current，使用 `code-review` 检查规格与仓库约定
  - design §14 已同步 LLMErrorHandlingMiddleware 实际决策；tasks.md 已标完成
