# 测试用例 Agent 评测

```
evals/case/
  testpoints/                 ← 测试点评测（找这里）
    promptfooconfig.yaml      ← 评测集 + 金标准（tests 段）
    documents/                ← 输入需求文档（PRD）
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

**就在各目录的 `promptfooconfig.yaml` 里 `tests:` 段**，没有单独的 `dataset.jsonl`。

| 想看什么 | 打开 |
|----------|------|
| 测试点用例 + 金标准测试点 | `testpoints/promptfooconfig.yaml` |
| RAG 用例 + 检索金标准 | `rag/promptfooconfig.yaml` |

## 文档在哪？

| 用途 | 路径 |
|------|------|
| 测试点评测输入（当前需求全文） | `testpoints/documents/*.md` |
| RAG 历史需求语料（与上相同文件灌库） | `testpoints/documents/*.md` |
| RAG 历史用例语料 | `rag/corpus/test_cases/*.md` |

## 命令

```bash
cd backend
uv run python -m evals.case --phase testpoints --tag baseline
uv run python -m evals.case --phase rag --tag rb-baseline
uv run python -m evals.case.rag.ingest --map-only
uv run python -m evals.case.rag.ingest --reset
```
