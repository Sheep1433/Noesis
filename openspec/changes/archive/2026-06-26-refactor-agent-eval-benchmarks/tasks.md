## 1. 共享骨架与 taxonomy

- [x] 按 benchmark 分子模块（`browsecomp/`、`wildclaw/`、`legacy/`），非 `suites/` 统一 runner — 见 `evals/agent/__main__.py`
- [x] `evals/agent/_agent.py` 共用 DeepResearchAgent 执行
- [x] `evals/README.md` 三子模块章节
- [ ] ~~`taxonomy.yaml` + `--suite all` 统一 CLI~~（非目标：保持子模块独立入口）

## 2. WildClawBench（evals.agent.wildclaw）

- [x] `wildclaw/__main__.py` + `worker.py` + `noesis_agent.py`
- [x] 调官方 `script/run.sh` + Docker grader
- [ ] 全量 60 题 manifest 入库（依赖上游 vendor 与运维环境，非合并门禁）

## 3. BrowseComp（evals.agent.browsecomp）

- [x] `browsecomp/official.py` + `__main__.py`
- [x] `tests/test_eval_agent_browsecomp.py`
- [ ] 全量 1266 题 CI 跑分（手动 smoke 即可）

## 4. OpenHands Index 五柱

- [ ] ~~`suite=openhands` 统一柱~~（未实现；未来按需独立 change）

## 5. legacy 本地开发集

- [x] `legacy/datasets/deep_research/` + `runner.py` + `scoring.py`
- [x] 主规格 `agent-offline-eval` 已描述 legacy 与官方 benchmark 分工

## 6. 回归

- [x] `test_eval_agent_dataset.py`、`test_eval_agent_scoring.py`、`test_eval_agent_integration.py`
- [x] `uv run pytest tests/test_eval_agent_* -q`（合并前已通过）

> **归档说明**：本 change 的「三套件 taxonomy + 统一 runner」设计未按原 tasks 落地；实际交付为 **browsecomp / wildclaw / legacy 三子模块**，与 `openspec/specs/agent-offline-eval/spec.md` 及 `backend/evals/README.md` 一致。
