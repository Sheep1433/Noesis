# 提案：记忆质量与可审计性增强

## Why

md-memory-layer 落地后，记忆管线（抽取 → 整理 → 注入）已自动运转，但存在三类结构性缺口：**元数据埋在正文散文里**（来源/更新时间是行文约定而非结构化字段，引擎改写靠 regex、索引重建非机械）；**维护过程不可观测**（抽取决策黑盒、整理改写/淘汰不留底，"journal 可重建"实为理论能力）；**分类与裁决缺乏防呆**（时效性内容可误入稳定类型且最难自愈，矛盾裁决只看时间与证据）。对照外部实践（Claude Code 记忆 frontmatter、个人记忆系统五层设计）后，这些点均有成熟解法且改动集中在 prompt 与条目文件结构层。

## What Changes

- **条目文件引入 YAML frontmatter 结构化元数据**：字段集冻结为 `type` / `title` / `description` / `tags`（可选）/ `created` / `updated` / `sources`；正文保留结论 + Why + 适用条件，`来源:` 与 `更新时间` 行文约定迁入 frontmatter。
- **description 语义定为「是什么 + 何时调用」**，`MEMORY.md` 索引行改为 frontmatter 的投影（同一写路径生成，消灭漂移）；每 Run 选条的 precision 随之提升。
- **抽取路由防呆**：时效性内容禁入稳定类型（只能进 goal）；抽取 prompt 增加灰色地带 few-shot 对照示例（decision vs preference、experience vs gotcha、goal vs decision）。
- **抽取决策写入 journal**：每次抽取把决策摘要（新条目/合并更新/排除内容及理由）随情景条目追加进当日 journal，管线可调试、可对账。
- **整理操作前快照**：整理任务在改写、合并、淘汰前把改前正文（含 frontmatter）追加进 journal，"从情景层重建条目"变为机械操作，可回退、可审计。
- **矛盾裁决引入类型稳定度权重**：裁决优先级改为「用户显式修正 > 稳定类型条目 > 时间与证据」，动态层陈述不得静默改写稳定层条目。
- **frontmatter 容错**：YAML 解析失败退化为无元数据条目，注入与检索不失败（对齐既有「索引损坏行跳过」场景）。

明确非目标（各自独立立项，本变更不涉及）：技能/过程记忆层、命中计数驱动的晋升与淘汰、Qdrant 影子索引、privacy 字段、持久追加式 reminder。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-memory-cortex`: 条目文件 SHALL 结构（散文约定 → frontmatter 权威 + 索引投影）、抽取路由守卫（时效性禁入稳定类型）、抽取决策与整理快照写入 journal、矛盾裁决优先级、frontmatter 容错场景。

## Impact

- **后端**：`noesis/services/` 下记忆抽取与整理服务（条目文件读写、索引行生成）、抽取/整理 prompt 模板、journal 写入路径；不新增数据库表、不新增 API、不触碰 SSE。
- **文件结构**：`memory/<type>/<slug>.md` 条目文件格式变更（加 frontmatter）。**存量条目迁移**：首次整理任务统一补写 frontmatter（从现有正文约定解析）；迁移失败条目按容错规则退化处理，不阻塞。
- **兼容性**：`MEMORY.md` 索引行格式不变（`- [标签] 描述 → 路径`），变的是描述的来源（frontmatter 投影）与语义（「是什么 + 何时调用」）；`USER.md` 与 journal 结构不变；无 API/SSE 破坏性变更。
- **评测**：抽取/整理 fixture 需扩展（frontmatter 生成、路由防呆、决策与快照落 journal、裁决优先级）。
