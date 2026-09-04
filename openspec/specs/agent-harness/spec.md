# agent-harness Specification

## Purpose
本能力规定后端**包边界与依赖方向**：Agent 内核与业务服务层（agents、factory、llm、runtime、chat、services、auth、knowledge、storage、errors、config）统一位于 workspace distribution `noesis-core`（唯一 Python 顶层包 `noesis`）；HTTP 壳（api、bootstrap、middleware、wiring）位于 `backend/server`，import `server.*`。依赖方向 SHALL 单向：`server → noesis`，`noesis` SHALL NOT import `server` 或任何历史顶层平台包。运行时行为见 `agent-runtime`；评测消费见 `offline-evals`。边界由 `backend/tests/test_core_package_boundary.py` 钉住。

## Requirements

### Requirement: noesis 为唯一内核包且不反向依赖 HTTP 壳

系统 SHALL 将 Agent 工厂、LLM、agents、runtime、backends、middlewares、tools、prompts、mcp、skills、guardrails 及业务 service、chat、auth、knowledge、storage 置于 distribution `noesis-core`（目录 `backend/packages/noesis-core`，唯一顶层包 `noesis`）。`noesis` 包源码 **SHALL NOT** 静态 import `server` 或历史顶层平台包（`api` / `services` / `domain` / `models` / `kb` / `config` / `common` 等旧名）。HTTP 编排、FastAPI app 与平台 wiring SHALL 位于 `backend/server`。

#### Scenario: 边界静态检查

- **WHEN** CI 运行 `test_core_package_boundary`
- **THEN** `noesis` 包内 SHALL 不存在对 `server` 或历史顶层平台包的 import
- **AND** 违例 SHALL 使测试失败

#### Scenario: 评测可独立 import

- **WHEN** 离线评测进程启动
- **THEN** SHALL 能 `from noesis.factory import create_noesis_agent`，且 SHALL NOT 需要启动 FastAPI 或加载 `server` 包

#### Scenario: 单一 import 权威路径

- **WHEN** 扫描 backend Python 源码
- **THEN** SHALL 不存在顶层 `agent.*` / `harness.*` / `llm.*` import 或转发 shim，内核统一使用 `noesis.*`、HTTP 壳统一使用 `server.*`

### Requirement: noesis 提供稳定的公共门面

宿主与评测常用的配置对象、路径函数、logger、共享 stream 和依赖绑定函数 SHALL 可直接从 `noesis.config` / `noesis.runtime` 导入（如 `ModelConfig`、`data_path`、`logger`、`stream_agent_events`、`noesis.runtime.deps`），无需依赖 `env.py`、`stream.py`、`deps.py` 等内部文件布局。公共门面 SHALL 避免在仅导入子系统时急切加载重型运行时或产生配置与日志循环依赖。

#### Scenario: 调用公共配置与运行时能力

- **WHEN** 外部调用方执行 `from noesis.config import ModelConfig, data_path` 以及 `from noesis.runtime import logger, stream_agent_events`
- **THEN** 导入 SHALL 成功，且导出的对象 SHALL 与其权威实现一致

#### Scenario: 子系统导入保持轻量

- **WHEN** 进程仅执行 `import noesis.config` 或 `import noesis.runtime`
- **THEN** SHALL NOT 因门面导出而立即加载全部配置、LangGraph stream 或平台 wiring

### Requirement: 共享流式核

系统 SHALL 提供 `noesis.runtime.stream.stream_agent_events`，线上 BaseAgent 与评测 **SHALL** 复用该入口产出 LC/LG 事件 dict（含 HITL 哨兵）。

#### Scenario: 评测不旁路 stream

- **WHEN** 评测 `BaseAgent` 执行一轮
- **THEN** SHALL 调用 `stream_agent_events`（或等价委托），SHALL NOT 复制一份无 HITL 处理的裸 `astream_events` 循环作为长期权威路径

### Requirement: server 为薄 HTTP 壳

`backend/server` SHALL 只承载 HTTP 关注点：api 路由、bootstrap、middleware、异常处理、wiring；业务逻辑与数据访问 SHALL 位于 `noesis`（services / chat / repositories）。server 模块 SHALL NOT 内联业务规则或直接访问数据库连接之外的实现细节。

#### Scenario: QA 服务单一入口

- **WHEN** server api 调用问答编排
- **THEN** SHALL 经 `noesis.services` 的应用服务入口，且 SHALL NOT 存在重新导出私有 helper 的兼容层

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

### Requirement: Harness SHALL 分离 Kernel 与 Profile Capability

Harness SHALL 由 factory 根据调用参数选择上游与 Noesis middleware；附件 SHALL 在 Agent 调用前解析。系统 SHALL NOT 为了减少类数而删除上游基线能力，也 SHALL NOT 引入 compiler/spec 中间模型。所有 ReAct Profile SHALL 通过同一 LangChain Agent loop 创建。

#### Scenario: COMMON_QA 不加载 SuperAgent 能力

- **WHEN** factory 创建 `COMMON_QA`
- **THEN** SHALL NOT 加载 Skills、Memory、Todo、Filesystem 或 SubAgent capability
- **AND** SHALL 只启用该 Profile 实际配置的 context、tool 与 model policy

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

最终装配 SHALL 满足下列行为矩阵；"可选"能力只有在配置或 Profile 明确启用时才出现。`TEST_CASE_QA` 继续使用 CaseCoordinator workflow。

| Profile | 必需能力 | 可选能力 |
|---|---|---|
| `COMMON_QA` | DynamicContext、ToolResultBudget、Compaction、PatchToolCalls、ToolFailure | Snip、DeferredToolFilter、manual compact、call limits |
| `SUPER_AGENT_QA` | COMMON 全部 + ReadBeforeWrite、DurableContext、Filesystem、RefreshingSkills、RefreshingMemory、Todo、SubAgent、DeferredToolFilter | Snip、manual compact、HITL、call limits |
| `FAULT_OPERATION_QA` | DynamicContext、DurableContext、ToolResultBudget、Compaction、PatchToolCalls、ToolFailure、SubAgent、DeferredToolFilter | ReadBeforeWrite、Snip、HITL、call limits |
| SimpleMCP | DynamicContext、ToolResultBudget、Compaction、DeferredToolFilter、PatchToolCalls、ToolFailure | Snip、manual compact、call limits |
| Super/Fault 子 Agent | DeepAgents isolated task context、ToolResultBudget、Compaction、PatchToolCalls、ToolFailure | ReadBeforeWrite、RefreshingSkills、Filesystem、DeferredToolFilter、Snip、局部 call limits |

#### Scenario: 未配置能力不出现

- **WHEN** Profile 没有 HITL、retry、model limit 或 tool limit 配置
- **THEN** 最终 stack SHALL 不包含对应能力
- **AND** inventory SHALL 不把潜在能力描述为已生效

#### Scenario: Context 能看到最终输入

- **WHEN** SuperAgent 启用 Skills、Memory、SubAgent、Todo 与 HITL
- **THEN** compaction 与最终预算 SHALL 观察到这些能力处理后的 system instructions 与 tool definitions
- **AND** exact hook order SHALL 由装配契约测试固定

### Requirement: Skills 与 Memory SHALL 使用上游解析与独立 Freshness Adapter

Skills 与 Memory SHALL 直接使用选定 DeepAgents 版本的来源解析、private state 与 prompt 注入行为。Noesis SHALL NOT 维护第二套 parser 或 prompt 模板；`RefreshingSkillsMiddleware` 和 `RefreshingMemoryMiddleware` SHALL 分别补足上游默认只加载一次的 freshness 差异，系统 SHALL NOT 建立统一 source hash middleware。

#### Scenario: Skills 来源变化

- **WHEN** 用户安装、删除或启停 Skill
- **THEN** 后续顶层 turn SHALL 生成新 source revision 并定向刷新上游 Skills state
- **AND** Noesis SHALL NOT 自行解析 Skill 正文或维护另一份 Skills metadata

#### Scenario: Memory 使用上游生命周期

- **WHEN** Agent 使用配置的 memory 路径
- **THEN** 加载与注入 SHALL 由上游 MemoryMiddleware 完成
- **AND** RefreshingMemory SHALL 只在顶层 invocation 边界失效缓存，不得在 run 中途突变

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
