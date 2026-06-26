## 1. 阶段 B 产品行为（RAG 全文注入）

- [x] 1.1 `rag.py`：移除 `current_requirement` 检索；`retrieval_trace` 仅两 channel
- [x] 1.2 `case_graph._build_scene_cases_prompt`：拼接 `document_context` 全文 + `scene_rag_context`
- [x] 1.3 更新 `test_scene_rag_context.py`

## 2. 单目录 promptfoo（`evals/case/promptfoo/`）

- [x] 2.1 新增 `promptfooconfig.stage-a.yaml`、`provider_stage_a.py`
- [x] 2.2 新增 `promptfooconfig.stage-b.yaml`、`provider_stage_b.py`
- [x] 2.3 `assertions.py`：L0、scene_name_recall、Recall/Hit、`document_context_present`；移除 `rag_hit_at_3`
- [x] 2.4 `evals.case.__main__.py`：`--phase` → `-c promptfooconfig.<phase>.yaml`
- [x] 2.5 删除旧 `promptfooconfig.yaml`、`provider.py`

## 3. Fixture 与数据集

- [x] 3.1 `ingest.py` 占位 + `fixtures/` 目录约定（文档说明）
- [x] 3.2 扩展 stage-b yaml：补 `relevant_ids` 金标准（需正式 ingest）
- [x] 3.3 实现完整 Qdrant ingest pipeline

## 4. 测试与文档

- [x] 4.1 `tests/test_eval_promptfoo.py`：两 yaml 结构
- [x] 4.2 scorer 单测（mock）
- [x] 4.3 更新 `evals/README.md`、`docs/NOTES.md`
- [x] 4.4 `docs/test/test_tdd_design.md` 登记两阶段指标
- [x] 4.5 可选：`NOESIS_CASE_STAGE_B_EVAL=1` 集成测
