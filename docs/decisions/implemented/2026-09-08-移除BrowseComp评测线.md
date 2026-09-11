# 决策：移除 BrowseComp 评测线

状态：implemented
日期：2026-09-08

## 问题

离线评测的五条 Agent 线中，BrowseComp（`evals.agent.browsecomp`，官方 CSV 多步检索短答案基准）的单位数据点成本与不稳定度显著高于其他线：每题即一次完整深度研究 run（多步 web 检索、长耗时），30 题基线受免费网关限流与外网可达性影响动辄中断。而它的信号不再不可替代：端到端任务成功率已由 `evals.agent.rag`（judge 判卷）产出，通用 Agent 场景的质量面由 DeepResearch Bench 子集线（`evals.agent.deepresearch`）承担，SSE 链路稳定性由 loadtest 压测覆盖。评测体系的目标是让每个能力条目有一个可辩护的数字，BrowseComp 对应不上任何独占条目。

## 决策

整体移除 BrowseComp 评测线：删除 `backend/evals/agent/browsecomp/` 与 `tests/test_eval_agent_browsecomp.py`；`evals.agent` 入口清单、`evals/README.md`、`docs/engineering/agents/agent-evaluation.md`、`docs/test/eval-set-design.md`、`offline-evals` 规格与 `eval-metrics-baseline` 提案中的引用全部改指向 DeepResearch 线。共用执行层 `_agent.py` 的结果 `suite` 字段默认值从 `browsecomp` 改为中性的 `super-agent`——该默认值实际服务 memory/deepresearch 线，原命名是历史残留。

## 备选方案

- **保留但降频（按需手动跑）**：否——不稳定线的维护挫败感与排查成本不因降频消失，且它不产出任何叙事需要的数字。
- **保留 BrowseComp、改砍 case 线**：case 线（测试用例）虽已不在简历叙事主线，但实现稳定、近零维护，冻结不投入即可，删除没有收益。

## 代价

- 通用 Agent 场景失去「多步检索 + 短答案」口径的官方基准数字；未来需要时以自身 change 恢复（官方 CSV 可重新下载，数据管道简单）。
- `docs/engineering/agents/agent-evaluation.md` 中 2026-07-30 的两次 BrowseComp 实测记录随重写移除（数据仅说明当时链路行为，无后续参照价值）。
- `offline-evals` 规格中 Harbor 相关条款因 Harbor 实现已从工作区移除而悬空，属既有问题，待其自身 change 收口，不在本次范围。

## 验证

- `grep -rn -i browsecomp` 全仓仅剩归档 openspec 记录与压缩 fixture 数据文件（历史记录与数据，不改写）。
- `python3 scripts/verify-md-links.py` 通过（248 文件）。
- `uv run pytest tests/test_eval_agent_runtime.py tests/test_eval_agentic_rag.py tests/test_eval_agent_memory.py tests/test_harness_eval_overrides.py tests/test_eval_langfuse_generation.py -q`：39 passed。
