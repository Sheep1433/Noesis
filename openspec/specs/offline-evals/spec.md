# offline-evals Specification

## Purpose

本能力索引 Noesis **离线评测**入口：`evals.agent`（Agent benchmark / Harbor）、`evals.case`（测试用例两阶段 promptfoo）、`evals.compression`（消息摘要压缩）、`evals.kb`（单集合检索）。在线 chat 与 CaseCoordinator 产品行为见 `agent-profiles` / `platform-chat`。

## Requirements

### Requirement: Agent 离线评测

`evals.agent` SHALL 提供可重复的 Agent benchmark 入口（含 Harbor 等工作负载，以仓库 README 为准）。评测装配 SHALL 使用与线上一致的 SuperAgent（或文档声明的 profile），**SHALL NOT** 依赖已删除的 `DeepResearchAgent` 作为默认被测对象。

#### Scenario: 文档可跑

- **WHEN** 按 `backend/evals/README.md` 执行示例命令
- **THEN** SHALL 能启动评测进程并产出结果目录或报告

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

单集合 KB 评测入口与 `knowledge-base` 中评测 Requirement 一致；本能力仅要求在 evals 索引中可发现，避免与在线检索 API 混淆。

#### Scenario: 索引存在

- **WHEN** 开发者打开 `backend/evals/` 文档
- **THEN** SHALL 能找到 kb / case / agent / compression 各类入口说明
