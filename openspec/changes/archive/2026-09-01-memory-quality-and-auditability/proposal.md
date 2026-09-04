# 提案：记忆质量与可审计性增强

## Why

md-memory-layer 落地后，记忆管线（抽取 → 整理 → 注入）已自动运转，但存在三类结构性缺口：**元数据埋在正文散文里**（来源/更新时间是行文约定而非结构化字段，引擎改写靠 regex、索引重建非机械）；**维护过程不可观测**（抽取决策黑盒、整理改写/淘汰不留底，"journal 可重建"实为理论能力）；**分类与裁决缺乏防呆**（时效性内容可误入稳定类型且最难自愈，矛盾裁决只看时间与证据）。对照外部实践（Claude Code 记忆 frontmatter、个人记忆系统五层设计）后，这些点均有成熟解法且改动集中在 prompt 与条目文件结构层。同时，现行召回为「廉价模型 Run 起点按最新一条消息盲选注入」，检索时机与上下文均弱、机制冗余（选条服务 + 注入中间件 + alreadySurfaced 记账），一次性切换为 Agent 主动检索。

## What Changes

- **召回模式切换为 Agent 主动检索（Agentic recall）**：删除每 Run 被动注入——`selection.py` 选条服务与 `memory_entries_middleware.py` 注入中间件整体移除（含挂载点与 alreadySurfaced 记账）；稳定前缀保持 `USER.md` + `MEMORY.md` 索引不变；条目正文由 Agent 经 `search_memory` 工具 / 文件读取按需召回；`run.memory_context` 写入方由注入中间件改为检索工具（防自强化语义不变）；检索结果附条目年龄（stale）提示；记忆使用 prompt 增加召回纪律。
- **条目文件引入 YAML frontmatter 结构化元数据**：字段集冻结为 `type` / `label` / `description` / `tags`（可选）/ `created` / `updated` / `sources`；正文保留结论 + Why + 适用条件，`来源:` 与 `更新时间` 行文约定迁入 frontmatter。
- **description 语义定为「是什么 + 何时调用」**，`MEMORY.md` 索引行改为 frontmatter 的投影（同一写路径生成，消灭漂移）；索引成为 Agent 召回的唯一路由面，描述质量即召回质量。
- **抽取路由防呆**：时效性内容禁入稳定类型（只能进 goal）；抽取 prompt 增加灰色地带 few-shot 对照示例（decision vs preference、experience vs gotcha、goal vs decision）。
- **抽取决策写入 journal**：每次抽取把决策摘要（新条目/合并更新/排除内容及理由）随情景条目追加进当日 journal，管线可调试、可对账。
- **整理操作前快照**：整理任务在改写、合并、淘汰前把改前正文（含 frontmatter）追加进 journal，"从情景层重建条目"变为机械操作，可回退、可审计。
- **矛盾裁决引入类型稳定度权重**：裁决优先级改为「用户显式修正 > 稳定类型条目 > 时间与证据」，动态层陈述不得静默改写稳定层条目。
- **frontmatter 容错**：YAML 解析失败退化为无元数据条目，召回与检索不失败（对齐既有「索引损坏行跳过」场景）。

明确非目标（各自独立立项，本变更不涉及）：技能/过程记忆层、命中计数驱动的晋升与淘汰、Qdrant 影子索引、privacy 字段。（md-memory-layer §9 后续阶段的「持久追加式 reminder」优化项随注入通道删除而失效，不再立项。）

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-memory-cortex`: 条目文件 SHALL 结构（散文约定 → frontmatter 权威 + 索引投影）、抽取路由守卫（时效性禁入稳定类型）、抽取决策与整理快照写入 journal、矛盾裁决优先级、frontmatter 容错场景；召回 requirement 整体更换（被动每 Run 选条注入 → 稳定前缀索引 + Agent 主动检索）。

## Impact

- **后端**：`noesis/services/` 下记忆抽取与整理服务（条目文件读写、索引行生成）、抽取/整理 prompt 模板、journal 写入路径；**删除** `services/memory/selection.py`、`agents/middlewares/memory_entries_middleware.py` 及挂载点（`super_agent.py`/`factory.py`/`agents/middlewares/stack.py`）；`agents/tools/memory_tools.py` 扩展（召回清单回写 + stale 提示）；清理仅注入使用的 MemoryConfig 项。不新增数据库表、不新增 API、不触碰 SSE。
- **文件结构**：`memory/<type>/<slug>.md` 条目文件格式变更（加 frontmatter）。**存量条目不迁移**：散文解析 fallback 长期保留，存量条目照常可读可检索；条目被引擎下次写入时自然升级为 frontmatter 格式。
- **兼容性**：`MEMORY.md` 索引行格式不变（`- [标签] 描述 → 路径`），变的是描述的来源（frontmatter 投影）与语义（「是什么 + 何时调用」）；`USER.md` 与 journal 结构不变；无 API/SSE 破坏性变更。
- **评测**：抽取/整理 fixture 需扩展（frontmatter 生成、路由防呆、决策与快照落 journal、裁决优先级）；新增召回行为断言（应召回场景 Agent 调用检索）与 memory on/off paired 口径沿用。
