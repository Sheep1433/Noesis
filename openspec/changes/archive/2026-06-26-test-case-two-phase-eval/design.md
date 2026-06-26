## Context

- **流水线**：阶段 A（`generate_scenes_testpoints`：query + `document_context` → 场景+测试点）→ 用户勾选 → 阶段 B（按场景：`document_context` 全文 + 两路 RAG → 批量生成用例）。
- **现状**：`evals.case` 仅有一套 `promptfoo/` 端到端配置；`rag_hit_at_3` 为宏 Hit 而非 Recall@K；无 `expected_rag` 金标准；`provider` 未灌库且 `source_file_names=[]`；阶段 B 仍对 `current_requirement` 做 Top-K 检索，与产品方向不符。
- **约束**：评测统一 **promptfoo**；Langfuse 用 `backend/evals/.env`；默认 CI pytest 不调 DashScope（阶段 A coverage 单测 mock Judge）；阶段 B 集成测需 Qdrant + embedding，门控 `NOESIS_CASE_STAGE_B_EVAL=1`。
- **参考**：promptfoo 官方 [Evaluating RAG pipelines](https://www.promptfoo.dev/docs/guides/evaluate-rag/) 的 **retrieval-in-isolation** 模式（`file://retrieve.py` + python/`contains-all` 断言）。

## Goals / Non-Goals

**Goals:**

- 以 promptfoo 建立 **阶段 A**、**阶段 B 检索** 两条可独立运行的评测线，指标定义清晰、可 baseline 对比。
- 阶段 B 产品行为：当前需求 **全文注入**，检索仅 **历史需求 + 历史用例** 两路 hybrid Top-K（默认 K=3）。
- 金标准数据集 + Qdrant eval fixture + `ingest` 脚本，支持 `relevant_ids` 标注。
- 更新 OpenSpec / README / test_tdd_design，与实现一致。

**Non-Goals:**

- 不引入 RAGAS 作为必选依赖（后续可在 python assertion 内可选扩展）。
- 不把阶段 B **用例生成质量**（步骤是否合理）纳入本变更主指标（属 E2E / 未来 Judge）。
- 不强制数据集扩至 20 条（以仓库渐进扩充为准）。
- 不改动 SSE / 前端交互契约。

## Decisions

### D1：单目录 `promptfoo/` + 多 yaml（不按阶段拆目录）

```
evals/case/promptfoo/
  promptfooconfig.stage-a.yaml   ← --phase stage-a（默认）
  promptfooconfig.stage-b.yaml   ← --phase stage-b
  provider_stage_a.py
  provider_stage_b.py
  assertions.py                  # 两阶段 scorer 共用
  judge.py
  documents/
  run-python.sh
```

`evals.case.__main__` 根据 `--phase` 传 `-c promptfooconfig.<phase>.yaml` 给 `npx promptfoo eval`。

**理由**：同一工具链、同一 documents、同一 assertions 模块；拆目录只会重复 `run-python.sh` 与路径解析。阶段差异用 **不同 yaml + 不同 provider 文件** 表达即可（promptfoo 官方 `eval-rag-full` 也是单目录 `ingest.py` + `retrieve.py` + 一个 config）。

**不采用**：`promptfoo-stage-a/`、`promptfoo-stage-b-retrieval/`、`promptfoo/` 三目录并列。

**不纳入本变更**：独立 `e2e` 全链路 config；若需冒烟，后续可在同目录加 `promptfooconfig.e2e.yaml`，不新增目录。

### D2：阶段 A 指标

| 指标 | metric 名 | 计算 | 断言类型 |
|------|-----------|------|----------|
| 结构门禁 | `l0` | schema + 无 error | python，`pass` 门禁 |
| **测试点覆盖召回率** | `point_coverage_recall` | \|covered golden points\| / \|golden_test_points\| | llm-rubric（`judge.py` → `get_llm()`） |
| 场景名命中率（辅助） | `scene_name_recall` | \|golden scene_names ∩ generated\| / \|golden scene_names\| | python，确定性 |
| 延迟（观测） | `latency_ms_testpoints` | provider 耗时 | metadata，不断言 |

`point_coverage_recall` 与现有 rubric 一致：语义覆盖，允许多生成点覆盖一条金标准。

**理由**：用户关心的「一阶段召回」= 金标准测试点是否被生成覆盖；`scene_name_recall` 辅助发现场景拆分偏差。

### D3：阶段 B 指标（仅两路检索）

| 指标 | metric 名 | 公式（每 item × channel） |
|------|-----------|---------------------------|
| Recall@K | `historical_requirements_recall_at_k` / `historical_test_cases_recall_at_k` | \|relevant ∩ topK\| / \|relevant\| |
| Hit@K | `*_hit_at_k` | 1 if relevant ∩ topK ≠ ∅ else 0 |
| MRR@K | `*_mrr_at_k` | 1/rank of first relevant in topK，否则 0 |
| 宏平均 | `macro_recall_at_k` | 两路 recall 的算术平均（仅含已标注 channel） |
| 全文注入（非召回） | `document_context_present` | prompt 组装逻辑或 provider 返回 flag = true |

**K 默认 3**，与 `DEFAULT_TOP_K` 一致；config 可覆盖 `vars.k`。

**对账键**：`ground_truth.stage_b_scenes[]` 每项含 **固定** `scene_name`（fixture），**不**使用阶段 A LLM 输出名。

**理由**：标准 IR 指标；python assertion 实现，不用 `context-recall`（LLM 不稳定、非 Recall@K）。

### D4：阶段 B RAG 产品行为变更

```
scene_cases_prompt =
  document_context（全文，固定标题「当前需求文档」）
  + scene_rag_context（仅历史需求片段 + 历史用例片段）
```

- 删除 `build_scene_rag_context` 内 `current_requirement` 检索协程。
- `retrieval_trace.channels` 仅含 `historical_requirements`、`historical_test_cases`。
- `historical_requirements` 默认 **开启**于 eval config（`case_rag_historical_requirements_enabled: true`）；生产默认仍可由 yaml 控制。

### D5：数据集 Schema（`datasets/test_case/dataset.jsonl`）

```json
{
  "id": "tc_login_001",
  "scenario_description": "重点覆盖登录失败与验证码",
  "document_path": "documents/tc_login_001.md",
  "ground_truth": {
    "golden_test_points": [
      { "scene_name": "用户登录", "point_name": "用户名密码错误提示" }
    ],
    "golden_scene_names": ["用户登录"],
    "stage_b_scenes": [
      {
        "scene_name": "用户登录",
        "scene_description": "账号密码登录与验证码",
        "adopted_point_names": ["用户名密码错误提示", "验证码过期刷新"],
        "source_file_names": ["tc_login_001.md"],
        "expected_rag": {
          "historical_requirements": { "relevant_ids": ["<uuid>"], "k": 3 },
          "historical_test_cases": { "relevant_ids": ["<uuid>"], "k": 3 }
        }
      }
    ]
  }
}
```

- `stage_b_scenes` 可多条（多场景 item）。
- `relevant_ids` 由 `ingest.py` 产出 `id_map.json` 后人工标注。
- **移除** `expected_rag.current_requirement`。

### D6：Qdrant Eval Fixture

```
evals/case/
  fixtures/
    requirements/
    test_cases/
  ingest.py                # → requirement_docs_eval, test_case_docs_eval
  promptfoo/id_map.json    # 或 evals/case/id_map.json，ingest 产出
```

- Collection 名通过 `config.yaml` eval 段或 env 覆盖，**禁止**写入生产 collection。
- `ingest` 使用与线上一致的分块+嵌入 pipeline（`documents_to_points`）。
- Provider 评测前检查 collection 非空，否则 fail fast。

### D7：`promptfooconfig.stage-b.yaml` 结构（与 stage-a 同目录）

```yaml
prompts: ['{{ query }}']
providers:
  - id: file://provider_stage_b.py
    config:
      pythonExecutable: ./run-python.sh
defaultTest:
  assert:
    - type: python
      value: file://assertions.py:assert_historical_requirements_recall
      metric: historical_requirements_recall_at_3
    # ... hit, mrr, document_context_present
```

`provider_stage_b.py`：读取 `vars.stage_b_scene`，调用 `build_scene_rag_context`。`provider_stage_a.py` 仅阶段 A。

### D8：报告与 baseline

- promptfoo 原生 `eval` 输出 + 可选 `aggregate.py` 汇总宏平均至 `results/<tag>/aggregate.json`。
- `--compare` / `--baseline` 沿用 promptfoo CLI（`evals.case` 透传）。
- Langfuse：`eval_line=case`，`eval_phase=stage-a|stage-b`。

### D9：CI 策略

| 层 | 内容 | 默认 CI |
|----|------|---------|
| L1 | scorer 单测（mock trace / mock Judge） | ✅ pytest |
| L2 | stage-a promptfoo config 结构测试 | ✅ pytest |
| L3 | stage-b + live Qdrant | `NOESIS_CASE_STAGE_B_EVAL=1` |

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| `relevant_ids` 标注成本高 | `ingest` 导出 id_map；先 3 条试点再扩 |
| embedding/分块变更导致 id 漂移 | fixture 版本 pin；ingest 写入 `fixture_version` |
| 阶段 A LLM Judge 波动 | 记录 rubric reason；baseline 对比看趋势非绝对阈值 |
| 历史需求通道生产默认关闭 | eval config 显式开启；spec 区分 prod default vs eval |
| 旧 `promptfooconfig.yaml` 与新区共存混淆 | 重命名为 `promptfooconfig.stage-a.yaml` 或删除旧端到端 rag 断言 |

## Migration Plan

1. 在现有 `promptfoo/` 内拆 `stage-a` / `stage-b` 两套 yaml 与 provider；废弃旧 `rag_hit_at_3`。
2. 改 `rag.py` + `case_graph` 全文注入；更新 `test_scene_rag_context.py`。
3. 废弃 `assert_rag` / `rag_hit_at_3`；迁移 scorer 单测至新 assertion。
4. 补 `ingest.py` + 至少 3 条 `stage_b_scenes` 金标准。
5. 更新 `evals/README.md`、`docs/test/test_tdd_design.md`。
6. 归档 OpenSpec 后合并 delta 至 `agent-test-case`、`test-case-agent-eval`。

**回滚**：RAG 行为可通过 git revert `rag.py`；旧 `promptfooconfig.yaml` 可从 git 恢复。

## Open Questions

- `historical_requirements` 生产默认是否随本变更改为 `true`？（建议 eval 开启、生产另 PR 决策。）
- `id_map.json` 是否入库 git？（建议入库，避免 CI 重复 ingest 调 embedding API。）
- 是否在 `docs/NOTES.md` 记录指标口径变更？（实现阶段写入。）
