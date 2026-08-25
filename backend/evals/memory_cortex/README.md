# Machine-memory evaluation

This suite freezes evaluation inputs before the production pipeline is implemented.

```bash
cd backend
uv run python -m evals.memory_cortex.harness
uv run python -m evals.memory_cortex.runner --output evals/memory_cortex/reports/structural.json
uv run pytest tests/test_memory_eval_contract.py -q
```

Default tests use deterministic fixtures. Live extraction requires both `--live` and `NOESIS_MEMORY_LIVE_EVAL=1`; its report is still not a complete release decision until retrieval, safety, cache and paired end-to-end layers are present.

`release_gate` 聚合全部报告并 fail closed；任一报告缺失、无效或未通过都会返回非零退出码：

```bash
uv run python -m evals.memory_cortex.release_gate \
  --output evals/memory_cortex/reports/release.json
```

该命令是发布准入，不是第二个运行时开关；产品运行时仍只有用户 `enabled`。

分层报告：

```bash
uv run python -m evals.memory_cortex.consolidation_eval --output evals/memory_cortex/reports/consolidation.json
uv run python -m evals.memory_cortex.retrieval_eval --output evals/memory_cortex/reports/retrieval-bulletin.json
uv run python -m evals.memory_cortex.safety_eval --output evals/memory_cortex/reports/safety.json

NOESIS_MEMORY_LIVE_EVAL=1 uv run python -m evals.memory_cortex.runner --live --split dev --output evals/memory_cortex/reports/live-dev.json
NOESIS_MEMORY_LIVE_EVAL=1 uv run python -m evals.memory_cortex.runner --live --split test --output evals/memory_cortex/reports/live-test.json
NOESIS_MEMORY_LIVE_EVAL=1 uv run python -m evals.memory_cortex.cache_eval --live --output evals/memory_cortex/reports/cache.json
```

Live 命令返回非零即表示对应检查未通过。retrieval/safety/cache 文件是 component diagnostics，不是 production-integration Release Gate；真实 paired A/B 必须由隔离用户、冻结 PostgreSQL/workspace/index snapshot 和完整 Agent Run 生成。

Production integration 使用独立数据库并自行创建、删除临时 Qdrant collection：

```bash
POSTGRES_DATABASE=noesis_memory_eval_20260824 uv run python -m evals.memory_cortex.integration_eval --report-dir evals/memory_cortex/reports
POSTGRES_DATABASE=noesis_memory_eval_20260824 uv run python -m evals.memory_cortex.runtime_integration_eval --live --report-dir evals/memory_cortex/reports
```

Release Gate 不接受复用旧模型 observations 的重签报告。模型、评测器或代码指纹变化后，必须重新执行对应 live 命令。
