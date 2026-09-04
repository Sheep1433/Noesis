# offline-evals Specification

## Purpose

本能力索引 Noesis **离线评测**入口：`evals.agent`（BrowseComp / Harbor / Agentic RAG / 记忆应召回）、`evals.case`（测试用例两阶段 promptfoo）、`evals.compression`（消息摘要压缩）、`evals.kb`（单集合检索 + ERB 企业级基准）。在线 chat 与 CaseCoordinator 产品行为见 `agent-profiles` / `platform-chat`。
## Requirements
### Requirement: Agent 离线评测经 harness

`evals.agent`（含 BrowseComp、Harbor 和 Agentic RAG）SHALL 通过 `noesis` 包的 Agent Profile 或工厂与流式核执行被测 Agent。Harbor 自定义 Agent SHALL 基于官方 `harbor.agents.base.BaseAgent` 生命周期直接运行，不得为加载 Harness 额外维护 Worker 或进程间代理。评测专用 backend、prompt、collector 和 benchmark adapter 可以注入。各 Agent benchmark SHALL 共用最小事件结果模型，专用报告可以在该结果之上扩展。

#### Scenario: Harbor 使用 harness factory

- **WHEN** Harbor 调用 `NoesisHarborAgent.run()`
- **THEN** 系统 SHALL `from noesis.factory import create_noesis_agent` 并经 `noesis.runtime.stream.stream_agent_events` 消费事件
- **AND** SHALL 直接使用 Harbor 传入的 `BaseEnvironment`，不得启动 Noesis Worker 或 TCP 环境代理
- **AND** 公共完成状态、文本、工具统计、usage 和错误 SHALL 由共享 Agent event collector 生成

#### Scenario: BrowseComp 使用 SuperAgent Profile

- **WHEN** 运行 BrowseComp
- **THEN** 系统 SHALL 通过 `noesis.agents.super_agent.SuperAgent` 执行题目
- **AND** SHALL 使用与 Harbor 相同的公共 Agent event result

#### Scenario: Harbor 不加载平台 wiring

- **WHEN** Harbor `BaseAgent` 初始化内存 checkpointer 并执行无 KB/附件工具的 Agent
- **THEN** 系统 SHALL NOT import `server.wiring` 或平台 service 模块
- **AND** SHALL NOT 初始化平台 KB、附件或 ORM 服务

#### Scenario: 评测覆盖使用公开 Harness API

- **WHEN** 评测临时替换 checkpointer、KB dependency 或显式 provider/model
- **THEN** 系统 SHALL 使用 `noesis` 公开上下文或模型构建 API
- **AND** SHALL NOT 直接修改 `_saver` 或调用 `_build_chat_model`

### Requirement: Agentic RAG 评测经 Harness Tool 链路

系统 SHALL 在保留 `evals.kb` 纯检索评测的同时，提供独立 Agentic RAG 入口，经 `GeneralQAAgent`、Harness KB Tool、runtime dependency binding 和平台 retrieval adapter 执行样本。

#### Scenario: 执行 Agentic RAG 样本

- **WHEN** 开发者使用包含 query、collection scope 和期望来源的 JSONL 数据集运行 Agentic RAG 评测
- **THEN** 每个样本 SHALL 经 `GeneralQAAgent` 和 `stream_agent_events` 执行
- **AND** 结果 SHALL 至少记录完成状态、KB Tool 是否调用、期望来源命中、最终回答、耗时和错误

#### Scenario: 默认测试不访问外部服务

- **WHEN** 运行默认 pytest
- **THEN** Agentic RAG collector、scoring 和 dependency binding SHALL 使用 fake event 或 fake service 验证
- **AND** SHALL NOT 要求真实 LLM、PostgreSQL 或 Qdrant

### Requirement: Case 展示评测维持可运行

系统 SHALL 修复测试用例 RAG provider 对已删除模块路径的依赖，但不要求 Case 评测迁移到 Agent benchmark 公共 runner。

#### Scenario: Case RAG 使用当前 Harness 模块

- **WHEN** Case RAG provider 覆盖评测 collection 配置
- **THEN** patch 目标 SHALL 指向 `noesis.agents.case_generate.rag`
- **AND** provider SHALL 能继续调用当前 Case RAG 构建函数

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

KB 检索评测入口（`evals.kb.erb`，ERB 企业级基准）SHALL 与 `knowledge-base` 中评测 Requirement 一致；本能力 SHALL 保证其在 evals 索引中可发现，避免与在线检索 API 混淆。

#### Scenario: 索引存在

- **WHEN** 开发者打开 `backend/evals/` 文档
- **THEN** SHALL 能找到 kb / case / agent / compression 各类入口说明

### Requirement: ERB 企业级检索基准

`evals.kb.erb` SHALL 对 `erb-eval` 集合执行 EnterpriseRAG-Bench 子集评测：正样本题（GT 文档全部在语料内）SHALL 输出 Recall 与 GT 命中排名，负样本（info_not_found）SHALL 输出各阈值档位的拒答/误检判定。评测 SHALL 单次检索记录原始 rerank 分并离线模拟阈值，SHALL NOT 为同一题重复调用检索 API。数据集与语料清单 SHALL 位于 gitignored 目录，经 `ERB_DATA_DIR` 可覆盖。

#### Scenario: 抽样冒烟

- **WHEN** 运行 `uv run python -m evals.kb.erb --sample 2`
- **THEN** SHALL 各抽一道正样本与负样本，打印 top-10、GT 命中标记与阈值模拟结果

#### Scenario: GT 不在语料的题不参与正样本评测

- **WHEN** 题目的 `expected_doc_ids` 存在未入库文档（如 gmail 来源）
- **THEN** 该题 SHALL 被排除出正样本集（检索不到是正确行为，不应计为 miss）

### Requirement: 记忆应召回评测

`evals.agent.memory` SHALL 以「应召回场景」评测记忆召回行为：harness SHALL 幂等写入版本化种子条目（经 `MemoryStore.upsert_entry`），对每个场景运行 SuperAgent 并断言 Agent 经 `search_memory` 主动检索到目标条目，记录完成状态、命中条目、耗时与错误。评测用户 SHALL 与真实用户隔离。

#### Scenario: 应召回场景命中

- **WHEN** 场景 prompt 涉及某条种子记忆的适用条件
- **THEN** harness SHALL 断言该 run 调用过 `search_memory` 且目标条目进入召回结果
- **AND** Agent SHALL 依据召回内容组织回答而非凭空编造

#### Scenario: 种子幂等

- **WHEN** 同一评测用户重复运行
- **THEN** 种子写入 SHALL 不产生重复条目

