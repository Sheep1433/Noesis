# 评测

`evals` 为评测包根目录，**不直接跑分**。各场景使用独立子模块：

| 场景 | 命令 | 状态 |
|------|------|------|
| 测试用例 Agent | `uv run python -m evals.case` | 已实现 |
| Agent / BrowseComp | `uv run python -m evals.agent.browsecomp` | 已实现 |
| Agent / Terminal-Bench | `./evals/agent/harbor/run-noesis.sh` | 已实现 |
| Agent E2E（判卷/引用/归因） | `uv run python -m evals.agent.rag` | 已实现 |
| 记忆召回（LongMemEval） | `uv run python -m evals.agent.memory` | 已实现 |
| 消息压缩（多臂对照） | `uv run python -m evals.compression` | 已实现 |
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
| BrowseComp | `evals/agent/browsecomp/results/<tag>/` | **无** | `summary.json` / `convos.jsonl`；可选 Langfuse trace |
| Terminal-Bench（Harbor） | `evals/agent/harbor/results/<job>/` | **有**（Harbor） | `harbor view evals/agent/harbor/results/<job>` |
| Agent E2E | `evals/agent/rag/results/<tag>/` | **无** | `summary.md` + `attribution.md`（失败归因） |
| 记忆召回 | `evals/agent/memory/results/<tag>/` | **无** | `summary.md`（三层指标） |
| 消息压缩 | `evals/compression/results/<tag>/` | **无** | `summary.md`；`--compare-to` 对比历史 |
| 知识库检索 `evals.kb.erb` | `evals/kb/results/<tag>/` | **无** | `summary.md`（Recall/MRR/nDCG/阈值表 + CI） |
| 负载测试 Locust | Locust Web（运行时） | **有**（Locust） | `http://localhost:8089`（仅压测进行中） |

所有线共用统一产物结构：`results/<tag>/{manifest.json, raw(.jsonl), summary.json, summary.md}`——
manifest 记录模型/数据集/种子/配置/token 成本/git sha；**tag 复用被拒绝**（历史基线不覆盖，换新 tag）。
含 LLM-as-judge 的线另落盘 `manual_review_queue.json`（固定种子 10% 人工抽检清单）。
判卷模型必须与被测模型不同（`--judge-model-id` 必填，相同即拒跑）。

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

### BrowseComp（无专用结果页）

**整批题目跑完后**才写入（跑的过程中目录可能为空）：

```
evals/agent/browsecomp/results/<tag>/
  summary.json    # accuracy、耗时、题数
  convos.jsonl    # 每行一题：{ "index", "convo": [user, assistant] }
```

```bash
# 汇总
jq . evals/agent/browsecomp/results/bc-smoke-new-12/summary.json

# 逐题（每行一题）
jq . evals/agent/browsecomp/results/bc-smoke-new-12/convos.jsonl
```

终端结束时会打印 `BrowseComp accuracy: ...` 与 `Results: ...` 路径。

**过程/trace（非成绩表）**：配置 `evals/.env` 后可在 Langfuse 按 `eval_tag=<tag>`、`eval_line=agent` 筛选；整次 session 为 `browsecomp-<tag>`，单题为 `browsecomp-<uuid>`。单题工作区与卸载文件在 `.noesis/users/eval-browsecomp/sessions/`。

BrowseComp 走仓库内 Python 模块（`uv run python -m evals.agent.browsecomp`），直接调 Noesis `SuperAgent`，因此**未**集成 Harbor / promptfoo 类 viewer。

### Terminal-Bench（Harbor 自带 Web UI）

Harbor 通过 **外部 CLI + Docker** 跑题（`./evals/agent/harbor/run*.sh` → `harbor run`），产物在：

```
evals/agent/harbor/results/<job-name>/
```

本地 Web 查看（Harbor 提供，非 Noesis 前端）：

```bash
cd backend
harbor view evals/agent/harbor/results/<job-name>
```

**Harbor 残留容器**：任务结束后部分容器会以 `sleep infinity` 保持运行，便于查看挂载日志。评测结果已落在 `results/<job>/` 且不再调试时，可停止并删除，例如：

```bash
docker stop <container-id> && docker rm <container-id>
# 或批量清理已退出的 Harbor 相关容器
docker container prune
```

镜像 `alexgshaw/*` 为 Terminal-Bench 官方任务环境，删除容器**不会**删镜像；下次 `harbor run` 会按需复用本地镜像。

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

## 2. Agent 评测（BrowseComp + Harbor + Agentic RAG）

个人学习与日常回归推荐 **两条主线**：

1. **BrowseComp** — 多步检索 + 短答案（`SuperAgent` / 深度研究能力）
2. **Harbor + Terminal-Bench** — 终端任务执行（`harbor view` 看轨迹）
3. **Agentic RAG** — 验证 GeneralQAAgent 经 core KB Tool/Port 检索并引用期望来源

```
evals/agent/
  runtime.py                # 公共事件 Collector 与 run manifest
  _agent.py                 # SuperAgent 共用执行
  browsecomp/
    official.py
    __main__.py               # uv run python -m evals.agent.browsecomp
    results/<tag>/
  harbor/
    run-noesis.sh             # Noesis SuperAgent
    run-opencode.sh           # OpenCode 对照组
    README.md
    results/<job-name>/
  rag/
    __main__.py             # GeneralQAAgent + core KB Tool
    fixtures/sample.jsonl
```

### BrowseComp

```bash
uv run python -m evals.agent.browsecomp \
  --tag bc-smoke --num-examples 5 --model-id flash
```

首次运行会从官方 URL 下载 CSV 并缓存到 `evals/agent/browsecomp/data/`；离线重跑复用缓存。也可设置 `BROWSECOMP_CSV_PATH` 指向本地文件。

官方 CSV + `BrowseCompEval` → 指标 **accuracy**。结果：`browsecomp/results/<tag>/summary.json`。

### Terminal-Bench（Harbor）

```bash
cd backend
./evals/agent/harbor/run-noesis.sh          # 单题
./evals/agent/harbor/run-noesis.sh cli-10   # 10 题
harbor view evals/agent/harbor/results/noesis-cli-10
```

OpenCode 对照组将脚本替换为 `run-opencode.sh`。产物：`evals/agent/harbor/results/<job-name>/`。

### Agentic RAG → Agent E2E（判卷 / 引用溯源 / 失败归因）

```bash
uv run python -m evals.agent.rag \
  --sample 10 --model-id <catalog-model-id> --judge-model-id <judge-model-id> --tag t1
# 中断后续跑（同 tag：已完成题自动跳过）；--retry-failed 只重跑 error 题
uv run python -m evals.agent.rag ... --tag t1 --resume
```

数据集 `fixtures/erb211.jsonl`（由 `uv run python -m evals.agent.rag.build_dataset` 从 ERB 生成，211 题），每行含 `query / expected_sources / expected_doc_ids / gold_answer / answer_facts`。每题跑完整 `GeneralQAAgent → search_knowledge_base → KbRetrievalService` 链路，产出：

- **任务成功率**：judge 按 gold_answer 三档判卷（采纳/部分采纳/不采纳），summary 同时给全量与折半口径
- **引用溯源三指标**：格式遵循率（citation 契约 + 伪协议头/URL 编码回归断言）、引用正确率（引用 ∈ GT，确定性）、事实可溯源率（answer_facts 逐条 judge）
- **失败归因**（`attribution.md`）：不采纳题关联检索命中（`--kb-results` 指向 kb 线 raw.json，缺则现场补检索）+ 工具轨迹，确定性规则归到「检索没召回 / 工具行为异常 / 推理错 / 待人工复核」

逐题增量落盘 `raw.jsonl`（同一 sample_id 后写覆盖先写），长跑中断不丢已完成题。

---

### 记忆召回（LongMemEval 三层指标）

```bash
uv run python -m evals.agent.memory \
  --model-id <m> --judge-model-id <j> --tag t1 [--sample 30] [--negative-every 5]
# 旧四场景冒烟（不依赖 LongMemEval 数据）
uv run python -m evals.agent.memory --mode smoke --model-id <m> --judge-model-id <j>
```

数据集 LongMemEval（v1，S 档，HuggingFace 公开 500 题）：首次运行自动下载到 `evals/agent/memory/data/`（gitignored，约 270MB；走 `HTTPS_PROXY` 等环境代理）。每题的 haystack 会话导入该题专属的隔离评测用户（upsert 幂等，不碰真实用户数据），SuperAgent 提问后按三层报告：

1. **答案正确性**：judge 对 gold answer 判卷（复用 E2E 判卷口径）
2. **检索命中**：`search_memory` 返回条目对 `answer_session_ids` 的 recall@k / precision@k
3. **行为级召回**：需要记忆线索的题，Agent 是否主动访问记忆——`search_memory` 工具调用或 `/memory` 虚拟路径读取（SuperAgent 两条合法路径，实跑发现 agent 偏爱直读）

负例为自建配对场景（S 档无拒答类题型）：无记忆线索的提问断言两条路径都未走且回答未引用种子事实。行为级断言是公开基准都不覆盖的层（AML 平台代调 Search、LoCoMo/LongMemEval 纯检索问答），这层只有自研。

---

### 消息压缩（多臂对照，recall% @ retained tokens）

评测线上同款 `CompactionMiddleware`：压缩是否丢事实、丢多少。**headline 口径于 2026-09-03 切换**
（旧五维 rubric 分 → recall% @ retained tokens，judge 2/1/0 判卷），旧 `results/` 与新口径不可比，
新基线从切换后起算。五维保留为诊断维度，只进逐题 raw 记录。

```
fixture → 每臂（uncompacted 对照 / 压缩策略档）→ 闭卷 probe 作答 → judge 2/1/0 + 五维诊断
       → recall% @ retained tokens + 任务保持率 Δ（压缩臂 − uncompacted 臂）
```

```bash
uv run python -m evals.compression --tag t1 \
  --model-id <作答模型> --judge-model-id <判卷模型> [--arms uncompacted,current,aggressive]
uv run python -m evals.compression --tag tweak --fixture debug_session --runs 3 \
  --model-id <m> --judge-model-id <j>
```

- **评测臂**：`--arms` 逗号分隔，`uncompacted` 为不压缩直接闭卷作答的 recall 上限；其余为策略名（`policies.py` 预设：current / aggressive / keep-10）
- **judge 解析失败**：重试一次后剔除出分母并单列 `judge_parse_error_rate`，不记 0 分
- 摘要模型：`get_llm(purpose="summarization")`，需 `summarization.enabled=true`
- token 口径：chars/4（content + tool_calls 序列化长度），写进 manifest
- 摘要识别只认中间件写入的结构化标记（`lc_source=summarization`），不猜内容

**评测集来源**（真实长会话）：

```bash
# 从本地 Claude Code 会话导出脱敏 transcript（--list 列最大会话）
uv run python -m evals.compression.export_session --list
uv run python -m evals.compression.export_session <session.jsonl> --out fixtures/real/<id>.json
# 从「将被压缩区域」生成事实 recall 题库（按 transcript 内容 hash 缓存，fixture 变更后重生成）
uv run python -m evals.compression.gen_probes --fixture <id> [--questions 15]
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

EnterpriseRAG-Bench（Onyx）子集：**211 正样本题**（GT 全部在语料内）+ **20 info_not_found 负样本**，语料 `erb-eval` 集合（312 GT + 220 confluence 干扰，文件名已转短名，`ingest_plan.json` 为语料清单与 dsid 映射）。

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
