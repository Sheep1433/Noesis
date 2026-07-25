# agent-harness Specification

## Purpose
TBD - created by archiving change extract-noesis-harness. Update Purpose after archive.
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

- **WHEN** Harbor worker 执行一轮
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
