# 评测集设计：压缩 · 记忆 · Agent

> 状态：Draft（评估集设计建议，未落地；落地改代码前先评审）
> 范围：`backend/evals/compression/`、`backend/evals/agent/memory/`、`backend/evals/agent/rag/`
> 运行入口：见 `docs/engineering/agents/agent-evaluation.md`

本文回答「评测集应该怎么设计：fixture/probe/dataset 覆盖什么、测什么断言、指标怎么算」，面向把这几条评测线的数字写进简历的场景。运行命令与排障不在这里重复，见 `agent-evaluation.md`。

## 目标与叙事

简历需要的三类可量化证据，对应三条评测线：

| 求职叙事 | 评测线 | 要的核心数字 |
|----------|--------|-------------|
| 长会话工程手段 | 压缩 `evals.compression` | 五维 0–5 分、压缩比、压缩后续答质量 |
| Agentic 记忆（该召回时主动召回） | 记忆 `evals.agent.memory` | 召回纪律 recall、检索命中质量 |
| RAG 回答带来源（引用溯源） | Agentic RAG `evals.agent.rag` | KB 调用率、来源召回 |

BrowseComp / Harbor 是**官方 benchmark**（BrowseComp 从官方 URL 下载 CSV、Harbor 用官方任务环境），评测集不自建、无法改题，只做回归基线——不在本文设计范围内，仅记录「无评测集设计空间」这一结论供简历叙事使用（跑官方 benchmark 本身就有说服力）。

---

## 1. 压缩评测集（`evals.compression`）

### 现状（已较完整）

- 3 个 fixture：`debug_session`（排错）、`feature_impl`（功能实现）、`config_build`（配置调参），每条 8–12 轮消息、含大段 tool 输出。
- 每条对应一组 10 个 probe（`probes/<fixture>.probes.json`），probe type 分四类：`recall` / `artifact` / `decision` / `continuation`。
- 指标：五维 0–5 LLM Judge（`accuracy` / `artifact_trail` / `context_awareness` / `continuity` / `completeness`），fixture 得分取各 probe 中位数；`--runs N` 取多次中位数降噪。
- 已有 `--compare-to` 支持与历史对比。

### 评测集设计要点与缺口

**① probe 类型覆盖已对这四条线的核心能力**——probe 类型就是「压缩后不该丢什么」的四类语义：
- `recall`：关键事实（数值、错误码、根因）
- `artifact`：路径、配置文件、工具名（可追溯性）
- `decision`：决策与理由（为什么放弃 A 选 B）
- `continuation`：下一步动作（能否继续未完成工作）

这四类恰好映射五维里的 `accuracy` / `artifact_trail` / `context_awareness` / `continuity` / `completeness`，设计是自洽的。

**② 建议每 fixture 的 probe 补齐一个「抗噪声」类目**。现有 probe 几乎全是「应命中」的正例，缺少**负例**（该回答「摘要未保留/无法确认」的场景）。压缩摘要合理地不保留所有细节，Judge 若对「诚实说不知道」扣分，会低估真实可用性。建议每个 fixture 增加 1–2 个 probe：

```json
{
  "id": "p11",
  "type": "continuation",
  "question": "日志里最早的错误发生在第几行？",
  "reference_answer": "摘要未保留行号，应诚实说明无法确认，而不是臆造"
}
```

这类 probe 的 Judge prompt 已内置「若摘要缺少具体事实请明确说明」，但 reference 语义与「应命中」probe 不同，需要标注 `"expect_honest_uncertain"`，并在评分时允许该维度得高分（而非按事实缺失扣成 0）。这能同时测出「压缩是否过度、是否保留到可延续」与「模型是否臆造」两端。

**③ 压缩比与质量需分开看，缺一个「token 预算」正交维度**。现在压缩比只反映「压了多少」，不反映「接近目标预算多少」。建议 fixture 里显式标注 `target_compression_ratio` 或 `target_post_tokens`，跑完后除压缩比外报告「距目标的偏差」，避免出现「压缩比高但质量崩塌」或「质量高但没压缩」两种表里不比的情况。

**④ 建议固化一个 baseline tag 并重复跑**。现有 results 目录为空，从未落盘。第一次跑用固定模型、固定种子 tag（如 `compress-baseline-<model>`），作为 `--compare-to` 的历史锚点——压缩改动前后的对比正是这个评测线最有价值的使用方式。

### 数据文件样例（新增 probe 形态）

`backend/evals/compression/probes/debug_session.probes.json` 追加：

```json
{
  "id": "p11",
  "type": "continuation",
  "expect_honest_uncertain": true,
  "question": "app.log 里连接池耗尽的报错最早出现在第几行？",
  "reference_answer": "摘要未保留具体行号，应明确说明无法从已保留信息确认"
}
```

配套改动（落地时）：`grader.py::grade_single_probe` 传 `expect_honest_uncertain` 到 build_judge_prompt，Judge prompt 增加一行说明「对 expect_honest_uncertain 项，诚实说明未保留应得高分；臆造具体数字应重扣」。

---

## 2. 记忆评测集（`evals.agent.memory`）

### 现状（骨架完整，语义单薄）

- 素材：`SEEDED_ENTRIES` 4 条种子记忆（preference / decision / experience / goal 各一）+ `RECALL_SCENARIOS` 4 个应召回场景（每类记忆对应 1 个 query）。
- 指标：`recall_rate` = 每个样本 `search_memory_calls > 0` 的比例。这是**行为断言**（该不该检索）。

### 核心缺口与设计

**① 现有断言只测「召回了」，没测「召回了对的」。** 一个调 `search_memory` 但搜到无关条目的 Agent，`recalled=true` 照样通过。简历里「记忆召回」真正要的数字是**检索命中质量**，而当前评测给不出。

**② 增加「命中质量」断言，需要把工具输出纳入判定。** `search_memory` 返回的 `results[]`（`rel_path`、`memory_type`、`slug`、内容）已经在 `run_memory_recall_sample` 里被 run 的事件 collector 捕获（与 `evals.agent.rag` 的 `retrieved_sources` 同机制），可以在 runner 里多取一个 `expect_entry`（种子条目的 slug），断言检索结果里是否包含该条目：

```python
# runner.py 新增——从工具输出解析命中条目
def _memory_hits(tool_outputs):
    hits = set()
    for item in tool_outputs:
        if item.get("name") != "search_memory":
            continue
        payload = json.loads(item.get("output", "{}"))
        for hit in payload.get("results", []):
            hits.add(str(hit.get("rel_path") or ""))
    return hits
```

```
指标 = recall_rate（行为） + hit_rate（每个场景检索结果命中 expect_entry 的比例）
```

**③ 建议把每类记忆的样本从 1 条扩到 3 条**，覆盖一个真实维度：**query 的措辞与记忆条目的字面匹配程度**。种子条目是「文档输出一律表格化」，query 可以是字面（「我文档输出格式要求？」）、意图paraphrase（「写报告时用什么排版？」）、干扰（含关键词但指向另一偏好）。这能测出检索是「字面 grep」还是「语义召回」——这是简历里区分记忆实现深度的重要证据。可利用现成的 `gotcha` 类型或新增一个干扰条目做负样本。

**④ 补充「批量平行场景」避免标签淹没。** 现有 4 个场景标注 `expect_label`（中文 label），用 `expect_label in final_text` 做弱语义判断。改为命中 `rel_path`/`slug` 后，`final_text` 判定可保留但降级为辅助。

### 数据文件样例（新增场景）

`backend/evals/agent/memory/fixtures.py` 的 `RECALL_SCENARIOS` 扩为：

```python
{
  "id": "preference-doc-format-literal",
  "query": "我之前对文档输出格式有什么要求？直接给结论。",
  "expect_label": "文档格式",
  "expect_entry": "preference/document-format.md",
},
{
  "id": "preference-doc-format-paraphrase",
  "query": "写周报和说明文档的时候，我习惯什么排版？",
  "expect_label": "文档格式",
  "expect_entry": "preference/document-format.md",
},
{
  "id": "preference-doc-format-distractor",
  "query": "有没有定过表格化输出的偏好？",
  "expect_label": "",
  "expect_entry": "",
},
```

配套改动（落地时）：`SEEDED_ENTRIES` 的 `slug_hint` 提供稳定 slug，`run_memory_recall_sample` 采集 `search_memory` 工具输出并按 `expect_entry` 算 `hit`。

---

## 3. Agentic RAG 评测集（`evals.agent.rag`）

### 现状（评测集还是占位符）

`fixtures/sample.jsonl` 只有一行 `"replace-me"` 占位，**尚未设计任何真实评测集**。这是三条线里最空的一块，也是简历「引用溯源」叙事最需要的一块。runner / scoring 已完备（`kb_tool_called` + `expected_sources` 的 `source_recall`），缺的只是真实 dataset。

### 评测集设计

**① 每行样本三要素**（已有 schema，需填真实值）：

```jsonl
{"id":"...","query":"...","collection_names":["..."],"expected_sources":["..."]}
```

- `query`：一个需走知识库才能完整回答的问题，措辞上不泄露答案关键词。
- `collection_names`：该回答应检索的集合。
- `expected_sources`：正确答案所依赖的文件名（Qdrant hit 的 `file_name`），**用于 source_recall 的引用溯源断言**。

**② 覆盖维度**——匹配 GeneralQA 的 RAG 能力边界，建议至少 6–8 行：

| 维度 | 样例语义 |
|------|---------|
| 单集合直接引用 | 一个知识库文件能直接回答 |
| 跨集合聚合 | 需合并多个集合的文件得出答案 |
| 需 rerank 排序 | 多命中里正确答案不在 top，需 rerank 后提到前面 |
| 无关干扰 query | 不依赖知识库的常识问题，断言 `expected_sources=[]`、KB 调用可低 |
| 答案需综合而非摘抄 | 来源在，但答案要跨段落综合 |
| 边界 / 否定 | 知识库里无答案，期望不臆造、诚实说明 |

**③ 关键设计点：source_recall 锚定的是「应命中的来源」，而非「模型答案正确」。** 这样量的是「引对了原文」，正是简历引用的「引用溯源」。需为每行 `expected_sources` 配上对齐的文档落地到对应 collection（用 `evals.kb` 同一套语料或专用 RAG corpus），否则 Qdrant 里没有这些文件，score 恒低。

**④ 负样本的 KB 调用率语义**：`kb_tool_call_rate` 是把「无关 query 也调 KB」当成功算的——这会稀释「该调时调」的精度。建议报两个口径：`kb_tool_call_rate`（全样本）与 `kb_tool_call_rate_on_kb_queries`（只看应有来源的样本），简历用后者更有说服力。

### 数据文件样例（新增真实 dataset）

`backend/evals/agent/rag/fixtures/sample.jsonl`：

```jsonl
{"id":"kb-single-001","query":"生产环境如何为订单拉起高可用部署？请说明依据来源。","collection_names":["deploy_runbook"],"expected_sources":["high-availability.md"]}
{"id":"kb-cross-002","query":"新到的连接池参数建议要在哪个环境先验证才能上生产？","collection_names":["tuning","deploy_runbook"],"expected_sources":["connection-pool.md","staging-process.md"]}
{"id":"kb-negative-003","query":"今天深圳天气如何？","collection_names":[],"expected_sources":[]}
```

配套改动（落地时）：`__main__.py` 计算新增的 `kb_tool_call_rate_on_kb_queries`，并把对应 collection 的语料通过 `evals.case.rag.ingest` 或 KbRetrievalService 落地。

---

## 落地优先级与验收

| 序 | 改动 | 最小工作量 | 简历产出 |
|----|------|-----------|---------|
| 1 | RAG dataset 真实化（最关键，当前是空壳） | 6–8 行 fixture + 语料入库 | KB 调用率 + source_recall |
| 2 | 记忆 hit 断言 + 场景扩到 3 条/类 | runner 解析工具输出 + fixtures | 召回纪律 + 命中率 |
| 3 | 压缩 baseline tag + 负例 probe | 固话 baseline + 1–2 个 probe/行 | 五维基线分 |
| 4 | 压缩 token 预算维度 | fixture 标 target + report 输出 | 压缩比+质量正交 |

评审通过后按「review 经济学」补测试：`tests/test_eval_agent_memory.py`、`tests/test_eval_agentic_rag.py` 需覆盖新增断言与指标。
