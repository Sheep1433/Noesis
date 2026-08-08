## ADDED Requirements

### Requirement: Harness SHALL 分离 Kernel 与 Profile Capability

`noesis.factory` SHALL 将公共 runtime kernel 与 Agent Profile capability 分开装配。公共 kernel SHALL 由 `agent-runtime-lifecycle` 定义；Filesystem、SubAgent、Skills、Memory、Attachments、Todo 与 HITL SHALL 作为 Profile capability 按需选择，SHALL NOT 被统计或实现为所有 Agent 必须加载的公共 guard。

#### Scenario: COMMON_QA 不加载 SuperAgent 能力

- **WHEN** factory 创建 `COMMON_QA` Agent
- **THEN** SHALL 加载公共 runtime kernel
- **AND** SHALL NOT 因 kernel 装配而加载 Skills、Memory、Todo 或 SubAgent capability

### Requirement: Harness SHALL 只有一条运行时装配权威路径

线上 Agent、子 Agent 与离线评测 SHALL 通过同一 factory/runner 获得 runtime lifecycle。系统 SHALL 删除被新 owner 取代的 middleware、兼容 wrapper 与重复计数，SHALL NOT 保留 legacy 与新 runtime 两套可配置并行方案。

#### Scenario: Harbor 使用统一 runtime

- **WHEN** Harbor `BaseAgent` 执行 SuperAgent 评测
- **THEN** SHALL 使用与线上相同的 Model Execution、Context Lifecycle、Tool Execution 和 Run Governor
- **AND** SHALL NOT 在 adapter 内复制这些状态机

### Requirement: Harness SHALL 维护可审计的 Middleware Inventory

`noesis.factory` SHALL 以代码常量、类型化 builder 或等价可测试结构维护以下 middleware inventory，并在装配测试中验证来源与适用 Profile。系统 SHALL 优先直接使用 LangChain/DeepAgents 已满足需求的 middleware；只有存在明确 Noesis 语义差异时才使用自定义 subclass 或 runtime middleware。

| 分类 | Middleware | 来源 | 装配方式 |
|---|---|---|---|
| 公共 kernel | `ContextLifecycleMiddleware` | Noesis | 所有 ReAct Agent |
| 公共 kernel | `ModelExecutionMiddleware` | Noesis | 所有 ReAct Agent |
| 公共 kernel | `ToolExecutionMiddleware` | Noesis | 所有 ReAct Agent |
| 公共 kernel | `RunGovernorMiddleware` | Noesis | 所有 ReAct Agent |
| 公共 kernel | `RuntimeTelemetryMiddleware` | Noesis | 所有 ReAct Agent；telemetry 可关闭但行为不变 |
| capability | `FilesystemMiddleware` | DeepAgents，直接使用 | 具有 workspace/backend 的 Profile 与对应子 Agent |
| capability | `SubAgentMiddleware` | DeepAgents，直接使用 | 配置同步子 Agent 的 Profile |
| capability | `AsyncSubAgentMiddleware` | DeepAgents，直接使用 | 配置远程异步子 Agent 的 Profile |
| capability | `TodoListMiddleware` | LangChain，直接使用 | SuperAgent |
| capability | `HumanInTheLoopMiddleware` | LangChain，直接使用 | 开启 HITL 且存在审批策略的 Profile |
| capability adapter | `VersionedSkillsMiddleware` | Noesis，继承 DeepAgents `SkillsMiddleware` | 启用 Skills 的主/子 Agent；来源 revision 变化时失效 |
| capability adapter | `TurnMemoryMiddleware` | Noesis，继承 DeepAgents `MemoryMiddleware` | SuperAgent 主 Agent；每次用户 turn 加载一次，本 run 内固定 |

LangChain `SummarizationMiddleware` SHALL 作为 `ContextLifecycleMiddleware` 内部采用的 compaction engine 或父类，SHALL NOT 与 Context Lifecycle 作为两个独立决策 owner 同时挂载。DeepAgents `FilesystemMiddleware` 已提供文件工具、filesystem 权限和大工具结果 offload；Noesis SHALL 直接使用该能力，SHALL NOT 为同一 ToolMessage 再次转存。会话附件 SHALL 由 Agent 调用前的 `AttachmentInputResolver`（或等价 input adapter）将附件 manifest、图片 block 或 VLM caption 写入本轮 HumanMessage，SHALL NOT 再挂载 `ChatAttachmentsMiddleware`。

#### Scenario: 工厂清单可枚举

- **WHEN** 测试按 Profile 调用 middleware builder
- **THEN** SHALL 能断言每个实例的类型、来源分类和顺序
- **AND** SHALL 能区分直接使用的第三方 middleware 与 Noesis 自定义 middleware

#### Scenario: 第三方能力不重复实现

- **WHEN** `FilesystemMiddleware` 已将大 ToolMessage 替换为 backend 路径和有界 preview
- **THEN** `ToolExecutionMiddleware` SHALL 保留该引用并只补统一 envelope 元数据
- **AND** SHALL NOT 再写第二份 artifact 或再次截断 preview

#### Scenario: 附件在调用前解析

- **WHEN** COMMON_QA 或 SUPER_AGENT_QA 发起带附件的新 user turn
- **THEN** input resolver SHALL 在调用 Agent 前生成最终 HumanMessage
- **AND** middleware inventory SHALL NOT 包含 `ChatAttachmentsMiddleware`

### Requirement: 各 Agent Profile SHALL 使用确定的 Middleware 集合

最终装配 SHALL 满足以下 Profile 矩阵；`TEST_CASE_QA` 的 CaseCoordinator 不是 ReAct Agent，保持现有 LangGraph workflow，不强制装配公共 kernel。

| Profile | 直接使用的 capability | Noesis capability adapter | 公共 kernel |
|---|---|---|---|
| `COMMON_QA` | 无；除非未来显式提供 backend | 无；附件由 input resolver 处理 | 五个公共 runtime middleware |
| `SUPER_AGENT_QA` | Filesystem、SubAgent、可选 AsyncSubAgent、TodoList、可选 HITL | VersionedSkills、TurnMemory；附件由 input resolver 处理 | 五个公共 runtime middleware |
| `FAULT_OPERATION_QA` | Filesystem、SubAgent | 无 | 五个公共 runtime middleware |
| SimpleMCP 调试 Agent | 无 | 无 | 五个公共 runtime middleware |
| Super/Fault 子 Agent | Filesystem | 按定义可选 VersionedSkills | 五个公共 runtime middleware，并继承父 Governor scope |

#### Scenario: SuperAgent inventory

- **WHEN** SuperAgent 在 Skills、Memory、SubAgent 与 HITL 全部启用时完成装配
- **THEN** middleware 类型集合 SHALL 与 Profile 矩阵一致
- **AND** SHALL NOT 同时出现旧 `ModelRetryMiddleware`、`LoopDetectionMiddleware`、`ToolErrorHandlingMiddleware` 或独立 `ToolCallLimitMiddleware`

### Requirement: Skills 与 Memory SHALL 使用符合生命周期的缓存语义

Skills capability SHALL 通过不透明 source revision 判断 checkpoint metadata 是否失效，仅在缺失或变化时调用 DeepAgents 基类重新加载。Memory capability SHALL 在每次新的顶层 Agent invocation 开始时调用 DeepAgents 基类加载一次，并在该 run 的后续 model call 中保持不变；同一 run 的 Memory 写入 SHALL 在下一次用户 turn 生效，SHALL NOT 触发当前 run 的 system prompt 动态刷新。

Noesis SHALL 保留 DeepAgents `SkillsMiddleware` / `MemoryMiddleware` 的来源扫描、内容解析与 system prompt 注入实现，SHALL NOT 复制这些逻辑。Memory SHALL NOT 建立持久 revision marker 或要求所有写入口发送缓存失效通知。

#### Scenario: Skills 未变化

- **WHEN** checkpoint 已有 skills metadata 且当前 Skills revision 与 state 一致
- **THEN** VersionedSkills SHALL 使用现有 metadata
- **AND** SHALL NOT 再次扫描 Skill 目录

#### Scenario: Skills 安装后下一轮可见

- **WHEN** 用户安装或删除 Skill 使 source revision 变化
- **THEN** VersionedSkills SHALL 使旧 metadata 失效并调用 DeepAgents 加载逻辑
- **AND** 下一次模型请求 SHALL 使用新 Skill 列表

#### Scenario: 下一用户 turn 读取最新 Memory

- **WHEN** Agent 成功写入 `/memory/AGENTS.md` 或 `/memory/USER.md`
- **THEN** 当前 Agent run 的后续 model call SHALL 继续使用本轮开始时加载的 Memory
- **AND** 下一次用户 turn 开始时 TurnMemory SHALL 通过 DeepAgents 重新加载最新内容一次
