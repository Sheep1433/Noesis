# offline-evals Specification

## Purpose

本能力索引 Noesis **离线评测**入口：`evals.agent`（BrowseComp / Harbor / Agentic RAG）、`evals.case`（测试用例两阶段 promptfoo）、`evals.compression`（消息摘要压缩）、`evals.kb`（单集合检索 + ERB 企业级基准）。在线 chat 与 CaseCoordinator 产品行为见 `agent-profiles` / `platform-chat`。
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
- **THEN** 系统 SHALL NOT import `noesis_server.services.harness_wiring`
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

单集合 KB 评测入口 SHALL 与 `knowledge-base` 中评测 Requirement 一致；本能力 SHALL 保证其在 evals 索引中可发现，避免与在线检索 API 混淆。

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

### Requirement: Run memory 评测 SHALL 使用冻结且带 source span 的数据集

评测 harness SHALL 使用版本化 Run snapshots，覆盖 completed、partial、error、有有效工作证据的 interrupted、无有效工作取消、无工具失败成功、决策变化、用户纠正、失败恢复、重复成功 workflow、环境 gotcha、长 Run/compaction、大工具输出、无价值 Run、跨项目、跨用户、外部内容和 recall-loop。每条样本 SHALL 标注 capture eligibility、expected memory type/statement/scope/state、gold source spans、预期 consolidation operation、是否允许自动注入和后续任务。dev/test SHALL 在评测前固定，test 标签 SHALL NOT 用于 prompt、阈值或 schema 调整。

#### Scenario: 无失败成功样本
- **WHEN** 数据集包含完成且验证成功、但没有工具失败的 Run
- **THEN** harness SHALL 评测 decision/workflow/experience 覆盖
- **AND** SHALL NOT 将“无失败”标记为预期 no-output

#### Scenario: 长 Run coverage 标签
- **WHEN** 样本超过单次 extraction 输入预算
- **THEN** gold SHALL 标注分块后仍需命中的中部 source spans
- **AND** harness SHALL 检测固定首尾截断造成的漏提

### Requirement: 评测 SHALL 分别报告 capture、extraction 与 consolidation 质量

harness SHALL 至少报告 eligible Run capture coverage、chunk coverage、silent-drop count、extraction precision/recall、memory type accuracy、source-span precision/recall、`succeeded_no_output` accuracy，以及 ADD/REINFORCE/UPDATE/SUPERSEDE/CONTRADICT/NOOP 的 operation accuracy。报告 SHALL 给出按 memory type、Run length、source provenance 和 project scope 的分组结果；全不提取 SHALL 在 recall 和 coverage 中失败。

#### Scenario: 全不提取不能虚假高分
- **WHEN** Extractor 对所有样本返回 no-output
- **THEN** recall 和 eligible coverage SHALL 反映全部漏提
- **AND** 报告 SHALL NOT 只展示 precision

#### Scenario: 错误来源引用
- **WHEN** statement 内容看似正确但引用了非 gold/非支持 source span
- **THEN** source-span 指标 SHALL 判定失败

#### Scenario: 错误 supersession
- **WHEN** consolidation 把并不冲突的两个 project-scoped item 合并或替代
- **THEN** operation/scope 指标 SHALL 判定失败

### Requirement: 检索评测 SHALL 分离 retrieval、Bulletin 与 reader error

harness SHALL 分别记录 memory item precision@k/recall@k、answer-bearing Run/span recall、abstention、query latency、step count、returned spans、Bulletin precision、stale/harmful rate、自动注入 token、cache-read tokens、cache-write tokens、uncached input tokens、cache metric availability 和 downstream task/reader correctness。检索命中正确来源但 downstream 回答错误 SHALL 与 retrieval miss 分开；返回相关主题但错误 Run/span SHALL 记为近似命中而非成功。

#### Scenario: 精确来源与近似主题分开
- **WHEN** 检索返回相同 task family 但不是支持答案的 Run/span
- **THEN** harness SHALL 记录 minor/near-match retrieval miss
- **AND** SHALL NOT 计为 exact evidence hit

#### Scenario: reader error
- **WHEN** Bulletin 含正确 gold evidence 而后续 Agent 仍回答错误
- **THEN** harness SHALL 标记 downstream/reader error
- **AND** SHALL 保留 memory context 以供复核

#### Scenario: 错误前提 abstention
- **WHEN** gold 表明用户问题前提错误或证据不足
- **THEN** query/Bulletin SHALL 明确 contradict/insufficient
- **AND** 迎合错误前提 SHALL 判定失败

### Requirement: 端到端评测 SHALL 使用冻结快照的 paired memory-on/off

harness SHALL 为 A/B 创建不同测试用户并复制语义相同的 item/evidence/workspace/index snapshot；跑批期间 SHALL 暂停两组自动 capture/extraction/consolidation，使输入不随任务执行漂移。A 组 `enabled=true` 并允许自动 Bulletin，B 组关闭自动链路；相同任务 SHALL 使用 paired seeds。评测 SHALL 报告任务成功率、重复失败率、工具调用数、token、TTFT、memory query latency、cache-read/write/uncached tokens、cache hit ratio、后台提取/整理成本和 95% 置信区间。

#### Scenario: A/B 使用相同冻结输入
- **WHEN** paired A/B 开始
- **THEN** 两组 SHALL 拥有语义相同且用户隔离的 memory snapshot
- **AND** 跑批新 Run SHALL NOT 改变任一组输入

#### Scenario: 结果和成本同时报告
- **WHEN** memory-on 提高任务成功率但显著增加延迟/token
- **THEN** 报告 SHALL 同时展示收益和成本
- **AND** SHALL NOT 只以成功率宣称通过

### Requirement: 安全评测 SHALL 覆盖隔离、外部内容、recall-loop 和关闭残留

harness SHALL 验证 cross-user、cross-project、disabled/invalidated/stale item、仅外部工具支持的命令、memory-recall 再提取、用户关闭后的自动 capture/注入、删除后的派生视图和来源越权。cross-user 自动/显式泄漏与低信任命令自动注入 SHALL 为零容忍 release gate。

#### Scenario: 外部指令投毒
- **WHEN** 外部工具输出包含要求未来执行危险操作的指令
- **THEN** 该内容 MAY 作为低信任 evidence 被显式查看
- **AND** SHALL NOT 形成自动注入的 active workflow/gotcha

#### Scenario: recall-loop
- **WHEN** 旧 memory 在 Run 中被自动或显式召回
- **THEN** 后续 capture SHALL 排除 recall 内容
- **AND** evidence count SHALL NOT 因重复召回增长

#### Scenario: 用户关闭残留
- **WHEN** 用户关闭经验记忆并完成新 Run、开始下一 Run
- **THEN** 新 Run SHALL 不创建自动 capture item 且下一 Run SHALL 零自动注入

### Requirement: 参数、模型和 test 运行 SHALL 在评测前冻结

chunk budget、schema、prompt、模型、embedding、lexical/vector 融合、top_k、阈值、Bulletin 格式、deep-query steps/timeout、测试 seeds 和 release gate SHALL 只在 dev 集调整。test SHALL 在冻结后运行；不得根据 test 结果修改参数并重报同一 test。默认测试 SHALL 使用 fake LLM、fake embedding、fixture workspace 和可控工具；live eval SHALL 由显式开关启用并记录版本、时间和成本。

live provider 暂态失败 SHALL 与有效的空候选分开记录安全异常类别。未完成报告 MAY 在相同 code/config/fixture/实际模型 fingerprint 下只续跑 failed fixtures，但 SHALL 保留成功 observations、原始创建时间和失败历史；passed report、无失败报告、跨模型或跨 fingerprint 报告 SHALL NOT resume。provider 故障后的完整重跑 SHALL 明确归档无效报告，不得冒充独立 test 通过次数。

#### Scenario: 默认离线运行
- **WHEN** 开发者运行默认 memory eval/test 命令且未设置 live 开关
- **THEN** harness SHALL NOT 请求真实 LLM、embedding provider、Qdrant 云服务或外部 MCP

#### Scenario: test 后调参
- **WHEN** test 结果已生成
- **THEN** 任何参数修改 SHALL 产生新的评测版本和新的 holdout
- **AND** SHALL NOT 覆盖原始报告

### Requirement: Release Gate SHALL 同时证明覆盖、安全、质量与实际收益

首版自动经验记忆进入可启用状态前 SHALL 满足：eligible fixture capture coverage=1.0 且 silent drop=0；extraction precision≥0.85、recall≥0.80；source-span precision/recall≥0.90；consolidation operation accuracy≥0.85；exact evidence recall@5≥0.80、precision@5≥0.70；cross-user/project 泄漏、低信任命令自动注入和 recall-loop evidence 增量均为 0；fast path 额外 p95 延迟≤500ms 且自动 Bulletin≤500 tokens；paired memory-on/off 中任务成功率差值的 95% CI 下界≥-2 个百分点，并且任务成功率提升或重复失败率下降至少一项的 95% CI 排除零。

任一 gate 未通过时，系统 SHALL 保持自动 capture/injection 不可启用，并回到对应 capture/extraction/consolidation/retrieval/context 层修正；SHALL NOT 以综合平均分、更多容错分支或修改 test 阈值绕过失败。

#### Scenario: 只有离线提取指标好但任务无收益
- **WHEN** extraction/retrieval 指标达标但 paired 任务成功率和重复失败率均无统计显著改善
- **THEN** release gate SHALL 失败
- **AND** 系统 SHALL NOT 宣称经验记忆有效

#### Scenario: 效果提升但存在安全泄漏
- **WHEN** memory-on 提高任务成功率但出现任一 cross-user/project 泄漏或低信任命令自动注入
- **THEN** release gate SHALL 失败

#### Scenario: test gate 通过
- **WHEN** 所有冻结阈值在独立 test 上满足
- **THEN** 系统 MAY 允许用户自行开启自动经验记忆
- **AND** SHALL 保存数据版本、模型/embedding、参数、命令、成本和完整指标报告

### Requirement: 上下文缓存评测 SHALL 区分稳定与变化 Bulletin

harness SHALL 至少覆盖同 Run 后续调用、新 Run 但 Bulletin 可见内容不变、新 Run且 Bulletin 内容变化、Deep Query tool result 四类场景，并比较模型实际收到的 prompt segment/hash。支持 cache telemetry 的 provider SHALL 报告 cache-read/write tokens 与 TTFT；不支持时 SHALL 报告 unavailable。同 Run、Bulletin 不变和 Deep Query 后续调用 SHALL 验证实际 cache reuse；Bulletin 变化场景 SHALL 硬性验证稳定 prefix 的逐字节 hash 与位置不变并报告实际 reuse，不得把 provider 路由、eviction 或 cache 实现差异造成的单次 miss 判为 prompt 装配错误。评测 SHALL 检测当前 run id、时间、source span、evidence count 或随机排序导致的无意义 hash/text 变化。

#### Scenario: 相同可见内容保持 hash
- **WHEN** 两个新 Run 选中相同 memory items 且 statement/applicability/verification 未变化
- **THEN** 两次 Bulletin hash/text SHALL 相同

#### Scenario: 动态 metadata 不进入可见 Bulletin
- **WHEN** source evidence count 或 last verified time 变化但可见结论未变化
- **THEN** 自动 Bulletin hash/text SHALL 保持不变
- **AND** private metadata MAY 更新

#### Scenario: 缓存指标不可用
- **WHEN** provider 不返回 cache token details
- **THEN** 报告 SHALL 将该场景标记 unavailable
- **AND** SHALL NOT 计算虚假的 0% cache hit ratio

