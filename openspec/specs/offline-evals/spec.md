# offline-evals Specification

## Purpose

本能力索引 Noesis **离线评测**入口：`evals.agent`（Agent benchmark / Harbor）、`evals.case`（测试用例两阶段 promptfoo）、`evals.compression`（消息摘要压缩）、`evals.kb`（单集合检索）。在线 chat 与 CaseCoordinator 产品行为见 `agent-profiles` / `platform-chat`。

## Requirements

### Requirement: Agent 离线评测经 harness

`evals.agent`（含 Harbor）SHALL 通过 `noesis` 包的工厂与流式核执行被测 Agent。**SHALL NOT** 在 worker 内长期维护与线上分叉的完整 middleware/factory 装配副本（允许注入评测专用 backend / prompt / collector）。

#### Scenario: Harbor 使用 harness factory

- **WHEN** 运行 Harbor `noesis_worker`
- **THEN** SHALL `from noesis.factory import create_noesis_agent` 并经 `noesis.runtime.stream.stream_agent_events` 消费事件

#### Scenario: Harbor 不加载平台 wiring

- **WHEN** Harbor worker 初始化内存 checkpointer 并执行无 KB/附件工具的 Agent
- **THEN** SHALL NOT import `noesis_server.services.harness_wiring`，也 SHALL NOT 初始化平台 KB、附件或 ORM 服务

### Requirement: 测试用例两阶段评测

`evals.case` SHALL 支持 promptfoo 两阶段（如 RAG / 生成）评测测试用例 Agent；配置与数据集路径 SHALL 可发现。

#### Scenario: phase 可选

- **WHEN** 指定 phase 运行
- **THEN** 仅该 phase 的用例 SHALL 执行（或按文档跳过其它 phase）

### Requirement: 消息压缩评测

`evals.compression` SHALL 能对摘要/压缩策略跑离线对比或回归。

#### Scenario: 产出指标

- **WHEN** 运行 compression 评测
- **THEN** SHALL 产出可读指标或对比输出

### Requirement: KB 检索评测指针

单集合 KB 评测入口 SHALL 与 `knowledge-base` 中评测 Requirement 一致；本能力 SHALL 保证其在 evals 索引中可发现，避免与在线检索 API 混淆。

#### Scenario: 索引存在

- **WHEN** 开发者打开 `backend/evals/` 文档
- **THEN** SHALL 能找到 kb / case / agent / compression 各类入口说明
