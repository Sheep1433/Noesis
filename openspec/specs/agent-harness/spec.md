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

具体 Agent 实现 SHALL 位于 `noesis.agents`。测试用例生成 Agent SHALL 位于 `noesis.agents.case_generate`。stream、HITL、宿主依赖端口、附件输入适配 SHALL 位于 `noesis.runtime`。

#### Scenario: 顶层目录扫描

- **WHEN** 扫描 `packages/harness/noesis` 顶层
- **THEN** SHALL 不存在 `profiles` / `case_generate` / `attachments` 目录或顶层 `stream.py` / `hitl.py` / `deps.py`

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

`noesis.factory` SHALL 将公共 runtime kernel 与 Agent Profile capability 分开装配。公共 kernel SHALL 由 `agent-runtime`（运行时执行 Lifecycle）定义；Filesystem、SubAgent、Skills、Memory、Attachments、Todo 与 HITL SHALL 作为 Profile capability 按需选择，SHALL NOT 被统计或实现为所有 Agent 必须加载的公共 guard。

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

