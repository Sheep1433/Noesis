# agent-harness Specification

## Purpose

本能力规定 **harness 包边界与依赖隔离**：Agent 内核（工厂、LLM、agents、runtime、backends、middlewares、tools、prompts、mcp、skills、guardrails）作为独立 distribution `noesis-harness`（顶层包 `noesis`），SHALL NOT 静态依赖上层平台包（services/domain/models/api/kb/config/common）；平台 Delivery 与 HTTP 编排 SHALL NOT 位于该包内。需要附件存储、KB 检索等宿主能力时 SHALL 经 `noesis.runtime.deps` 由宿主按运行场景绑定。运行时行为细节见 `agent-runtime`；评测消费见 `offline-evals`。
## Requirements
### Requirement: Harness 为独立 Agent 内核包

系统 SHALL 将 Agent 工厂、LLM、agents、runtime、backends、middlewares、tools、prompts、mcp、skills、guardrails 置于 backend workspace distribution `noesis-harness`。distribution 目录 SHALL 为 `packages/harness`，其唯一 Python 顶层包 SHALL 为 `noesis`。平台 Delivery 与 HTTP 编排 **SHALL NOT** 位于该包内。

#### Scenario: 评测可 import noesis

- **WHEN** 离线评测进程启动
- **THEN** SHALL 能 `from noesis.factory import create_noesis_agent`，且 **SHALL NOT** 需要启动 FastAPI 或预先绑定平台 deps 才能加载工厂

#### Scenario: LLM 与 Agent 内核同包

- **WHEN** Agent 或评测加载模型工厂
- **THEN** SHALL 从 `noesis.llm` 导入，且 SHALL NOT 存在平级 `packages/llm` distribution

### Requirement: 禁止 harness 反向依赖平台与能力实现

`noesis` 包源码 **SHALL NOT** 静态 import 上层平台包：`services`、`domain`、`models`、`api`、`kb`，也 SHALL NOT 依赖 backend 外层 `config` / `common`。需要附件存储、KB 检索、Langfuse、VLM 判定时，SHALL 经 `noesis.runtime.deps` 由宿主按运行场景绑定。

Agent 运行时配置与日志 SHALL 由 `noesis.config` / `noesis.runtime.logging` 提供。该允许列表不包含 FastAPI app、ORM model 或平台 service。

#### Scenario: 静态依赖检查

- **WHEN** 审查 `packages/harness/noesis/**/*.py`
- **THEN** AST 边界检查 SHALL 不存在对 `services` / `domain` / `models` / `api` / `kb` / 顶层 `config` / 顶层 `common` 的静态 import

#### Scenario: wheel 隔离导入

- **WHEN** 将构建出的 `noesis-harness` wheel 安装到 backend 源码目录外的新环境
- **THEN** SHALL 能导入 `noesis.factory` 与 `noesis.llm`，且不依赖 backend 源码出现在 `PYTHONPATH`

#### Scenario: 单一 import 权威路径

- **WHEN** 扫描 backend Python 源码
- **THEN** SHALL 不存在顶层 `agent.*` / `harness.*` / `llm.*` import 或转发 shim，仓内统一使用 `noesis.*`

### Requirement: 共享流式核

系统 SHALL 提供 `noesis.runtime.stream.stream_agent_events`，线上 BaseAgent 与评测/Harbor **SHALL** 复用该入口产出 LC/LG 事件 dict（含 HITL 哨兵）。

#### Scenario: Harbor 不旁路 stream

- **WHEN** Harbor `BaseAgent` 执行一轮
- **THEN** SHALL 调用 `stream_agent_events`（或等价委托），**SHALL NOT** 仅复制一份无 HITL 处理的裸 `astream_events` 循环作为长期权威路径

### Requirement: Agent 与 runtime 目录归属

Agent 场景、公共 middleware、backend adapter 与 runtime host SHALL 保持明确依赖方向。公共运行能力 SHALL 不依赖具体 Agent 场景；`agents.__init__` SHALL 使用 lazy 场景导出，使 `factory → agents.middlewares` 与场景模块按需导入 factory 可以共存，不得使用兼容 shim。具体物理布局与迁移路径由 design 规定。

#### Scenario: Package 依赖无环

- **WHEN** 独立导入 factory、公共运行能力与 Agent 场景
- **THEN** 导入 SHALL 成功，且 package lazy export SHALL NOT eager 导入所有场景实现
- **AND** 静态依赖检查 SHALL 不存在公共运行能力反向依赖具体场景

### Requirement: Harness 提供稳定的公共门面

宿主与评测常用的配置对象、路径函数、logger、共享 stream 和依赖绑定函数 SHALL 可直接从 `noesis.config` / `noesis.runtime` 导入，而无需依赖 `env.py`、`stream.py`、`deps.py` 等内部文件布局。公共门面 SHALL 避免在仅导入子系统时急切加载重型运行时或产生配置与日志循环依赖。

#### Scenario: 调用公共配置与运行时能力

- **WHEN** 外部调用方执行 `from noesis.config import ModelConfig, data_path` 以及 `from noesis.runtime import logger, stream_agent_events`
- **THEN** 导入 SHALL 成功，且导出的对象 SHALL 与其权威实现一致

#### Scenario: 子系统导入保持轻量

- **WHEN** 进程仅执行 `import noesis.config` 或 `import noesis.runtime`
- **THEN** SHALL NOT 因门面导出而立即加载全部配置、LangGraph stream 或平台 wiring

### Requirement: 平台宿主使用单一 Python 命名空间

Web API、应用服务、平台领域逻辑、数据库、KB、ORM、Schema、中间件与平台公共模块 SHALL 位于 `backend/noesis_server`，并使用 `noesis_server.*` 导入。backend 根目录 SHALL NOT 并列保留这些旧顶层 Python package 或兼容 shim。`evals`、`packages/harness`、Alembic/SQL 工具与启动脚本不属于平台 package，保持独立。

#### Scenario: backend 顶层目录扫描

- **WHEN** 扫描 backend 根目录
- **THEN** SHALL 不存在顶层 `api` / `services` / `domain` / `config` / `common` / `constants` / `exceptions` / `middleware` / `models` / `schemas` / `kb` Python package

#### Scenario: 平台 import 权威路径

- **WHEN** 扫描 backend 与测试 Python 源码
- **THEN** 平台模块 SHALL 从 `noesis_server.*` 导入，且 SHALL 不存在旧顶层平台 package import

### Requirement: 平台内部依赖方向保持单向

平台 SHALL 遵循 API → application services → domain / KB / harness 的依赖方向。进程启动与外部通道轮询属于 bootstrap/application。domain 和 KB 核心 SHALL NOT 静态 import application services；通用模块 SHALL NOT 承担服务启动编排。

#### Scenario: 平台边界静态检查

- **WHEN** AST 扫描 `noesis_server/domain`、`noesis_server/kb` 与 `noesis_server/common`
- **THEN** domain/KB SHALL 不 import `noesis_server.services`，common SHALL 不 import services/domain/KB/harness

#### Scenario: QA 服务单一入口

- **WHEN** 调用 QA 应用服务
- **THEN** SHALL 使用 `noesis_server.services.qa`，且 SHALL 不存在重新导出私有 helper 的 `qa_service.py` 兼容入口

#### Scenario: Knowledge Base API 不直连基础设施

- **WHEN** AST 扫描 `noesis_server/api/knowledge_base_api.py`
- **THEN** 该模块 SHALL 通过 `noesis_server.services.knowledge_base_service` 编排，且 SHALL NOT 直接 import `noesis_server.kb`、Qdrant client 或集合配置 service

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
| `COMMON_QA` | DynamicContext、ToolResultBudget、Compaction、PatchToolCalls、ToolFailure | Snip、DeferredToolFilter、manual compact、call limits |
| `SUPER_AGENT_QA` | COMMON 全部 + ReadBeforeWrite、DurableContext、Filesystem、RefreshingSkills、RefreshingMemory、Todo、SubAgent、DeferredToolFilter | Snip、manual compact、HITL、call limits |
| `FAULT_OPERATION_QA` | DynamicContext、DurableContext、ToolResultBudget、Compaction、PatchToolCalls、ToolFailure、SubAgent、DeferredToolFilter | ReadBeforeWrite、Snip、HITL、call limits |
| SimpleMCP | DynamicContext、ToolResultBudget、Compaction、DeferredToolFilter、PatchToolCalls、ToolFailure | Snip、manual compact、call limits |
| Super/Fault 子 Agent | DeepAgents isolated task context、ToolResultBudget、Compaction、PatchToolCalls、ToolFailure | ReadBeforeWrite、RefreshingSkills、Filesystem、DeferredToolFilter、Snip、局部 call limits；fork/resume 待上游公开 state-builder hook |

#### Scenario: 未配置能力不出现

- **WHEN** Profile 没有 HITL、retry、model limit 或 tool limit 配置
- **THEN** 最终 stack SHALL 不包含对应能力
- **AND** inventory SHALL 不把潜在能力描述为已生效

#### Scenario: Context 能看到最终输入

- **WHEN** SuperAgent 启用 Skills、Memory、SubAgent、Todo 与 HITL
- **THEN** compaction 与最终预算 SHALL 观察到这些能力处理后的 system instructions 与 tool definitions
- **AND** exact hook order SHALL 由装配契约测试固定

### Requirement: Skills 与 Memory SHALL 使用上游解析与独立 Freshness Adapter

Skills 与 Memory SHALL 直接使用选定 DeepAgents 版本的来源解析、private state 与 prompt 注入行为。Noesis SHALL NOT 维护第二套 parser 或 prompt 模板；`RefreshingSkillsMiddleware` 和 `RefreshingMemoryMiddleware` SHALL 分别补足 DeepAgents 默认只加载一次的 freshness 差异，系统 SHALL NOT 建立统一 source hash middleware。

#### Scenario: Skills 来源变化

- **WHEN** 用户安装、删除或启停 Skill
- **THEN** 后续顶层 turn SHALL 生成新 source revision 并定向刷新上游 Skills state
- **AND** Noesis SHALL NOT 自行解析 Skill 正文或维护另一份 Skills metadata

#### Scenario: Memory 使用上游生命周期

- **WHEN** Agent 使用配置的 memory 路径
- **THEN** 加载与注入 SHALL 由 DeepAgents MemoryMiddleware 完成
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

