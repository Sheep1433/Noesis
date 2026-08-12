## 1. 固定当前行为与上游契约

- [ ] 1.1 为 COMMON、SUPER、FAULT、SimpleMCP 和子 Agent 记录当前真实 middleware 顺序、state schema、system/messages/tools 基线
- [ ] 1.2 建立旧五 owner 字段级迁移表：配置、state、stop reason、ToolMessage metadata、SSE 事件与测试必须标记迁移、删除或上游替代
- [ ] 1.3 增加 summary 错误文本替换 history、tool pair、raw history、archive、overflow recovery 和 retry 副作用基线
- [ ] 1.4 增加 DeepAgents Filesystem、Skills、Memory、PatchToolCalls、SubAgent private state、prompt cache 与 HITL 契约测试
- [ ] 1.5 增加 `backend/config.yaml` 到 Pydantic 实际值的加载测试，找出被静默忽略的 runtime 配置键
- [ ] 1.6 pin DeepAgents `0.6.12`、LangChain `1.3.4` 和 LangGraph `1.2.4`，依赖升级必须通过上述契约测试

## 2. 建立 DeepAgents 风格装配

- [ ] 2.1 创建顶层 `noesis.middleware`、`noesis.backends` 与 Provider adapter 目录，保持公共包不反向导入具体 Agent
- [ ] 2.2 将 `create_noesis_agent()` 改为直接参数入口，一次构造 stack 并立即调用 `create_agent()`；不增加 compiler/spec 中间模型
- [ ] 2.3 让主 Agent、子 Agent 和离线评测使用同一入口，禁止 factory 返回后 append middleware
- [ ] 2.4 从实际实例列表生成 inventory，固定各 Profile 的必需项、可选项和 exact order
- [ ] 2.5 实现 Provider canonical request adapter，覆盖 system 合并、role/tool pair、thinking/media、schema 排序、deferred 字段与 cache marker

## 3. 实现稳定上下文来源

- [ ] 3.1 实现 `SourceRefreshMiddleware`：顶层 turn 计算 source revision，只失效变更来源，同一 run 内保持稳定
- [ ] 3.2 直接采用 DeepAgents Skills/Memory 的 parser 与 private state，由 SourceRefresh 解决“只加载一次”的 freshness 差异
- [ ] 3.3 实现 `DynamicContextMiddleware`，只注入时间、workspace/session 和 attachment manifest 等已解析来源
- [ ] 3.4 实现 `DurableContextMiddleware`，保存 plan/task/skill/file/tool 引用和 compact instructions，不复制 conversation 正文
- [ ] 3.5 实现 `FileContextMiddleware` 与 backend/tool 契约：read state、mtime/hash stale、write-before-read 和 post-compact excerpt 恢复
- [ ] 3.6 保证 source revision 未变时 prompt prefix 字节稳定，变更时以有界 delta 发布

## 4. 实现分层 Context Reduction

- [ ] 4.1 实现确定性 `ToolResultBudgetMiddleware`，保存 artifact path、synopsis、hash 与 replacement record，resume 后决策一致
- [ ] 4.2 实现 `SnipMiddleware`，只改变 effective projection，不物理删除 raw transcript，不切断 boundary/tool pair
- [ ] 4.3 实现 `MicroCompactionMiddleware`，缩减旧 tool result、write/edit 参数、重复附件和过期 tool delta
- [ ] 4.4 关闭 DeepAgents 中与 MicroCompaction 同义的旧 tool-arg truncation owner，保留 archive/partition/tail engine
- [ ] 4.5 对 replacement、snip 和 micro-compaction 增加 raw/effective history、tokens freed、tool pair 和 checkpoint resume 契约测试

## 5. 实现 Tool Catalog 与 Deferred Schema

- [ ] 5.1 建立 runtime tool registry 作为 MCP 连接、schema、revision 和权限的权威源
- [ ] 5.2 实现 `ToolCatalogMiddleware`：基础/激活工具常驻，大型 MCP 工具 deferred，`tool_search` 激活 discovered set
- [ ] 5.3 Provider 支持时输出原生 deferred schema，不支持时过滤最终 `request.tools`
- [ ] 5.4 覆盖 MCP 重连/变更、compaction rebuild、schema token 预算、未发现工具不可调用和执行时重新授权

## 6. 实现 Claude Code 式 Compaction

- [ ] 6.1 用最终 canonical request 计算 system/messages/tool results/tool schemas/attachments/framing 预算和 reserve/buffer/guard 阈值
- [ ] 6.2 实现 incremental、full、prefix、reactive 和 manual 模式；summary request 禁用业务 tools 并有 recursion guard
- [ ] 6.3 使用结构化 summary，校验空/错误文本，保留 raw history、archive、preserved tail 和完整 API round
- [ ] 6.4 实现 summary prompt-too-long 的有界 prefix retry、进展检查和 run/checkpoint 持久化连续失败 breaker
- [ ] 6.5 实现 archive/summary/boundary/stable refs 一次提交；失败不得先清空 history、file state 或 discovered tools
- [ ] 6.6 实现 host/runtime manual compact 入口与可选 tool，两者共用同一 compaction engine 并支持用户保留指令
- [ ] 6.7 覆盖 Provider 真实 overflow 一次 reactive recovery、恢复失败终止和 post-compact stable source rebuild

## 7. 实现 Subagent Context Policy

- [ ] 7.1 组合 DeepAgents SubAgent 并实现 `isolated` 默认模式：独立 messages、file state、tool discovery 和 compaction state
- [ ] 7.2 实现显式 `fork`：复制父 conversation snapshot 与白名单 durable context，可变 state 必须 deep copy
- [ ] 7.3 实现子 Agent 自有 checkpoint `resume`，不重读父 Agent 当前 state
- [ ] 7.4 子 Agent 只以对应 tool call id 的有界 ToolMessage 回传；并发、取消、超时和 admission 继续由 runtime task registry 管理

## 8. 迁移 Tool/Model 安全边界

- [ ] 8.1 实现只负责异常翻译的 `ToolFailureMiddleware`，放过 LangGraph control exception、整轮取消和 HITL interrupt
- [ ] 8.2 保留 `SafeModelRetryMiddleware`，只在无可见 text/tool call/HITL/副作用时重试；context overflow 交给 Compaction
- [ ] 8.3 删除 `empty_after_tools` 对 inner handler 的不可观测二次调用；需要 continuation 时重走完整 Agent lifecycle
- [ ] 8.4 只对已有真实配置与记账入口的轴启用 LangChain model/tool call limits

## 9. 删除旧结构并交付

- [ ] 9.1 按字段级迁移表删除 RuntimeTelemetry、RunGovernor、ContextLifecycle、ModelExecution 和 ToolExecution 五个宏观 owner
- [ ] 9.2 删除 `agents/middlewares/kernel|capabilities`、重复导出、`agents.__getattr__`、隐式 ContextVar 链、手写 inventory 和失效配置；不保留兼容 shim
- [ ] 9.3 运行 package 无环导入、backend 全量 pytest、启动冒烟和各 Profile/子 Agent E2E，停止所有临时进程
- [ ] 9.4 运行长上下文、summary PTL、Provider overflow、大 MCP catalog、file stale、fork/resume、HITL、SSE 与 assistant 持久化回归
- [ ] 9.5 记录 Provider actual usage、local request estimate、cache read/write、replacement/snip/micro/compaction 事件，保持两类 token 语义独立
- [ ] 9.6 将工程导读更新为 Current，使用 `code-review` 检查规格与仓库约定
