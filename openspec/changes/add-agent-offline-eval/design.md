## Context

- **测试用例线**（已有）：`evals/case/runner.py` + `uv run python -m evals.case` + promptfoo。
- **Agent 线**（缺失）：需跑完整 `DeepResearchAgent`（工具、Skills、子 Agent、工作区写入），评的是轨迹与产物。
- **压缩线**（缺失）：`SummarizationOffloadMiddleware` 在 token 逼近上限时卸载 tool 结果并 LLM 摘要；需 fixture 会话 + probe 验证摘要后是否仍能答对关键事实、保留文件路径与决策。
- **参考**：WildClawBench（Agent 任务与混合评分）；[hermes-compression-eval](https://github.com/NousResearch/hermes-compression-eval)（压缩评测流程）。Noesis **不**接入 WildClawBench Docker Harness。

用户明确：**评测线分离**（测试用例 / Agent / 压缩）；按需手动跑；**always 调真模型**；不做质量门与 CI 冒烟。

## Goals / Non-Goals

**Goals:**

- 提供可重复的 Agent 离线 runner 与压缩评测 runner，均输出 `results/<tag>/` 可对比报告。
- 首版 Agent 数据集 **8–12 条**；首版压缩 fixture **3 条**（调试、功能实现、配置构建类长会话），每条 **8–12 道 probe**。
- 全部使用真实 LLM（摘要、`get_llm(purpose="summarization")`；Judge、`get_llm()` 或 CLI 可配置 judge purpose）。

**Non-Goals:**

- 不集成 WildClawBench 官方容器或四 Harness 对标。
- 不评测 `COMMON_QA` RAG（另线规划）；本 change Agent 线仅 `DEEP_RESEARCH_QA`。
- 不新增 GitHub Actions 评测步骤或阈值门禁。
- 首版不使用 promptfoo 编排 Agent / 压缩评测。

## Decisions

### 1. 目录布局

```
backend/evals/
  __main__.py                 # 场景导航，不跑分
  case/                       # 测试用例评测
    __main__.py               # uv run python -m evals.case
    datasets/test_case/
    promptfoo/
  agent/
    __main__.py               # uv run python -m evals.agent
    datasets/deep_research/
  compression/
    __main__.py               # uv run python -m evals.compression
    fixtures/
    probes/
```

**理由**：`evals` 仅为包根；**每个场景**独立 `python -m evals.<scene>`，目录与 CLI 一一对应。

### 2. 数据集条目 schema（`dataset.jsonl`）

```json
{
  "id": "dr_code_sam3_debug",
  "category": "code_intelligence",
  "query": "……用户可见任务描述……",
  "workspace_seed": "workspaces/dr_code_sam3_debug",
  "time_budget_seconds": 600,
  "ground_truth": {
    "criteria": [
      {"id": "c1", "type": "file_exists", "path": "results/predictions.json"},
      {"id": "c2", "type": "json_field_min", "path": "results/predictions.json", "field": "cases.text_shoe.f1", "min": 0.8}
    ],
    "semantic_rubric": [
      "报告说明了定位到的 Bug 类别与修复思路",
      "最终 predictions 与测试脚本预期一致"
    ],
    "expected_tools": ["terminal", "read_file", "write_file"]
  },
  "provenance": "adapted_from:WildClawBench:02_Code_Intelligence_task_2_sam3_debug"
}
```

- `category`：`search_retrieval` | `code_intelligence` | `creative_synthesis` | `safety_alignment`（首版可仅前三类）。
- `workspace_seed`：runner 启动前复制到本次 eval 工作区（隔离 `.data/agent_workspace/evals/<run_id>/`）。
- `provenance`：记录迁移来源，便于对照 WildClawBench 原文。

### 3. 迁移任务清单（排除生活类）

| id（建议） | 来源类别 | 简述 | 纳入理由 |
|-----------|---------|------|---------|
| `dr_search_wiki_bio` | WildClawBench 检索 | 从维基传记章节提取人物并保存 md | 测 `web_search` + 文件写入 |
| `dr_search_policy_compare` | 自建/检索类 | 对比两份公开技术文档要点 | 测多源检索与归纳 |
| `dr_code_sam3_debug` | WildClawBench 代码 | 修复注入 Bug 使测试通过 | 测读代码 + terminal + 调试 |
| `dr_code_small_api_fix` | 简化代码类 | 修复小型 Python API 单测失败 | 低成本 smoke |
| `dr_report_market_scan` | 创意/合成 | 给定行业关键词输出带引用研究报告 | 测 skill 协议 + 引用 |
| `dr_report_competitive_table` | 创意/合成 | 竞品对比表 + 结论段 | 测结构化输出 |
| `dr_safety_refuse_destructive` | 安全（可选） | 拒绝删除系统目录等指令 | 测守卫，不涉邮件 |

**排除**：邮件发送、日历、即时通讯、电商下单等 WildClawBench productivity/social 类任务。

### 4. Runner 行为

1. 读取 `dataset.jsonl`，按 `--item-id` / `--limit` 过滤。
2. 为每条任务创建隔离工作区：`ensure_workspace_dir` + 复制 `workspace_seed`。
3. 构造 eval 用 `current_user`（固定测试用户 id）与 `session_id=eval-<item_id>-<run_id>`。
4. 调用 `DeepResearchAgent.run_agent`（或抽取的同步 `run_until_complete` 包装），收集：
   - 最终 assistant 文本；
   - `tool_stats`（从 stream 事件或 agent 返回结构汇总）；
   - `latency_ms`、`completed`、`error`。
5. Agent 结束后执行 `scoring.score_item(run_output, ground_truth, workspace_path)`。
6. 流式写入 `results/<tag>/runs/<item_id>.json`（crash-safe）。

**不经过 HTTP**，但工具栈与线上 `DEEP_RESEARCH_QA` 一致。

### 5. 混合评分

| 层级 | `criteria.type` 示例 | 实现 |
|------|---------------------|------|
| 规则 | `file_exists`, `file_contains`, `json_field_min`, `glob_count_min` | 读工作区路径，确定性 |
| 副作用 | `workspace_clean`（除 allowlist 外无多余文件） | 目录 diff |
| 语义 | `ground_truth.semantic_rubric` | 单次 LLM Judge，输入最终报告 + rubric，输出每项 0/1 |

`overall_score = passed_criteria / total_criteria`（语义项计入 criteria 或单独 `semantic_score` 字段，在 spec 中固定为加权平均：规则 70% + 语义 30%）。

Judge **必须**调用 `get_llm()`，不提供 mock 开关。

### 6. CLI

```bash
cd backend
uv run python -m evals.agent --tag baseline
uv run python -m evals.agent --tag try1 --item-id dr_code_sam3_debug
uv run python -m evals.agent --tag try1 --limit 3 --compare-to results/baseline
```

环境变量：`NOESIS_AGENT_EVAL_TAG`、`NOESIS_AGENT_EVAL_ITEM_ID`、`NOESIS_AGENT_EVAL_LIMIT`、`NOESIS_AGENT_EVAL_DATASET`。

环境变量：`NOESIS_AGENT_EVAL_TAG`、`NOESIS_AGENT_EVAL_ITEM_ID`、`NOESIS_AGENT_EVAL_LIMIT`、`NOESIS_AGENT_EVAL_DATASET`。

### 7. 消息压缩评测（`evals/compression/`）

**评测对象**：`SummarizationOffloadMiddleware`（`create_summary_offload_middleware` + `before_model` 摘要路径）。不经过完整 Agent 循环，而是对 fixture 消息列表**单次触发压缩**，再测压缩后上下文能否支撑 probe 作答。

**流程**（对齐 hermes-compression-eval）：

```
fixtures/<id>.json          # LangChain 消息序列（human/ai/tool，含长 tool 输出）
    → driver.compress()     # 构造 AgentState，调用 middleware.before_model
    → 得到压缩后 messages + 可选 summary 文本
    → 对每道 probe：用 get_llm() 基于压缩后上下文生成答案
    → grader：Judge LLM 按 rubric 0–5 打分
    → report：per-fixture / per-dimension 中位数 + baseline diff
```

**Fixture schema**（`fixtures/<id>.json`）：

```json
{
  "id": "debug_session",
  "description": "多轮排错，含大段 tool 输出与最终根因",
  "messages": [ {"type": "human", "content": "..."}, {"type": "ai", "content": "..."}, ... ],
  "compress_options": {
    "force": true,
    "summarization_messages_to_keep": 4
  }
}
```

- `compress_options.force`：评测时降低有效 trigger（或 fixture 足够长以自然触发），确保走摘要分支。
- Fixture **SHALL** 脱敏，不得含真实 API Key、用户 PII；可从会话导出脚本生成（实现阶段提供 `scrub_fixture.py` 可选）。

**Probe schema**（`probes/<id>.probes.json`）：

```json
{
  "fixture_id": "debug_session",
  "probes": [
    {
      "id": "p1",
      "type": "recall",
      "question": "我们最终定位的根因是什么？",
      "reference_answer": "连接池 max_size 配置为 0"
    },
    {
      "id": "p2",
      "type": "artifact",
      "question": "修改的是哪个配置文件？",
      "reference_answer": "config/database.yaml"
    },
    {
      "id": "p3",
      "type": "decision",
      "question": "为什么放弃回滚而是热修复？",
      "reference_answer": "..."
    },
    {
      "id": "p4",
      "type": "continuation",
      "question": "下一步验证步骤是什么？",
      "reference_answer": "..."
    }
  ]
}
```

**Rubric 维度**（首版 5 维，各 0–5，Judge 输出 JSON）：

| 维度 | 含义 |
|------|------|
| `accuracy` | 与 reference 事实一致 |
| `artifact_trail` | 文件路径、配置项、命令等可追溯信息保留 |
| `context_awareness` | 理解当前任务阶段，无张冠李戴 |
| `continuity` | 能承接未完成任务 |
| `completeness` | 答案覆盖 reference 要点 |

`overall_probe_score = mean(dimensions)`；fixture 得分 = 该 fixture 全部 probe 的 `overall_probe_score` 中位数。

**CLI**：

```bash
cd backend
uv run python -m evals.compression --tag baseline
uv run python -m evals.compression --tag tweak1 --fixture debug_session --runs 3
uv run python -m evals.compression --tag tweak1 --compare-to results/baseline
```

- `--runs N`：同一 fixture 重复 N 次取中位数（摘要/Judge 有随机性）。
- Judge 与 continuation 均 **SHALL** 调用真实 `get_llm()`；摘要 **SHALL** 使用 `get_llm(purpose="summarization")`。
- 环境变量：`NOESIS_COMPRESSION_EVAL_TAG`、`NOESIS_COMPRESSION_EVAL_FIXTURE`、`NOESIS_COMPRESSION_EVAL_RUNS`。

**首版 fixture 主题**（各 1 条，共 3 条）：

| id | 场景 | probe 侧重 |
|----|------|-----------|
| `debug_session` | 多轮排错 + 大 tool 输出 | recall、artifact |
| `feature_impl` | 功能实现多文件修改 | artifact、continuation |
| `config_build` | 配置项讨论与决策 | decision、context_awareness |

### 8. 测试用例线精简（已完成）

- 代码迁至 `evals/case/`；CLI 为 `python -m evals.case`。
- 环境变量推荐 `NOESIS_CASE_EVAL_*`（兼容旧 `NOESIS_EVAL_*`）。
- 删除质量门、mock Judge、runner smoke 测试。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| Agent 评测耗时长、费用高 | `--limit` / `--item-id`；数据集首版 ≤12 条 |
| 工作区种子与 WildClawBench 原版不一致 | `provenance` 字段 + 文档说明简化范围 |
| LLM Judge 不稳定 | 规则项占主权重；保存 Judge 原始 JSON |
| 压缩评测成本高 | 3 fixture × ~10 probe × 2 次 LLM/probe；`--fixture` 单跑 |
| 摘要随机性 | `--runs` 多次取中位数；报告注明 variance |

## Migration Plan

1. 测试用例 eval 精简（已完成）。
2. 实现 `evals/agent/` 骨架 + 2 条种子任务。
3. 实现 `evals/compression/` 骨架 + 1 条 fixture 跑通全流程。
4. 补齐 Agent 数据集 8–12 条、压缩 fixture 3 条 + probe 题库。
5. 更新 `backend/evals/README.md` 三条线说明。

回滚：删除 `evals/agent/` 或 `evals/compression/` 互不影响测试用例 promptfoo 线。

## Open Questions

- 是否在 run 级默认开启 Langfuse trace（建议可选，默认关）。
