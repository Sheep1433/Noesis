## Purpose

规定测试用例 Agent **离线 promptfoo 评测**：阶段 A（测试点生成召回）与阶段 B（历史需求/历史用例 RAG 召回）的指标、数据集、目录结构与 CLI。产品侧 RAG 行为见 `agent-test-case`。

## MODIFIED Requirements

### Requirement: 测试用例评测入口为 evals.case 子模块

测试用例 Agent 离线评测 SHALL 通过 `uv run python -m evals.case` 启动。`uv run python -m evals` **SHALL NOT** 直接执行 promptfoo，仅 SHALL 输出子模块说明。

系统 SHALL 支持 `--phase stage-a|stage-b`，映射到 **同一目录** `backend/evals/case/promptfoo/` 下不同配置文件：

| phase | 配置文件 | 用途 |
|-------|----------|------|
| `stage-a`（默认） | `promptfooconfig.stage-a.yaml` | 测试点 L0 + coverage + scene_name_recall |
| `stage-b` | `promptfooconfig.stage-b.yaml` | 两路 RAG Recall@K / Hit@K / MRR@K |

CLI SHALL 透传 promptfoo 的 `--compare` / `-o` 等参数。

#### Scenario: 根包不跑分

- **WHEN** 开发者运行 `uv run python -m evals`
- **THEN** 系统 SHALL 打印用法提示，**SHALL NOT** 调用 promptfoo

#### Scenario: 默认阶段 A

- **WHEN** 开发者运行 `uv run python -m evals.case --tag baseline`
- **THEN** 系统 SHALL 执行 `promptfoo/promptfooconfig.stage-a.yaml`

#### Scenario: 阶段 B 检索

- **WHEN** 开发者运行 `uv run python -m evals.case --phase stage-b --tag rb-baseline`
- **THEN** 系统 SHALL 执行 `promptfoo/promptfooconfig.stage-b.yaml`

### Requirement: 测试用例评测代码位于 evals/case 单目录 promptfoo

实现 SHALL 位于 `backend/evals/case/`，结构：

```
evals/case/
  promptfoo/
    promptfooconfig.stage-a.yaml
    promptfooconfig.stage-b.yaml
    provider_stage_a.py
    provider_stage_b.py
    assertions.py          # 两阶段 scorer 共用
    judge.py
    documents/
    run-python.sh
  fixtures/                # Qdrant 灌库素材
  ingest.py
```

用例与金标准 SHALL 写在 `promptfoo/promptfooconfig.stage-a.yaml` / `promptfooconfig.stage-b.yaml` 的 `tests` 段，**SHALL NOT** 使用单独的 `dataset.jsonl`。

#### Scenario: 共享 documents

- **WHEN** 任一路径解析 `document_path`
- **THEN** SHALL 从 `promptfoo/documents/` 读取

### Requirement: 金标准字段（yaml tests）

每条 stage-a 测试 vars SHALL 含：`item_id`、`scenario_description`、`document_path`、`ground_truth.golden_test_points`。MAY 含 `golden_scene_names`、`golden_test_points_json`（供 llm-rubric）。

每条 stage-b 测试 vars SHALL 含：`item_id`、`document_path`、`query`、`stage_b_scene`（含 `expected_rag` 两 channel）。

### Requirement: 阶段 A provider

`provider_stage_a.py` SHALL 解析 `document_context`，调用 `generate_scenes_testpoints_node`，返回 `state.scenes_testpoints`。**SHALL NOT** 连接 Qdrant 或调用阶段 B。

#### Scenario: 无 Qdrant

- **WHEN** 阶段 A 任一条目执行
- **THEN** SHALL NOT 调用 `init_qdrant_client`

### Requirement: 阶段 A 指标

| metric | 含义 | 断言 |
|--------|------|------|
| `l0` | schema 合法、无 error | python，`pass` 门禁 |
| `point_coverage_recall` | 金标准测试点语义覆盖比例 | llm-rubric + `judge.py` → `get_llm()` |
| `scene_name_recall` | 金标准 scene_name 出现在生成结果中的比例 | python |

`point_coverage_recall`：**SHALL NOT** 设仓库级自动失败阈值（仅记录分数）。

#### Scenario: coverage 分数记录

- **WHEN** `point_coverage_recall=0.42`
- **THEN** 断言 SHALL 记录分数，`pass` SHALL 为 true

#### Scenario: scene_name 部分命中

- **WHEN** 金标准 `["用户登录","会话管理"]`，生成仅 `用户登录`
- **THEN** `scene_name_recall` SHALL 为 0.5

### Requirement: 阶段 B provider

`provider_stage_b.py` SHALL 读取 `vars.stage_b_scene` 与 `document_path`，调用 `build_scene_rag_context`，返回 `retrieval_trace`、`scene_rag_context`、`document_context_injected`。**SHALL NOT** 调用测试点/用例生成 LLM。

评测运行 SHALL 启用 `case_rag_historical_requirements_enabled=true`。

#### Scenario: Qdrant 不可用

- **WHEN** `--phase stage-b` 且 Qdrant 未连接
- **THEN** SHALL 失败并返回明确错误

#### Scenario: fixture 未灌库

- **WHEN** eval collection 为空
- **THEN** SHALL 提示先执行 `uv run python -m evals.case.ingest`（或等价命令）

### Requirement: 阶段 B 检索指标

对 `stage_b_scenes[].expected_rag` 每个已标注 channel（K 默认 3）：

| metric | 定义 |
|--------|------|
| `historical_requirements_recall_at_3` 等 | \|relevant_ids ∩ topK\| / \|relevant_ids\| |
| `*_hit_at_3` | 交集非空为 1，否则 0 |
| `*_mrr_at_3` | 首个 relevant 排名倒数 |
| `macro_recall_at_3` | 已标注 channel recall 算术平均 |
| `document_context_present` | 当前需求全文参与组装（非向量召回） |

SHALL 使用 python assertion；**SHALL NOT** 以 `context-recall` 作主指标。对账使用 fixture 固定 `scene_name`，**SHALL NOT** 使用阶段 A LLM 生成的场景名。

#### Scenario: recall 部分命中

- **WHEN** 2 个 relevant_ids，top3 命中 1 个
- **THEN** `recall_at_3` SHALL 0.5，`hit_at_3` SHALL 1.0

#### Scenario: channel 未标注跳过

- **WHEN** `relevant_ids` 为空
- **THEN** 该 channel SHALL skipped，**SHALL NOT** 记失败

### Requirement: Qdrant eval fixture 与 ingest

`ingest.py` SHALL 将 `fixtures/` 写入隔离 collection（默认 `requirement_docs_eval`、`test_case_docs_eval`），输出 `id_map.json`。SHALL 使用与线上一致的分块嵌入 pipeline。

#### Scenario: 不污染生产库

- **WHEN** 未配置 eval collection 覆盖
- **THEN** ingest SHALL NOT 写入无后缀生产 collection 名

### Requirement: 评测无仓库质量门与 CI

SHALL **NOT** 依赖 `eval_targets.json` 或 mock Judge 开关（coverage live 跑分用真实 `get_llm()`）。pytest SHALL mock L0 / scene_name / Recall scorers；live 阶段 B 集成测门控 `NOESIS_CASE_STAGE_B_EVAL=1`。

#### Scenario: 默认 CI

- **WHEN** 默认 `pytest` 套件
- **THEN** SHALL NOT 调用 DashScope 或要求 Qdrant

### Requirement: 报告

阶段 A 汇总 SHALL 含 `l0_pass_rate`、`point_coverage_recall_mean`、`scene_name_recall_mean`。阶段 B 汇总 SHALL 含两路 `recall_at_3_mean`、`macro_recall_at_3_mean`（有标注时）。SHALL 支持 promptfoo baseline 对比。

#### Scenario: stage-b 分 channel 汇总

- **WHEN** `--phase stage-b` 完成
- **THEN** 报告 SHALL 分别汇总 `historical_requirements` 与 `historical_test_cases`

## REMOVED Requirements

### Requirement: 测试用例评测代码位于 evals/case（旧路径描述）

**Reason**: 数据集路径与多目录 promptfoo 描述过时。

**Migration**: 见 MODIFIED「单目录 promptfoo」需求。

### Requirement: 测试用例评测无质量门与 mock Judge（含 assert_rag）

**Reason**: `rag_hit_at_3` 废弃，由阶段 B Recall@K 取代。

**Migration**: `--phase stage-b` + `historical_*_recall_at_3` metrics。

## ADDED Requirements

### Requirement: CI 登记

系统 SHALL 在 `docs/test/test_tdd_design.md` 登记阶段 A、阶段 B 指标及 `promptfoo/promptfooconfig.*.yaml` 路径。

#### Scenario: 文档审查

- **WHEN** 审查测试设计文档
- **THEN** SHALL 列出两阶段 metric 名称与 mock 策略
