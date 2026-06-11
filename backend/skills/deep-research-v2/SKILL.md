---
name: deep-research-v2
description: Multi-phase deep research with planning, multi-source retrieval, quality screening, cross-validation, and cited Markdown reports. Use for market research, competitive analysis, academic surveys, and structured investigation tasks in Chinese or English.
version: 2.0.0

---

# Deep Research v2

多阶段深度研究 skill：规划 → 多源检索 → 质量筛选 → 分析 → 交叉验证 → 综合报告 → 可选迭代。

## Noesis 运行环境

本 skill 在 DeepResearchAgent 中运行，路径均为 **backend 虚拟路径**（非宿主机绝对路径）：

| 用途 | 虚拟路径 | 说明 |
|------|----------|------|
| 工作区（读写） | `/research/<主题-slug>/` | 所有研究产出写在此树下 |
| 本 skill | `/skills/deep-research-v2/` | 只读 |
| 网页抓取 | `/skills/baoyu-url-to-markdown/SKILL.md` | 抓取 URL 为 Markdown 时先读此 skill |
| 文本摘要 | `/skills/summarize/SKILL.md` | 音视频/长文摘要备选 |

**禁止**向 `/skills/` 写入；**禁止**使用 `/Users/...` 等 host 路径。

`<主题-slug>` 规则：小写、连字符分隔、简短英文或拼音，如 `ai-agent-roadmap`。

## 触发条件

用户要求进行深度研究、市场调研、学术论文检索、竞品分析时激活。

## 支持的命令格式

- `/research <主题> [--depth shallow|medium|deep]`
- `深度调研 <主题>`
- `研究 <主题> 的市场/技术/竞品`

## 研究阶段（强制顺序执行）

### Phase 1: 研究规划 (Research Planning)

**目标**：明确研究问题、定义边界、设计检索策略  
**输出**：`/research/<主题-slug>/research-plan.md`

必须包含：核心研究问题（3–5 个）、检索关键词矩阵、数据源清单、质量评估标准、预期产出结构。  
详细协议见 `/skills/deep-research-v2/RESEARCH_PROTOCOL.md`。

### Phase 2: 多源检索 (Multi-Source Retrieval)

**目标**：从多源获取信息，避免单一来源偏见  
**输出**：`/research/<主题-slug>/sources/raw-sources.json`

必须包含：URL、发布时间、作者/机构、摘要。建议来源类型：

- **行业/竞品/政策（首选）**：`web_search` 发现 URL → `web_fetch` 或 `/skills/baoyu-url-to-markdown` 抓取正文
- **学术**：OpenAlex 公开 API（`execute` + `curl`）、PubMed 检索页
- **行业**：公司官网、行业报告（复杂页经 baoyu-url-to-markdown 抓取）
- **政策/专利**：官方站点（如适用）

OpenAlex 示例（在 `execute` 中运行）：

```bash
curl -s "https://api.openalex.org/works?search=YOUR_KEYWORDS&per_page=10" | head -c 50000
```

网页正文抓取：先 `read_file` `/skills/baoyu-url-to-markdown/SKILL.md`，按其脚本说明执行。

### Phase 3: 质量筛选 (Quality Screening)

**目标**：过滤低质量信息  
**输出**：

- `/research/<主题-slug>/sources/filtered-sources.json`
- `/research/<主题-slug>/sources/excluded-sources.json`

评估维度见下方「质量评估标准」及 `QUALITY_CRITERIA.md`。

### Phase 4: 深度分析 (Deep Analysis)

**目标**：提取关键洞察  
**输出**：`/research/<主题-slug>/analysis/insights.md`

必须包含：证据等级（A/B/C/D）、矛盾发现及原因、局限性、研究空白。

### Phase 5: 交叉验证 (Cross-Validation)

**目标**：多源相互印证  
**输出**：`/research/<主题-slug>/analysis/validation-matrix.md`

### Phase 6: 综合报告 (Synthesis Report)

**目标**：生成可追溯的最终报告  
**输出**：

- `/research/<主题-slug>/report.md`（**面向用户的主报告，必填**）
- `/research/<主题-slug>/reports/final-report.md`（可与 report.md 内容相同或为其扩展版）

必须包含：执行摘要、方法论说明、核心发现（带证据等级）、批判性分析、可操作建议、参考文献（可点击链接）。  
模板：`/skills/deep-research-v2/templates/report-template.md`

### Phase 7: 用户反馈迭代 (Feedback Loop)

**触发**：用户对结论质疑或要求深化  
**输出**：`/research/<主题-slug>/reports/revised-report.md`

## 执行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| depth | deep | shallow=概览，medium=标准，deep=完整 7 阶段 |
| sources | all | academic / industry / all |
| min_sources | 20 | 最低来源数量 |
| quality_threshold | 0.7 | 质量评分阈值（0–1） |

## 质量门禁

报告生成前须满足：

- [ ] 核心结论至少有 2 个独立来源支持
- [ ] 数据有明确来源和日期
- [ ] 矛盾发现已标注并分析
- [ ] 局限性已说明
- [ ] 参考文献含可访问链接

## 工作区目录结构

```text
/research/<主题-slug>/
├── research-plan.md
├── sources/
│   ├── raw-sources.json
│   ├── filtered-sources.json
│   └── excluded-sources.json
├── analysis/
│   ├── insights.md
│   └── validation-matrix.md
├── reports/
│   ├── final-report.md
│   └── revised-report.md      # 如有迭代
└── report.md                  # 主交付物（Phase 6 必填）
```

## 工具与 Skill 依赖

| 能力 | 用法 |
|------|------|
| 关键词搜索 | `web_search`（Tavily 优先，无 Key 回退 DuckDuckGo） |
| 网页正文 | `web_fetch`（Tavily extract 优先，无 Key 回退本地 HTTP）；复杂/反爬页用 `/skills/baoyu-url-to-markdown` |
| 文件读写 | `read_file` / `write_file` / `edit_file`，路径以 `/research/` 或 `/skills/` 开头 |
| 公开 API | `execute` + `curl`（OpenAlex 等；arXiv 须用 `https://` 或 `curl -sSL`） |
| GitHub 检索 | `execute` + `gh`（如 `gh search repos <query> --limit 10 --json name,url`） |
| JSON 处理 | `execute` + Python 或写入后编辑 |
| 质量评分 | `execute`：`python3 /skills/deep-research-v2/scripts/quality-score.py`（可选） |

## 质量评估标准

### 学术论文评分卡（0–10 分）

| 维度 | 评分项 | 分值 |
|------|--------|------|
| 时效性 | 近 3 年 | 2.5 |
| 时效性 | 3–5 年 | 2 |
| 时效性 | 5 年+ | 1 |
| 权威性 | Nature/Science/Lancet | 3 |
| 权威性 | 其他顶刊 | 2.5 |
| 权威性 | PubMed 索引 | 2 |
| 方法论 | 样本量>1000 | 1 |
| 方法论 | 有对照组 | 1 |
| 方法论 | 多中心 | 1 |
| 可复现 | 数据/代码公开 | 1 |
| 透明度 | 利益冲突声明 | 0.5 |

**证据等级**：A(9–10) / B(7–8) / C(5–6) / D(<5)

## 相关文件

- 研究协议：`/skills/deep-research-v2/RESEARCH_PROTOCOL.md`
- 质量标准：`/skills/deep-research-v2/QUALITY_CRITERIA.md`
- 报告模板：`/skills/deep-research-v2/templates/report-template.md`
- 来源卡片：`/skills/deep-research-v2/templates/source-card.md`
