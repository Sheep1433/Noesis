## MODIFIED Requirements

### Requirement: Agent 与 runtime 目录归属

Agent 场景、公共 middleware、backend adapter 与 runtime host SHALL 保持单向依赖。公共运行能力 SHALL 不依赖具体 Agent 场景；factory 与场景包 SHALL 能直接导入且不需要 lazy import 或兼容 shim 解决循环。具体物理布局与迁移路径由 design 规定。

#### Scenario: Package 依赖无环

- **WHEN** 独立导入 factory、公共运行能力与 Agent 场景
- **THEN** 导入 SHALL 成功且不依赖 lazy import 回避循环
- **AND** 静态依赖检查 SHALL 不存在公共运行能力反向依赖具体场景

### Requirement: Harness SHALL 分离 Kernel 与 Profile Capability

Harness SHALL 参考 DeepAgents 的直接装配方式，由 factory 根据调用参数选择上游与 Noesis middleware；附件 SHALL 在 Agent 调用前解析。系统 SHALL NOT 为了减少类数而删除 Claude Code 基线能力，也 SHALL NOT 引入 compiler/spec 中间模型。所有 ReAct Profile SHALL 通过同一 LangChain Agent loop 创建。

#### Scenario: COMMON_QA 不加载 SuperAgent 能力

- **WHEN** factory 创建 `COMMON_QA`
- **THEN** SHALL NOT 加载 Skills、Memory、Todo、Filesystem 或 SubAgent capability
- **AND** SHALL 只启用该 Profile 实际配置的 context、tool 与 model policy

### Requirement: Harness SHALL 只有一条运行时装配权威路径

线上 Agent、子 Agent 与离线评测 SHALL 调用同一个 `create_noesis_agent()` 入口，并直接传入 model、tools、system prompt、backend、subagents、skills、memory 与 middleware。Factory SHALL 一次性完成 stack；调用方 SHALL NOT 在构造完成后继续追加 capability。

#### Scenario: 子 Agent 使用同一 Builder

- **WHEN** Super/Fault 构造 task worker
- **THEN** SHALL 通过 factory 参数输入 backend、Skills、compaction 与 tool policy
- **AND** SHALL NOT 在 builder 返回后手动追加 middleware

#### Scenario: 离线评测使用同一装配

- **WHEN** 离线评测执行某一 Agent Profile
- **THEN** SHALL 使用与线上相同的 factory 入口与 middleware 参数
- **AND** adapter SHALL NOT 复制 retry、compaction 或 tool bounding 状态机

### Requirement: Harness SHALL 维护可审计的 Middleware Inventory

Harness SHALL 从实际构造结果生成 middleware inventory，记录每个实例的职责来源、顺序、配置与适用 Profile。系统 SHALL NOT 维护与 builder 分离的 allowlist。Inventory 校验 SHALL 同时检查缺失项、额外项、精确顺序以及所有调用方是否使用完整 builder。

#### Scenario: Inventory 与真实 Stack 同源

- **WHEN** 测试枚举主 Agent 与子 Agent Profile
- **THEN** inventory SHALL 由实际待注册的实例列表生成
- **AND** 声明与构造 SHALL 不可能分别修改

#### Scenario: 已处理能力不重复

- **WHEN** stack 中某项能力已经完成 history archive、tool result offload、Skill 扫描或子 Agent 编译
- **THEN** 后续组件 SHALL 直接消费其结果
- **AND** SHALL NOT 维护第二套同义状态机或存储协议

### Requirement: 各 Agent Profile SHALL 使用确定的 Middleware 集合

最终装配 SHALL 满足下列行为矩阵；“可选”能力只有在配置或 Profile 明确启用时才出现。`TEST_CASE_QA` 继续使用 CaseCoordinator workflow。

| Profile | 必需能力 | 可选能力 |
|---|---|---|
| `COMMON_QA` | SourceRefresh、DynamicContext、ToolResultBudget、Snip、MicroCompaction、Compaction、PatchToolCalls、ToolFailure、SafeModelRetry | ToolCatalog、manual compact、call limits |
| `SUPER_AGENT_QA` | COMMON 全部 + FileContext、DurableContext、Filesystem、Skills、Memory、Todo、SubAgent、ToolCatalog | manual compact、HITL、call limits |
| `FAULT_OPERATION_QA` | SourceRefresh、DynamicContext、DurableContext、ToolResultBudget、Snip、MicroCompaction、Compaction、PatchToolCalls、ToolFailure、SafeModelRetry、SubAgent、ToolCatalog | FileContext、HITL、call limits |
| SimpleMCP | SourceRefresh、DynamicContext、ToolResultBudget、Snip、MicroCompaction、Compaction、ToolCatalog、PatchToolCalls、ToolFailure、SafeModelRetry | manual compact、call limits |
| Super/Fault 子 Agent | isolated/fork policy、SourceRefresh、ToolResultBudget、Snip、MicroCompaction、Compaction、PatchToolCalls、ToolFailure、SafeModelRetry | FileContext、Skills、Filesystem、ToolCatalog、局部 call limits |

#### Scenario: 未配置能力不出现

- **WHEN** Profile 没有 HITL、retry、model limit 或 tool limit 配置
- **THEN** 最终 stack SHALL 不包含对应能力
- **AND** inventory SHALL 不把潜在能力描述为已生效

#### Scenario: Context 能看到最终输入

- **WHEN** SuperAgent 启用 Skills、Memory、SubAgent、Todo 与 HITL
- **THEN** compaction 与最终预算 SHALL 观察到这些能力处理后的 system instructions 与 tool definitions
- **AND** exact hook order SHALL 由装配契约测试固定

### Requirement: Skills 与 Memory SHALL 使用上游解析并支持 Source Revision

Skills 与 Memory SHALL 直接使用选定 DeepAgents 版本的来源解析、private state 与 prompt 注入行为。Noesis SHALL NOT 维护第二套 parser 或 prompt 模板；Noesis `SourceRefreshMiddleware` SHALL 仅负责 revision 判定与定向失效上游 private cache，用于补足 DeepAgents 默认只加载一次的 freshness 差异。

#### Scenario: Skills 来源变化

- **WHEN** 用户安装、删除或启停 Skill
- **THEN** 后续顶层 turn SHALL 生成新 source revision 并定向刷新上游 Skills state
- **AND** Noesis SHALL NOT 自行解析 Skill 正文或维护另一份 Skills metadata

#### Scenario: Memory 使用上游生命周期

- **WHEN** Agent 使用配置的 memory 路径
- **THEN** 加载与注入 SHALL 由 DeepAgents MemoryMiddleware 完成
- **AND** SourceRefresh SHALL 只在顶层 turn 边界根据 revision 失效缓存，不得在 run 中途突变

## ADDED Requirements

### Requirement: Harness SHALL 固定依赖行为契约

Harness SHALL 使用可重复安装的确定依赖版本。依赖升级 SHALL 通过 hook 顺序、private state 隔离、compaction、tool result offload、Skills/Memory 与 subagent propagation 契约测试后才能合入。

#### Scenario: 子 Agent Private State

- **WHEN** 主 Agent state 含仅供内部使用的字段
- **THEN** 普通子 Agent input 与回传 state SHALL 不包含该字段
- **AND** 测试 SHALL 覆盖实际安装版本

#### Scenario: 依赖升级

- **WHEN** lockfile 中 Agent framework 或 middleware library 版本变化
- **THEN** 上游行为契约测试 SHALL 在 CI 执行
- **AND** 失败时 SHALL 阻止在未知 hook/state 语义下继续升级
