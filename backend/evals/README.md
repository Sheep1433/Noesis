# 评测

`evals` 为评测包根目录，**不直接跑分**。各场景使用独立子模块：

| 场景 | 命令 | 状态 |
|------|------|------|
| 测试用例 Agent | `uv run python -m evals.case` | 已实现 |
| 深度研究（DeepResearch Bench 子集） | `uv run python -m evals.agent.deepresearch` | 已实现（官方判分未接入） |
| Agent E2E（ERB 官方口径） | `uv run python -m evals.agent.rag` | 已实现 |
| 记忆召回（LongMemEval） | `uv run python -m evals.agent.memory` | 已实现 |
| 消息压缩（三组对照，真 Agent 路径） | `uv run python -m evals.compression` | 已实现 |
| 深度研究负载测试 | `uv run locust -f evals/loadtest/locustfile.py` | 已实现 |
| 知识库检索（ERB 基准） | `uv run python -m evals.kb.erb --all` | 已实现 |

```bash
cd backend
uv run python -m evals    # 仅打印上表说明
```

OpenSpec：`openspec/specs/offline-evals/spec.md`

## Langfuse（评测专用项目）

三条评测线共用 **`backend/evals/.env`** 中的 Langfuse 凭据，与 `backend/.env` 隔离：

```bash
cp backend/evals/.env.example backend/evals/.env
# 填入评测专用 Langfuse 项目的 pk/sk
```

- 仅在 `eval_langfuse_run(...)` 上下文内临时注入 SDK 环境变量，**退出后恢复**，不污染主项目
- trace metadata：`source=noesis-eval`、`eval_line`（`case` / `agent` / `compression`）、`eval_tag`
- 未配置 `evals/.env` 时评测照常跑分，只是不上报 Langfuse

---

## 各评测线如何查看结果

Noesis **没有**统一的评测结果 Web 页面；各子模块产物与查看方式不同：

| 评测线 | 产物目录 | 专用 Web UI | 推荐查看方式 |
|--------|----------|-------------|--------------|
| 测试用例 `evals.case` | `evals/case/results/<tag>/` | **有**（promptfoo） | 控制台汇总 + `npx promptfoo view` |
| 深度研究 | `evals/agent/deepresearch/results/<tag>/` | **无** | `articles.jsonl`（直接喂官方 RACE/FACT 判分脚本）+ `summary.json` |
| Agent E2E | `evals/agent/rag/results/<tag>/` | **无** | `erb_answers.jsonl`（喂 ERB 官方判分脚本）+ `summary.md`（运行健康度） |
| 记忆召回 | `evals/agent/memory/results/<tag>/` | **无** | `summary.md`（三层指标） |
| 消息压缩 | `evals/compression/results/<tag>/` | **无** | `summary.md`；`--compare-to` 对比历史 |
| 知识库检索 `evals.kb.erb` | `evals/kb/results/<tag>/` | **无** | `summary.md`（Recall/MRR/nDCG/阈值表 + CI） |
| 负载测试 Locust | Locust Web（运行时） | **有**（Locust） | `http://localhost:8089`（仅压测进行中） |

所有线共用统一产物结构：`results/<tag>/{manifest.json, raw(.jsonl), summary.json, summary.md}`——
manifest 记录模型/数据集/种子/配置/token 成本/git sha；**tag 复用被拒绝**（历史基线不覆盖，换新 tag）。
含 LLM-as-judge 的线（memory）另落盘 `manual_review_queue.json`（固定种子 10% 人工抽检清单）。
判卷模型必须与被测模型不同（`--judge-model-id` 必填，相同即拒跑）。
Agent E2E 线不自带判分：跑完自动导出 `erb_answers.jsonl`，质量指标由 ERB 官方脚本判分产出（见下文 Agentic RAG 一节）。

### 测试用例（promptfoo）

跑分结束控制台打印 recall/precision，并写入：

```
evals/case/results/<tag>/<phase>.json
evals/case/results/<tag>/<phase>-summary.json
```

Web 查看（需 Node.js）：

```bash
cd backend/evals/case/testpoints   # 或 rag，与 --phase 对应
npx promptfoo@latest view
```

控制台若打印了 `eval id`，在 promptfoo UI 里按该 id 定位本次跑分。

### 深度研究（DeepResearch Bench 中文 5 题子集）

题库 `fixtures/tasks-5.json`（HuggingFace `muset-ai/DeepResearch-Bench-Dataset` 确定性子集），SuperAgent 逐题生成调研报告，产物：

```
evals/agent/deepresearch/results/<tag>/
  articles.jsonl  # 每行一题：{ "id", "prompt", "article" }
  summary.json
```

`articles.jsonl` 可直接喂官方 RACE/FACT 判分脚本（评测线暂未接入判分，先产出报告原文）。

```bash
NOESIS_WEB_PROXY=http://127.0.0.1:7897 \
uv run python -m evals.agent.deepresearch --tag smoke-1p --limit 1
```

- 被测对象经 noesis CLI 子进程驱动（每题一个进程，`SANDBOX_BACKEND=local_shell`）；被测模型走 env 直连：`evals/.env` 配 `NOESIS_API_KEY` / `NOESIS_BASE_URL` / `NOESIS_MODEL`，`--model-id` 即端点真实模型名
- `--eval-user` 须为真实账号（默认 `test`）：子 Agent 会话血缘按 user_id 落库，假用户名会导致派发失败、主 Agent 单干
- `NOESIS_WEB_PROXY` 为 web_fetch 的代理回退（直连失败时自动走代理重试）
- 子 Agent 前台等待窗口经 `SUBAGENT_*_SECONDS` env 注入（评测单回合无通知回合，等待窗口须长于生产默认）
- trace：配置 `evals/.env` 后可在 Langfuse 按 `eval_tag=<tag>`、`eval_line=agent` 筛选

**未**集成 promptfoo 类 viewer。

### 消息压缩

```
evals/compression/results/<tag>/summary.json
```

```bash
uv run python -m evals.compression --tag after-tweak --compare-to results/compress-baseline
```

### 知识库检索

产物落盘 `evals/kb/results/<tag>/`（manifest / raw / summary.json / summary.md），控制台同步打印汇总：

```bash
uv run python -m evals.kb.erb --all --tag baseline
cat evals/kb/results/baseline/summary.md
```

### 负载测试（Locust）

压测进行中访问 Locust 自带 UI：`http://localhost:8089`。结束后仅保留终端 `--only-summary` 输出，无项目内持久化结果目录。

---

## 1. 测试用例（`evals.case` + promptfoo）

指标：**阶段 A**（L0、`point_coverage_recall`、`point_coverage_precision`）、**阶段 B**（两路 RAG Recall@3/Hit@3、`document_context_present`）。

```
evals/case/
  README.md
  report.py                   # 跑分后汇总指标、写 summary
  results/<tag>/              # 默认 promptfoo JSON + *-summary.json
  testpoints/
    golden/                   # 金标准源（prd_*.yaml）
    golden_loader.py
    generate_eval_dataset.py  # 从 documents/ + golden/ 生成 promptfooconfig
    promptfooconfig.yaml      # 运行时配置（由脚本生成）
    documents/
  rag/
    promptfooconfig.yaml
    corpus/test_cases/
    ingest.py
  shared/                     # assertions、judge
```

```bash
uv run python -m evals.case --phase testpoints --tag baseline
uv run python -m evals.case --phase stage-a --tag baseline   # 同上别名
uv run python -m evals.case --phase rag --tag rb-baseline
uv run python -m evals.case --phase stage-b --tag rb-baseline # 同上别名
uv run python -m evals.case.rag.ingest --map-only
uv run python -m evals.case.rag.ingest --reset
uv run python -m evals.case --phase testpoints --tag debug --item-id prd_001
```

阶段 A 金标准源在 `testpoints/golden/*.yaml`；运行时写入 `promptfooconfig.yaml` 的 `golden_test_points_json`。**不**使用 `dataset.jsonl`。跑分结束后默认写入 `results/<tag>/` 并在控制台打印 recall/precision 汇总。

RAG 集成测（pytest，默认 skip）：`NOESIS_CASE_RAG_EVAL=1` + 先 `evals.case.rag.ingest`。

coverage 走 Python 确定性 scorer（`shared/coverage_scorer.py`）；borderline 可启用 LLM 仲裁。详见 `evals/case/README.md`。

---

## 2. Agent 评测（DeepResearch + Agentic RAG）

个人学习与日常回归推荐 **两条主线**：

1. **DeepResearch Bench 子集** — 开放调研任务（`SuperAgent` / 深度研究能力）
2. **Agentic RAG** — 验证 GeneralQAAgent 经 core KB Tool/Port 检索并引用期望来源

```
evals/agent/
  runtime.py                # 公共事件 Collector 与 run manifest
  _agent.py                 # SuperAgent 共用执行
  deepresearch/
    __main__.py               # uv run python -m evals.agent.deepresearch
    fixtures/tasks-5.json
    results/<tag>/
  rag/
    __main__.py             # GeneralQAAgent + core KB Tool
    fixtures/sample.jsonl
```

### DeepResearch（中文 5 题子集）

题库来源、运行方式与产物见上文「各评测线如何查看结果 → 深度研究」；官方 RACE/FACT 判分暂未接入，当前产出报告原文供外部脚本判分。

### Agentic RAG → Agent E2E（官方 ERB 口径）

```bash
# ① 跑被测：HTTP 驱动生产 server API（登录 → 建会话 → 创建 run → 消费 SSE）。
#    会话/消息/工具调用由 server 持久化，--eval-user 账号（默认 test）前端可见；
#    server 须已运行（scripts/run.sh dev，默认 127.0.0.1:8089）。
#    --model-id 为「provider_slug/模型名」复合 id（设置页自定义模型，key 加密存 DB）。
uv run python -m evals.agent.rag --sample 10 \
  --model-id "huoshan/glm-5.3-flash" --tag t1
# 中断后续跑（同 tag：已完成题自动跳过）；--retry-failed 只重跑 error 题
uv run python -m evals.agent.rag ... --tag t1 --resume
```

数据集 `fixtures/erb211.jsonl`（由 `uv run python -m evals.agent.rag.build_dataset` 从 ERB 生成，211 题）。本脚本只跑被测链路并自动导出 ERB 官方判分格式 `erb_answers.jsonl`（`question_id / answer / document_ids`，检索文档名经 `evals/kb/erb_data/ingest_plan.json` 映射回官方 dsid），不做任何自研判分。

**质量指标由 ERB 官方脚本产出**（github.com/onyx-dot-app/EnterpriseRAG-Bench，判分 prompt 与指标定义均为官方）：

| 官方指标 | 含义 |
|---|---|
| Correctness（回答正确率） | 回答与标准答案二值比对（判分前官方先剥除回答中的引用标记） |
| Completeness（要点完整率） | `answer_facts` 逐条判「回答是否包含或蕴含」，取比例 |
| Document Recall@10（检索命中率） | 提交的 document_ids 覆盖金标文档的比例 |
| Invalid Extra Documents（平均噪声文档数） | 提交的多余文档经裁判分类后计无关个数（绝对数，故意不用 precision） |
| info_not_found 拒答（负样本） | 是否承认信息不存在而非编造 |

官方判分脚本已移植进仓库：`evals/agent/rag/erb_scorer/`（来源与适配说明见其 README：官方 commit `d36685e`，判分 prompt 与指标算法零改动；含 532 篇官方格式语料与 231 题金标子集，自包含可独立运行）。判分（裁判模型自定，官方论文口径 GPT-5.4，结果须随数声明裁判）：

```bash
cd evals/agent/rag/erb_scorer && LLM_PROVIDER=openai LLM_API_KEY=<key> \
  LLM_BASE_URL=<gateway> LLM_MODEL_NAME=<judge-model> \
  uv run --with openai --with 'pydantic[email]' --with tiktoken \
  --with pyyaml --with python-dotenv --with pyarrow \
  python -m src.scripts.answer_evaluation.metrics_based_eval \
  --answers-file <run 目录>/erb_answers.jsonl --parallelism 6
```

归因由官方指标天然覆盖：检索命中率低 = 检索问题；命中率高但正确率低 = 回答问题。逐题增量落盘 `raw.jsonl`（同一 sample_id 后写覆盖先写），长跑中断不丢已完成题。

---

### 记忆召回（LongMemEval 三层指标）

```bash
# 成本试跑：先 --sample 3 确认链路（单题 haystack 导入 + SuperAgent 全程
# 约 30-50 万 input token），再上 20+ 题出基线
# 被测模型走 env 直连：evals/.env 配 NOESIS_API_KEY / NOESIS_BASE_URL /
# NOESIS_MODEL，--model-id 即端点真实模型名（无 provider 前缀）
uv run python -m evals.agent.memory \
  --model-id glm-5.3-flash \
  --judge-model-user admin --judge-model-id openai/stepfun/step-3.7-flash:free \
  --tag t1 [--sample 30] [--negative-every 5]
# 旧四场景冒烟（不依赖 LongMemEval 数据）
uv run python -m evals.agent.memory --mode smoke \
  --model-id glm-5.3-flash --judge-model-user admin \
  --judge-model-id openai/stepfun/step-3.7-flash:free
```

被测对象经 noesis CLI 子进程驱动（每题一个进程，`SANDBOX_BACKEND=local_shell`
不产生 runner 沙箱容器）：评测与被测之间只隔 argv + env + stream-json，
工具轨迹采集、wall-clock 超时与部分结果保留由 `evals/agent/cli_driver.py`
负责。judge 在宿主机侧单次 LLM 调用，沿用用户模型绑定（`--judge-model-user`）。

数据集 LongMemEval（v1，S 档，HuggingFace 公开 500 题）：首次运行自动下载到 `evals/agent/memory/data/`（gitignored，约 270MB；走 `HTTPS_PROXY` 等环境代理）。每题的 haystack 会话导入该题专属的隔离评测用户（upsert 幂等，不碰真实用户数据），SuperAgent 提问后按三层报告：

1. **答案正确性**：judge 对 gold answer 判卷（复用 E2E 判卷口径，部分采纳折半口径并列报告）；超时题只要有 final_text 仍判卷
2. **检索命中**：`search_memory` 返回条目对 `answer_session_ids` 的 recall@k / precision@k
3. **行为级召回**：需要记忆线索的题，Agent 是否主动访问记忆——`search_memory` 工具调用或 `/memory` 虚拟路径读取（SuperAgent 两条合法路径，实跑发现 agent 偏爱直读）

负例为自建配对场景（S 档无拒答类题型）：无记忆线索的提问断言两条路径都未走且回答未引用种子事实。行为级断言是公开基准都不覆盖的层（AML 平台代调 Search、LoCoMo/LongMemEval 纯检索问答），这层只有自研。

单题时间预算默认 600s（haystack 大、SuperAgent 多轮检索，实测 240s 大概率超时；超时在 summary 按 `timeout` 单独计数，与链路错误 `agent_error` 区分）。

---

### 消息压缩（三组对照，真 Agent 路径，recall% @ retained tokens）

评测线上同款压缩链路：压缩是否丢事实、丢多少。**作答侧于 2026-09-08 切换为真 Agent 路径**
（此前为自造作答循环 + 三套评测专用提示词，旧 `results/` 与新口径不可比）：作答即生产
SuperAgent（真系统提示词、真实压缩、生产 `search_history`），无任何评测专用提示词。

```
fixture 按生产行状落库（t_chat_session + t_chat_message，search_history 的真实数据源）
       → 消息规范化后 aupdate_state 灌入 checkpoint → 一条真实消息触发线上 CompactionMiddleware
       → 每题把「压缩后状态」灌到独立 thread（同题同摘要，题间互不污染）→ 真 Agent 作答
       → judge 2/1/0 + 五维诊断 → recall% @ retained tokens + 分层召回
       + 任务保持率 Δ（压缩组 − uncompacted 组）+ 检索兜底收益（recovery − current）
```

```bash
uv run python -m evals.compression --tag t1 \
  --model-id <作答模型> --judge-model-id <判卷模型> [--arms uncompacted,current,recovery]
# 只跑微观层（成本试跑常用；题库分层见下方 gen_probes）
uv run python -m evals.compression --tag t1 --fixture cc-0146daeb --layer detail \
  --arms uncompacted,current,recovery --model-id <m> --judge-model-id <j>
```

- **三组配置矩阵**（见 `agent_path.ARM_FLAGS`，严格单变量对照链）：
  `uncompacted` = 完整原文 + 压缩关闭 + 无会话检索（原生召回上限，与 `current` 只差压缩）；
  `current` = 压缩后历史 + 无会话检索（旧线上形态，与 `recovery` 只差检索）；
  `recovery` = 压缩后历史 + 生产 `search_history`（新线上形态）
- 压缩由**线上 `/compact` 宿主路径**触发（`build_compaction_middleware` + `acompact_state`，
  与 `compact_session` 服务同构，仅模型绑定来自评测快照）：显式命令语义，无合成消息、
  不依赖阈值；产物写回 checkpoint 后由 current/recovery 共享（对照只差工具）
- **judge 解析失败**：重试一次后剔除出分母并单列 `judge_parse_error_rate`，不记 0 分
- 摘要模型：`get_llm(purpose="summarization")`，需 `summarization.enabled=true`；评测经
  `--model-user` 绑定时以 `include_summarization` 与作答同模型
- token 口径：chars/4（content + tool_calls 序列化长度），写进 manifest；作答/触发轮真实
  usage 由生产事件流采集
- 摘要识别只认中间件写入的结构化标记（`lc_source=summarization`），不猜内容
- 压缩一律走 `agent_path` 的 /compact 宿主触发；旧离线压缩路径（中间件直调 + 自建 summarize 接线）已删——两套实现会漂移。fixture 解析与 chars/4 token 口径在 `fixture_loader.py`

**评测集来源**（真实长会话）：

```bash
# 从本地 Claude Code 会话导出脱敏 transcript（--list 列最大会话）
uv run python -m evals.compression.export_session --list
uv run python -m evals.compression.export_session <session.jsonl> --out fixtures/real/<id>.json
# 从「将被压缩区域」生成分层事实 recall 题库（按 transcript hash + 出题 prompt 版本缓存）
# 题目分三层：macro（目标/结论/决策，摘要必须保留）、meso（文件级改动与根因）、
# detail（错误串/数值/原文措辞，通常需检索兜底）；报告按层分别报召回
uv run python -m evals.compression.gen_probes --fixture <id> [--questions 12]
```

导出脱敏为规则级（邮箱/key/绝对路径占位替换），产物必须人工过审后才可作为 fixture。旧三个合成 fixture 保留为手写题库档；`evals/compression/synthetic.py` 提供可种植事实的零 LLM 冒烟（CI 回归压缩机制，不依赖真实数据）。

环境变量：`NOESIS_COMPRESSION_EVAL_TAG`、`NOESIS_COMPRESSION_EVAL_FIXTURE`、`NOESIS_COMPRESSION_EVAL_RUNS`。

集成测试（默认 skip）：

```bash
NOESIS_COMPRESSION_EVAL_INTEGRATION=1 uv run pytest tests/test_eval_compression_integration.py -q
```

---

## 4. 深度研究负载测试（`evals.loadtest` + Locust）

对运行中的后端发 HTTP 请求，压测 `SUPER_AGENT_QA` SSE 链路（与离线 eval 不同，走真实 API）。

```
evals/loadtest/
  locustfile.py
  sse_client.py
  queries.py
  data/queries.jsonl    # 5 条压测 query
  __main__.py           # 打印运行说明
```

```bash
uv sync --extra loadtest
uv run python -m evals.loadtest
uv run locust -f evals/loadtest/locustfile.py --host=http://127.0.0.1:8089
uv run locust -f evals/loadtest/locustfile.py --host=http://127.0.0.1:8089 \
  --headless -u 1 -r 1 --run-time 30m --only-summary
```

单用户（admin）、每请求新 session；客户端不设超时，等后端 SSE 自然结束。指标：`deep_research_stream`（端到端）、`deep_research_ttft`、`deep_research_tool_calls`。

---

## 5. 知识库检索（`evals.kb.erb`）— ERB 企业级基准

EnterpriseRAG-Bench（Onyx）子集：**211 正样本题**（GT 全部在语料内）+ **20 info_not_found 负样本**，语料 `erb-eval` 集合（566 篇 = 首批 312 GT + 220 confluence 干扰 + 2026-09-17 补充 34 篇官方 Conflicting Info 题金标，文件名已转短名，`ingest_plan.json` 为语料清单与 dsid 映射）。补充导入用 `evals/kb/erb_supplement.py`（生产入库管道，幂等）。扩充题集 `fixtures/erb238.jsonl`（211 + 17 道 Conflicting Info，跑 Agent E2E 时 `--dataset evals/agent/rag/fixtures/erb238.jsonl`；默认数据集仍为 erb211 保持旧基线可复现）。官方 High Level 题（10 道）无金标文档，暂未纳入题池。

```bash
cd backend
uv run python -m evals.kb.erb --sample 2 --tag smoke   # 抽样冒烟（正/负各一）
uv run python -m evals.kb.erb --all --tag baseline     # 全量 211+20，rerank 成本 ~2 元
```

- 指标：Recall@1/3/5/10、**MRR（miss 计 0 进分母）**、**nDCG@10（多 GT 折算、同文档多 chunk 去重）**、阈值离线模拟（正样本 GT 存活 × 负样本拒答，档位 0.30~0.0，窗口默认 top-10 可调）、负拒率；headline 指标附 bootstrap 95% CI（固定种子）
- 设计：单次检索记录原始 rerank 分（`score_threshold=0`），阈值效果离线模拟，不重复调用 API
- 产物：`evals/kb/results/<tag>/`（manifest/raw/summary 四件套；raw 含每题原始分，阈值实验可离线复算）
- 数据集：`evals/kb/erb_data/`（gitignored；`ERB_DATA_DIR` 可覆盖）。语料入库用集合级 `chunk_size=2000/overlap=200`，阈值 `score_threshold=0.05`
- 与 `evals.case --phase rag` / `evals.agent.rag`（场景级 Agent E2E）互补；归因时把 kb 线 raw.json 经 `--kb-results` 喂给 E2E 线

`sse_client.consume_sse_stream` 读到 `data: [DONE]` 才计为成功端到端（与前端一致）；**提前断开**不影响服务端 partial 落库（见 `docs/engineering/platform/chat-streaming.md`）。压测验证落库时请查 `t_chat_message` 同一 session 仅一条 assistant 行。
