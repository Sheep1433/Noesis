# offline-evals Specification

## Purpose

本能力索引 Noesis **离线评测**入口：`evals.agent`（DeepResearch / Harbor / Agentic RAG / 记忆应召回）、`evals.compression`（消息摘要压缩）、`evals.kb`（单集合检索 + ERB 企业级基准）。在线 chat 产品行为见 `agent-profiles` / `platform-chat`。
## Requirements
### Requirement: Agent 离线评测经 harness

`evals.agent`（含 DeepResearch、Harbor 和 Agentic RAG）SHALL 通过 `noesis` 包的 Agent Profile 或工厂与流式核执行被测 Agent。Harbor 自定义 Agent SHALL 基于官方 `harbor.agents.base.BaseAgent` 生命周期直接运行，不得为加载 Harness 额外维护 Worker 或进程间代理。评测专用 backend、prompt、collector 和 benchmark adapter 可以注入。各 Agent benchmark SHALL 共用最小事件结果模型，专用报告可以在该结果之上扩展。

#### Scenario: Harbor 使用 harness factory

- **WHEN** Harbor 调用 `NoesisHarborAgent.run()`
- **THEN** 系统 SHALL `from noesis.factory import create_noesis_agent` 并经 `noesis.runtime.stream.stream_agent_events` 消费事件
- **AND** SHALL 直接使用 Harbor 传入的 `BaseEnvironment`，不得启动 Noesis Worker 或 TCP 环境代理
- **AND** 公共完成状态、文本、工具统计、usage 和错误 SHALL 由共享 Agent event collector 生成

#### Scenario: DeepResearch 使用 SuperAgent Profile

- **WHEN** 运行 DeepResearch Bench 子集
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

### Requirement: 消息压缩评测

`evals.compression` SHALL 以「压缩后任务保持率」为 headline 评测消息压缩：评测 SHALL 将真实长会话导出并脱敏为 transcript fixture，由 LLM 从将被压缩的区域生成事实 recall 题库（按 transcript 内容缓存以保证可复现），对每个评测臂（压缩策略档）仅凭压缩后上下文闭卷作答，judge 按 2/1/0（正确/部分/错误）判卷，headline 指标 SHALL 为 recall% @ retained tokens。评测 SHALL 包含 uncompacted 对照臂（不压缩直接闭卷作答，作为 recall 上限），并 SHALL 报告任务保持率 Δ（压缩臂 recall% − uncompacted 臂 recall%）。多策略档 SHALL 经参数化的压缩配置生效。judge 模型 SHALL 与摘要及作答模型分离；judge 解析失败 SHALL 重试后剔除并单列失败率，SHALL NOT 以 0 分计入 recall%。摘要识别 SHALL 依赖压缩中间件写入的结构化标记，SHALL NOT 依赖内容启发式猜测。可种植事实的合成 fixture SHALL 保留为零 LLM 冒烟档。

评测 SHALL 支持 `recovery` 臂：作答模型在压缩后上下文基础上额外获得会话历史检索工具（对 fixture 原文建进程内 trigram 索引，工具 schema 与产品一致），其余条件与压缩臂完全相同。`recovery` 臂与闭卷压缩臂的 recall% 差值 SHALL 报告为「检索兜底收益」。recovery 臂作答 SHALL 限制为检索结果片段（top-k、有长度上限），SHALL NOT 允许读入全量 transcript。

#### Scenario: headline 指标口径

- **WHEN** 压缩评测完成运行
- **THEN** summary SHALL 以 recall% @ retained tokens 为 headline 指标
- **AND** SHALL 同时报告 uncompacted 臂 recall% 与任务保持率 Δ

#### Scenario: uncompacted 对照臂

- **WHEN** 以默认评测臂运行
- **THEN** uncompacted 臂 SHALL 跳过压缩、以同一题库闭卷作答并判卷
- **AND** 其 recall% SHALL 作为该 fixture 的 recall 上限呈现

#### Scenario: recovery 臂对照

- **WHEN** 以含 recovery 的评测臂运行
- **THEN** recovery 臂 SHALL 与闭卷压缩臂同题同判卷，唯一差异为作答模型可用会话检索工具
- **AND** summary SHALL 单列「检索兜底收益」（recovery recall% − 闭卷压缩臂 recall%）
- **AND** recovery 臂的作答上下文 SHALL NOT 包含全量 fixture 原文

#### Scenario: judge 解析失败不污染分数

- **WHEN** judge 输出无法解析为 2/1/0 判分
- **THEN** 该题 SHALL 重试一次，仍失败则剔除出 recall% 分母
- **AND** summary SHALL 单列 judge 解析失败率

#### Scenario: 零 LLM 冒烟

- **WHEN** 使用可种植事实的合成 fixture 运行
- **THEN** 评测 SHALL 不调用任何 LLM 即完成压缩与事实存活断言

### Requirement: KB 检索评测指针

KB 检索评测入口（`evals.kb.erb`，ERB 企业级基准）SHALL 与 `knowledge-base` 中评测 Requirement 一致；本能力 SHALL 保证其在 evals 索引中可发现，避免与在线检索 API 混淆。

#### Scenario: 索引存在

- **WHEN** 开发者打开 `backend/evals/` 文档
- **THEN** SHALL 能找到 kb / agent / compression 各类入口说明

### Requirement: ERB 企业级检索基准

`evals.kb.erb` SHALL 对 `erb-eval` 集合执行 EnterpriseRAG-Bench 子集评测：正样本题（GT 文档全部在语料内）SHALL 输出 Recall@K（K=1/3/5/10）、MRR（未命中题记 0 计入分母）、nDCG@10（多 GT 文档折算）与 GT 命中排名，并 SHALL 对 headline 指标给出固定种子的 bootstrap 95% 置信区间；负样本（info_not_found）SHALL 输出各阈值档位的拒答/误检判定，阈值模拟的 GT 存活窗口 SHALL 可参数化且默认与 Recall 口径一致。评测 SHALL 单次检索记录原始 rerank 分并离线模拟阈值，SHALL NOT 为同一题重复调用检索 API。评测 SHALL 将结果落盘至 `results/<tag>/`（manifest、逐题原始分、summary），SHALL NOT 仅输出 stdout。数据集与语料清单 SHALL 位于 gitignored 目录，经 `ERB_DATA_DIR` 可覆盖。

#### Scenario: 抽样冒烟

- **WHEN** 运行 `uv run python -m evals.kb.erb --sample 2`
- **THEN** SHALL 各抽一道正样本与负样本，打印 top-10、GT 命中标记与阈值模拟结果
- **AND** SHALL 落盘包含 manifest 与逐题原始分的评测产物

#### Scenario: GT 不在语料的题不参与正样本评测

- **WHEN** 题目的 `expected_doc_ids` 存在未入库文档（如 gmail 来源）
- **THEN** 该题 SHALL 被排除出正样本集（检索不到是正确行为，不应计为 miss）

#### Scenario: MRR 计入未命中题

- **WHEN** 正样本集中存在 GT 未进 top-10 的题
- **THEN** MRR 的分母 SHALL 为全部正样本题数，未命中题贡献 0

### Requirement: 记忆应召回评测

`evals.agent.memory` SHALL 以「应召回场景」评测记忆召回行为：harness SHALL 幂等写入版本化种子条目（经 `MemoryStore.upsert_entry`），对每个场景运行 SuperAgent 并断言 Agent 经 `search_memory` 主动检索到目标条目，记录完成状态、命中条目、耗时与错误。评测用户 SHALL 与真实用户隔离。

#### Scenario: 应召回场景命中

- **WHEN** 场景 prompt 涉及某条种子记忆的适用条件
- **THEN** harness SHALL 断言该 run 调用过 `search_memory` 且目标条目进入召回结果
- **AND** Agent SHALL 依据召回内容组织回答而非凭空编造

#### Scenario: 种子幂等

- **WHEN** 同一评测用户重复运行
- **THEN** 种子写入 SHALL 不产生重复条目

### Requirement: 统一评测基建（manifest、落盘与 judge 分离）

所有离线评测线（`evals.kb.erb` / `evals.agent.rag` / `evals.compression` / `evals.agent.memory`）SHALL 产出统一结构的评测产物：`results/<tag>/` 下至少包含 `manifest.json`、逐题原始记录（`raw.json` 或 `raw.jsonl`）、`summary.json` 与 `summary.md`。manifest SHALL 记录评测线标识、tag、时间、被测模型、judge 模型（如有）、embedding 与 rerank 模型（如适用）、数据集路径与题数与随机种子、关键配置快照、token 消耗与估算费用、git sha。LLM-as-judge 的评测线 SHALL 经独立的 judge 模型入口取模型，且 runner SHALL 校验 judge 模型与被测模型不同，相同时拒绝运行。含 LLM-as-judge 的评测线 SHALL 落盘 10% 人工抽检样本清单（`manual_review_queue.json`）。评测参数与题库的调整 SHALL 产生新 tag 运行，SHALL NOT 覆盖已有 tag 的历史结果。

#### Scenario: 产物四件套齐备

- **WHEN** 任一评测线以 `--tag baseline` 完成运行
- **THEN** `results/baseline/` SHALL 包含 manifest.json、逐题原始记录、summary.json 与 summary.md
- **AND** manifest SHALL 含被测模型、judge 模型、数据集题数、随机种子、token 消耗与 git sha

#### Scenario: judge 与被测模型相同被拒绝

- **WHEN** 运行含 LLM-as-judge 的评测线且 `--judge-model-id` 与被测 `--model-id` 相同
- **THEN** runner SHALL 报错退出且不产生评测结果

#### Scenario: 历史 tag 不被覆盖

- **WHEN** 以已存在的 tag 再次运行评测且未指定续跑参数
- **THEN** 系统 SHALL 拒绝写入并要求换新 tag
- **AND** 已有 tag 的历史结果 SHALL 保持不变
- **AND** 显式 `--resume` / `--retry-failed` SHALL 为续跑例外：复用同 tag 只追加逐题记录，SHALL NOT 改写已有记录与 manifest 之外的产物

### Requirement: 引用溯源评测

系统 SHALL 提供引用溯源评测：复用 ERB 数据集的 `answer_facts` 与 `gold_answer`，对 GeneralQAAgent 全链路产出的最终回答计算三项指标——引用格式遵循率（citation 契约解析成功率，确定性）、引用正确率（引用文档属于该题 `expected_doc_ids` 的比例，确定性）、事实可溯源率（逐条 answer_fact 是否在引用 chunk 中有支撑，LLM-as-judge）。引用评测 SHALL 与 Agent E2E 判卷共享同一次 agent run，SHALL NOT 为引用指标额外运行 agent。

#### Scenario: 同一次 run 产出引用三指标

- **WHEN** 运行 Agent E2E 评测
- **THEN** 每题结果 SHALL 同时包含引用格式遵循率、引用正确率与事实可溯源率的判定输入与得分

#### Scenario: 已知引用失败模式回归

- **WHEN** 被评回答包含伪协议头引用（如 `file:` 代替 `kb:`）或张冠李戴引用
- **THEN** 引用格式遵循率或引用正确率 SHALL 判定该题失败
- **AND** 该失败 SHALL 在逐题原始记录中标注具体失败模式

#### Scenario: 事实可溯源率由独立 judge 判定

- **WHEN** 计算 answer_fact 的支撑判定
- **THEN** SHALL 使用与被测 agent 不同的 judge 模型
- **AND** 10% 样本 SHALL 进入人工抽检清单

### Requirement: Agent E2E 判卷与失败归因

`evals.agent.rag` SHALL 对 ERB 正样本题提供基于 `gold_answer` 的 LLM-as-judge 任务判卷（采纳 / 部分采纳 / 不采纳三档），judge 模型与被测模型分离。runner SHALL 逐题增量落盘并支持断点续跑：启动时按逐题记录中的样本标识跳过已完成题；单题失败 SHALL 记录错误并继续整批，SHALL NOT 中断后续题。对判卷不采纳的题，系统 SHALL 自动关联该题的检索命中记录与工具轨迹，按确定性规则归因到「检索没召回」「工具行为异常」「推理错」三类并产出归因报告；无组件层数据可关联的题 SHALL 标注为待人工复核而非默认归类。通用 Agent（SUPER_AGENT_QA）场景 SHALL 复用既有 DeepResearch Bench 子集评测线（`evals.agent.deepresearch`），SHALL NOT 在本能力内新建通用 Agent 数据集。

#### Scenario: 断点续跑

- **WHEN** 评测中途中断后以同一 tag 与数据集重新运行
- **THEN** 已完成题 SHALL 被跳过不重跑
- **AND** error 状态的题 SHALL 可经显式参数单独重跑

#### Scenario: 失败归因报告

- **WHEN** 存在判卷不采纳的题
- **THEN** 归因报告 SHALL 按三类给出计数与逐题明细
- **AND** 检索命中且工具轨迹正常但回答错误的题 SHALL 归为「推理错」

#### Scenario: 通用 Agent 场景复用既有线

- **WHEN** 需要 SUPER_AGENT_QA 场景的端到端数字
- **THEN** SHALL 使用 deepresearch 评测线产出，报告原文按官方 RACE/FACT 口径判分

### Requirement: 记忆召回行为评测

`evals.agent.memory` SHALL 评测 Agent 行为级记忆召回。数据集 SHALL 采用公开记忆评测数据集（LongMemEval）的会话与题目：每题的会话历史 SHALL 导入 Noesis 记忆存储（按题隔离评测用户，导入幂等且不触碰真实用户数据），题目经 Noesis Agent 执行。指标 SHALL 分三层报告——答案正确性（对齐该数据集的评测协议）、检索命中（`search_memory` 返回条目对该题标注答案会话的 recall@k / precision@k）、行为级召回（需要记忆线索的题 Agent 是否主动访问记忆：调用 `search_memory` 或经 `/memory` 虚拟路径读取）。负例场景 SHALL 断言 Agent 未访问记忆（两条路径都未走）且最终回答未引用已导入记忆，负例 SHALL 由数据集拒答类题目与自建配对场景共同构成。

#### Scenario: 行为断言与检索命中分开

- **WHEN** Agent 经 `/memory` 虚拟路径读取命中了答案会话但未调用 `search_memory`
- **THEN** 行为级召回 SHALL 记为成功
- **AND** 条目级 recall@k SHALL 记为未命中（无 search_memory 返回条目可评）

#### Scenario: 负例不误召回

- **WHEN** 无记忆线索的提问场景中 Agent 调用了 `search_memory`、读取了 `/memory` 路径或回答引用了已导入记忆
- **THEN** 该负例 SHALL 判定失败并计入误召回率

#### Scenario: 导入隔离

- **WHEN** 导入 LongMemEval 会话历史
- **THEN** 每题 SHALL 使用独立评测用户
- **AND** SHALL NOT 写入或修改任何真实用户的记忆数据

