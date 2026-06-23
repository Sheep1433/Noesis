## 1. 测试用例评测精简与目录重组（已完成）

- [x] 删除 `eval_targets.json`、`--mock-judge`、`NOESIS_EVAL_MOCK_JUDGE` 路径
- [x] 调整 `scoring.py` 中断言为指标汇报模式
- [x] 删除 `tests/test_eval_runner_smoke.py`
- [x] 迁移至 `evals/case/`；入口改为 `python -m evals.case`
- [x] `evals/__main__.py` 仅作场景导航
- [x] 更新 `backend/evals/README.md`

## 2. 深度研究 Agent 评测骨架

- [x] 新增 `backend/evals/agent/` 包：`dataset.py`、`runner.py`、`scoring.py`、`report.py`、`__main__.py`
- [x] 实现工作区隔离：eval 专用路径 + `workspace_seed` 复制
- [x] 实现 `DeepResearchAgent` 离线 runner（收集 `tool_stats`、耗时、`completed`）

## 3. 混合评分

- [x] 实现规则 criteria：`file_exists`、`file_contains`、`json_field_min`（首版最小集）
- [x] 实现 `semantic_rubric` LLM Judge（`get_llm()`，无 mock）
- [x] 汇总 `overall_score` 与 per-criterion 结果

## 4. 数据集（迁移 WildClawBench 典型题，排除生活类）

- [x] 创建 `datasets/deep_research/dataset.jsonl` 与 `workspaces/` 种子目录
- [x] 纳入检索类 ≥2 条（含维基传记类改编）
- [x] 纳入代码智能类 ≥2 条（含 SAM3 debug 简化版）
- [x] 纳入报告合成类 ≥2 条
- [x] 可选：安全拒绝类 1 条
- [x] 每条填写 `provenance` 字段

## 5. CLI 与报告

- [x] `uv run python -m evals.agent --tag <name> [--item-id] [--limit] [--compare-to]`
- [x] 输出 `results/<tag>/runs/*.json` 与 `summary.json` / `summary.md`

## 6. 测试与文档（深度研究）

- [x] 单元测试：规则 scoring、dataset 加载（不调 LLM）
- [x] 集成测试 `@pytest.mark.integration`：单条 Agent 跑分（默认 skip）
- [x] 更新 `backend/evals/README.md` Agent 小节

## 7. 消息压缩评测骨架

- [x] 新增 `backend/evals/compression/`：`driver.py`、`grader.py`、`rubric.py`、`report.py`、`__main__.py`
- [x] `driver`：加载 fixture → 调用 `SummarizationOffloadMiddleware.before_model` → 返回压缩后 messages
- [x] `grader`：probe continuation + Judge（`get_llm()`）
- [x] `rubric.py`：五维 0–5 分 Judge prompt 与 JSON 解析

## 8. 压缩数据集

- [x] 创建 `fixtures/debug_session.json`、`feature_impl.json`、`config_build.json`（脱敏合成会话）
- [x] 创建对应 `probes/*.probes.json`（每 fixture 8–12 道，覆盖四种 type）
- [x] 可选：`scripts/scrub_fixture.py` 从会话 JSONL 脱敏导出（文档说明即可，非阻塞）

## 9. 压缩 CLI 与报告

- [x] `uv run python -m evals.compression --tag <name> [--fixture] [--runs] [--compare-to]`
- [x] 输出 `results/<tag>/runs/<fixture_id>.json` 与 `summary.md`

## 10. 压缩测试

- [x] 单元测试：rubric 解析、probe 加载、fixture schema（不调 LLM）
- [x] 集成测试 `@pytest.mark.integration`：单 fixture 全流程（默认 skip）

## 11. 文档

- [x] `backend/evals/README.md` 三条评测线总览与示例命令
