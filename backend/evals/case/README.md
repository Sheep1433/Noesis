# 测试用例 Agent 评测

```
evals/case/
  report.py                   # 跑分后汇总指标
  results/<tag>/              # 默认 eval JSON + summary
  testpoints/                 ← 测试点评测（找这里）
    promptfooconfig.yaml      ← 评测运行时配置（tests 段由脚本生成）
    golden/                   ← 金标准源数据（scene + test_points）
      prd_001.yaml
    documents/                ← 输入需求文档（PRD）
    golden_loader.py          ← 读取 golden/*.yaml
    generate_eval_dataset.py  ← 从 documents/ + golden/ 生成 promptfooconfig
    provider.py
  rag/                        ← RAG 检索评测（找这里）
    promptfooconfig.yaml      ← 评测集 + relevant_ids 金标准
    corpus/test_cases/        ← 灌库用的历史用例文档
    id_map.json               ← 分块 point_id（ingest 生成）
    ingest.py                 ← 灌 Qdrant
    provider.py
  shared/                     ← 断言、Judge（一般不用改）
    assertions.py
    judge.py
    run-python.sh
```

## 评测集在哪？

**金标准源数据**在 `testpoints/golden/prd_*.yaml`（按场景列出测试点）；**跑评测时**由脚本写入 `promptfooconfig.yaml` 的 `tests:` 段。共 **20** 条样本、**428** 条金标准测试点。recall/precision 由 `shared/coverage_scorer.py` 确定性打分（token 对齐）；borderline 可选 LLM 仲裁（`NOESIS_CASE_COVERAGE_LLM_BORDERLINE=1`，默认开启）。

| 想看什么 | 打开 |
|----------|------|
| 金标准（人工维护） | `testpoints/golden/prd_*.yaml` |
| 评测运行时配置（生成） | `testpoints/promptfooconfig.yaml` |
| RAG 用例 + 检索金标准 | `rag/promptfooconfig.yaml` |

## 文档在哪？

| 用途 | 路径 |
|------|------|
| 测试点评测输入（当前需求全文） | `testpoints/documents/prd_*.md`（20 篇） |
| RAG 历史需求语料（与上相同文件灌库） | `testpoints/documents/*.md` |
| RAG 历史用例语料 | `rag/corpus/test_cases/*.md` |

## 命令

```bash
cd backend
# 改 PRD 正文 → testpoints/documents/prd_*.md
# 改金标准 → testpoints/golden/prd_*.yaml
uv run python evals/case/testpoints/generate_eval_dataset.py
uv run python -m evals.case --phase testpoints --tag baseline
uv run python -m evals.case --phase stage-a --tag baseline      # 别名
uv run python -m evals.case --phase rag --tag rb-baseline
uv run python -m evals.case --phase stage-b --tag rb-baseline   # 别名
uv run python -m evals.case --phase testpoints --tag debug --item-id prd_001 --limit 1
uv run python -m evals.case.rag.ingest --map-only
uv run python -m evals.case.rag.ingest --reset
```

CLI 还支持 `--baseline`（promptfoo compare）、`--output`（自定义结果 JSON）。默认结果目录：`evals/case/results/<tag>/`（含 `<phase>.json`、`<phase>-summary.json`）。

RAG pytest 集成测（默认 skip）：`NOESIS_CASE_RAG_EVAL=1 uv run pytest tests/test_eval_case_stage_b_integration.py -q`
