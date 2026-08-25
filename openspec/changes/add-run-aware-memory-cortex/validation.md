# Validation Record

验证日期：2026-08-24。当前 eval revision=`2026-08-24.10`，代码指纹=`2920258aec56687df46fa75b00a35ef254c4418f811ecdeb8d87e5bcdad6ddf1`。当前没有该指纹下的完整 live 报告，`release_ready=false`；产品开关保持默认关闭。

## 当前冻结输入

- extraction prompt：`memory-extraction-v6`；seed=`20260824`；live chunk concurrency=`1`；provider retry backoff=`2s`。
- 当前 holdout：`test_v5.json`，SHA-256=`1fc9a013b25b5fff78305efeae6f204b6bcc4e303b368d92fa9b88b04dd87f7d`。
- Gold、prompt、模型、阈值或实现发生变化时必须进入新 revision；passed report、语义失败或跨代码/config/实际模型报告不得 resume。
- 相同指纹下只允许续跑 `RateLimitError`、连接/超时和 provider 5xx 等明确暂态失败；成功 observations、原始创建时间和每次失败 history 必须保留。

## 失败历史与修正

- v2：候选内容正确，但 Gold 把冗余 assistant 总结误标为强制 source ref；旧 fixture/report 保留，没有在原 test 上修改后重报。
- v3：漏掉“用户纠正 + 独立 validation”的 decision。生产 extractor 增加一次 high-value targeted recheck；重查仍为空允许正常 no-output，不制造 dead job。
- v4：带 ordered steps、validation、stop rule 的 procedure 被模型误标为 decision。prompt 与代码级分类都改为 workflow 优先。
- v5 首次运行：前两个 fixture 成功，随后 8 个 chunk 因免费 provider 限流失败。旧 harness 未记录异常类别，报告归档为 `live-test-v5-infra-failed.json`，不作为模型质量结论。
- 修正后的 dev 探测明确记录全部 chunk 为 `RateLimitError`。OpenCode 公共目录下载超时；逐模型实测中 `laguna-s-2.1-free` structured schema 不合格，`nemotron-3.5-lightning-free` upstream idle timeout，`x-preview-f-free` streaming payload 无法解析，因此没有切换冻结模型。

## 已完成的工程证据

- 旧 Dream、按日 API/UI/search、`memory/YYYY-MM-DD.md` 数据路径和 `config.yaml` 旧 `memory_cortex` 配置均已删除；只保留用户显式维护的 `USER.md` / `AGENTS.md`。
- 单一用户开关、终态 capture、snapshot/chunk、四类 candidate、consolidation、workspace/Qdrant 派生视图、Bulletin/Deep Query、治理 API/UI、cache-aware prompt 和 recall-loop 防护均已实现。
- 上一完整可用 paired A/B 证据显示 memory-on success=0.8333、off=0.1667，delta=0.6667，95% CI=`[0.4167, 0.9167]`；cache 专项读取 39424 tokens。但这些报告属于旧指纹，只证明此前实现阶段的运行表现，不能冒充当前 release evidence。
- 当前完整 Backend 回归为 1190 passed、6 skipped、22 deselected；Frontend lint、15 个影响范围 Vitest 与 build 通过（只有既有 chunk-size warning）；OpenSpec strict validation 通过；最新 Standards/Spec 双审均为 P1=0、P2=0。live provider 恢复并生成最终报告后仍需再做一次最终核验。

## 待完成

1. provider 限流恢复后，在当前指纹下重新跑 dev live；若只有可续跑的 provider 暂态失败，使用 `--resume-from` 续跑 failed fixtures。
2. dev 通过后运行冻结 `test_v5`；不得根据结果修改同一 holdout 后重报。
3. 重新生成 consolidation、production retrieval/safety、paired A/B、cache 与 Release Gate 报告。
4. Release Gate、完整回归和双审全部通过后，才能把 11.1 标记完成并允许用户开启功能。

## 命令

```bash
cd backend
NOESIS_MEMORY_LIVE_EVAL=1 uv run python -m evals.memory_cortex.runner --live --split dev --output evals/memory_cortex/reports/live-dev.json
NOESIS_MEMORY_LIVE_EVAL=1 uv run python -m evals.memory_cortex.runner --live --split test --output evals/memory_cortex/reports/live-test.json
# 仅同指纹、同模型且只有 provider 暂态失败时：
NOESIS_MEMORY_LIVE_EVAL=1 uv run python -m evals.memory_cortex.runner --live --split test --resume-from evals/memory_cortex/reports/live-test.json --output evals/memory_cortex/reports/live-test.json
```
