## Why

测试用例 Agent 离线评测目前把阶段 A 与阶段 B RAG 混在同一条 promptfoo 端到端流水线里，且 `rag_hit_at_3` 名实不符、金标准缺失，与产品方向（二阶段全文注入当前需求、仅检索历史需求与历史用例）不一致。需要在 **同一 spec（`test-case-agent-eval`）** 与 **同一 `promptfoo/` 目录** 下，按 `--phase` 切换配置，明确两阶段指标与完整方案。

## What Changes

- **单一目录** `backend/evals/case/promptfoo/`：共用 `assertions.py`、`judge.py`、`documents/`；按 phase 选用不同 `promptfooconfig.*.yaml` 与 provider 脚本。
- **阶段 A**（`promptfooconfig.stage-a.yaml`）：L0、`point_coverage_recall`、`scene_name_recall`；provider 仅 `generate_scenes_testpoints_node`。
- **阶段 B**（`promptfooconfig.stage-b.yaml`）：两路 RAG 的 Recall@3 / Hit@3 / MRR@3；provider 仅 `build_scene_rag_context`；`document_context_present` 检查（非召回）。
- **产品对齐**（`agent-test-case`）：阶段 B 当前需求全文注入；`retrieval_trace` 仅两 channel。
- 金标准、`fixtures/`、`ingest.py` 置于 `evals/case/`（与 `promptfoo/` 平级）。
- **BREAKING**：废弃 `rag_hit_at_3`；`evals.case` CLI 用 `--phase stage-a|stage-b` 选择 config 文件（默认 `stage-a`）。

## Capabilities

### New Capabilities

（无 — 指标与方案全部写入 `test-case-agent-eval`。）

### Modified Capabilities

- `test-case-agent-eval`：两阶段 promptfoo 指标、单目录多 config、数据集、ingest、CLI、报告。
- `agent-test-case`：阶段 B RAG 产品行为（全文注入、两路检索）、离线评测章节与上面对齐。

## Impact

| 区域 | 变更 |
|------|------|
| `backend/evals/case/promptfoo/` | 拆为 `stage-a` / `stage-b` 两套 yaml + 两个 provider；合并 assertions |
| `backend/evals/case/` | `fixtures/`、`ingest.py`；`__main__.py` 按 phase 选 `-c` |
| `backend/agent/case_generate/` | `rag.py`、`case_graph.py` 全文注入 |
| `openspec/specs/test-case-agent-eval/spec.md` | 归档后合并完整评测 spec |
