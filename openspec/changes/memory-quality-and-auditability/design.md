# 设计：记忆质量与可审计性增强

## Context

md-memory-layer 已落地（`openspec/changes/archive/2026-08-27-md-memory-layer/`），管线为：
抽取（`noesis/services/memory/extraction.py`，水位增量 + LLM 五选一）→ 整理（`consolidation.py`，AutoDream 门控 merge/rewrite/remove/keep）→ 注入（`selection.py` 每 Run 廉价模型选条 + `agents/middlewares/memory_entries_middleware.py`）。

当前条目文件是散文约定格式（`# 标签` + 正文 + `**Why**` + `**适用条件**` + `**来源**` + `**更新时间**`，`store.py:_render_entry`），`read_entry_file` 靠手写状态机解析散文；索引行描述是 `body[:60]` 截断（`upsert_entry`），`rebuild_index` 同源。整理动作改写/淘汰不留底；抽取决策不入 journal；矛盾裁决 prompt 只写「按时间与证据」。

本变更不动管线骨架、不加数据库表、不触碰 SSE，改动集中在：条目文件格式（frontmatter）、抽取/整理 prompt（路由防呆 + 决策报告 + 快照）、裁决优先级。

## Goals / Non-Goals

**Goals:**

- 条目文件元数据结构化：YAML frontmatter 为权威，字段集冻结；索引行成为投影；description 由 LLM 显式生成（「是什么 + 何时调用」），取代 body 截断。
- 抽取路由防呆：时效性内容禁入稳定类型；灰色地带 few-shot 对照示例。
- 维护可观测、可回退：抽取决策（含排除理由）落 journal；整理改写/合并/淘汰前快照改前全文落 journal。
- 矛盾裁决优先级：用户显式修正 > 稳定类型 > 时间与证据。

**Non-Goals:**

- 技能/过程记忆层、命中计数驱动的晋升与淘汰（依赖 `run.memory_context` 聚合，独立立项）
- Qdrant 影子索引、持久追加式 reminder、privacy 字段、历史会话导入（均维持 md-memory-layer §9 阶段划分）
- 索引行格式变更（`- [标签] 描述 → type/slug.md` 保持不变——变更的是描述的来源与语义）
- USER.md / AGENTS.md / journal 块格式变更

## Decisions

### D1：frontmatter 字段集冻结为七字段，沿用 `label` 而非 `title`

```yaml
---
type: preference          # 五类之一；与所在目录不一致 = 治理信号（见 D6）
label: 文档格式偏好        # 沿用现有代码词汇（IndexEntry.label / prompt 术语），不引入 title
description: 偏好表格化简体中文输出；涉及文档/报告/说明输出时调用
tags: [写作]              # 可选；生成时至多 3 个；LLM 抽取默认不生成；用户手写不校验数量（解析容错）
created: 2026-08-29       # 首次沉淀日期
updated: 2026-08-29       # 引擎每次改写时更新
sources:                  # 追加式：会话 id 前 8 位 + 日期
  - 会话 3f2a1b9c · 2026-08-29
---
```

正文保留 `# 标签` + 结论 + `**Why**` + `**适用条件**` 散文节（人读友好），`**来源**` / `**更新时间**` 散文行迁入 frontmatter（`store.py:_render_entry` / `read_entry_file` 重写为 YAML frontmatter 解析 + 散文节解析）。

**为什么字段集要冻结**：类型冻结靠「目录即枚举」，frontmatter 若不冻结会退化为第二个无限发散维度。新增字段走变更提案，与新增类型同等待遇。

**为什么 type 与目录冗余**：文件被单独读到时（search_memory 返回、注入、整理扫描）自描述；且两者不一致本身是治理信号（用户挪了文件 / 抽取写错位置），整理任务可机械检测。

**不进 frontmatter 的**：命中计数（从 `run.memory_context` 聚合的派生数据，写回会造成每次注入都动文件，违反「同 Run 冻结」精神）；status（淘汰语义已定义为删文件 + journal 快照，加 archived 会造出第二条淘汰路径，违反单方案原则）。

### D2：description 权威在 frontmatter，索引行是投影，靠既有单一写路径消灭漂移

所有写入（抽取 `_apply`、整理 `_apply_action`、Agent HITL 主动写入）都收敛在 `MemoryStore.upsert_entry`——这是现成的单一写路径。改造：

- `ExtractedEntry` 增加 `description` 字段（LLM 显式生成，prompt 约束为「一句话结论 + 分号 + 何时调用」），`upsert_entry` 增加 `description` 参数写入 frontmatter；**更新路径未提供 description 时保留既有 frontmatter 值**（防清空权威字段）；
- `IndexEntry` 不变（本就含 label/description），`_sync_index_line` 用 frontmatter 里的 description 同步索引行——**frontmatter 与索引行在同一次 `upsert_entry` 中一起生成**，漂移是代码 bug 而非库状态；
- **投影覆盖语义**：用户只手改索引行 description（不动条目文件）时，下次注入仍生效（读到改后索引），但该条目下次引擎写入时索引行被 frontmatter 覆盖——持久修改入口是条目 frontmatter，此语义写入 spec；
- `rebuild_index` 改为读 frontmatter 的 label/description（不再 `body[:60]` 截断），索引重建成为纯机械投影；
- 无 frontmatter 的存量/手写条目：description 回退现状（正文前 60 字），行为不劣化。

选条（`selection.py`）与注入（`memory_entries_middleware.py`）读的都是索引行/条目文件，格式不变，仅描述质量提升——每 Run 选条 precision 是本变更最大的单点收益。

### D3：抽取路由防呆——时效性禁入稳定类型 + 灰色地带 few-shot

`_EXTRACTION_PROMPT` 增加：

- 硬规则：**带时效性/阶段性的内容（「正在」「本周」「接下来三个月」）只能进 goal，不得写入 preference/decision/experience/gotcha**。错误条目越稳定越难被整理淘汰，这是分类体系里最难自愈的污染路径。
- few-shot 对照示例（直接复用为评测 fixture）：
  - 「定了包管理用 pnpm」→ decision；「就是喜欢 pnpm 的简洁」→ preference
  - 「这个做法绕过了缓存失效的坑」→ gotcha；「先复现再定位的三步法很有效」→ experience
  - 「Q3 要做完迁移」→ goal（时效性）；「迁移已完成，最终选了双写方案」→ decision

### D4：抽取决策落 journal（排除理由必写）

`ExtractionResult` 增加 `excluded: list[ExcludedItem]`（`gist` 一句话内容 + `reason` 排除理由，如「临时任务状态」「与现有条目 X 重复」）。`_apply` 在既有 journal_summary 之外，追加「抽取决策」块：

```
## 14:32 · 会话 3f2a1b9c（抽取决策）
- 新建：preference/document-format（理由）
- 更新：goal/nodejs-learning（追加来源）
- 排除：×N —— 内容 / 理由
```

理由：线上出了坏记忆时管线可调试、可对账；fixture 之外的线上行为可抽查。排除理由由 LLM 生成有噪声，定位是调试线索而非证据链——**journal 仍是唯一证据层**，决策块只是随行记录。

### D5：整理操作前快照改前全文（含 frontmatter）

`consolidation.py:_apply_action` 在执行 merge / rewrite / remove 前，把改前条目文件全文（frontmatter + 正文）追加进当日 journal，块头标记 `（整理快照 · 原条目 type/slug.md）`。数据流：`read_entry 原文 → append_journal 快照 → 执行动作`。

- 效果：「从情景层重建条目」从考古变成机械操作；矛盾裁决结果可审计；淘汰条目的**整理后成果版本**不再丢失（现状只保留原始会话记录）。
- **description 同步维护**：`ConsolidateAction` 增加 `new_description` 字段（空则保留原值；merge 时以保留方条目的 description 为准）——否则 rewrite 换了正文但 description 停留旧值，权威源与正文脱节。
- journal 膨胀有界：快照只发生在低频整理动作时，单条目上限 `max_entry_chars=4000`。
- `_recent_journal`（整理的情景信号输入）排除快照块，避免整理器读到自己的产物形成自指。

### D6：矛盾裁决优先级与治理信号

`_CONSOLIDATION_PROMPT` 裁决规则从「按时间与证据取舍」改为三级优先：

1. **用户显式修正**（抽取侧 `is_correction=true` 的更新，journal 有记录）最高；
2. **稳定类型 > 动态类型**：goal 类新内容不得静默 rewrite preference/decision 条目——若确实冲突，改写动态侧或产出新 goal 条目，稳定侧条目仅在显式修正证据下改写；
3. 同级才按时间与证据。

外加机械治理信号（不依赖 LLM）：整理任务扫描时发现 frontmatter `type` 与所在目录不一致 → **以所在目录为准修正 frontmatter `type`（不移动文件）**，journal 记录。定向依据：引擎写入路径 `entry_path` 自带 `validate_memory_type`，type 与目录必然一致；不一致几乎总源于用户挪动文件，而位置即用户意图。

### D7：存量迁移——一次性惰性迁移，失败退化不阻塞

`MemoryStore` 新增 `migrate_legacy_entries(user_id)`：遍历五类目录条目文件，用现有 `read_entry_file` 散文解析结果补写 frontmatter（`created` 取首个来源日期，无来源取当天）。迁移状态记入既有 `.consolidation_state.json`（`frontmatter_migrated: true`），由抽取 sweeper 启动时按用户惰性执行一次。

- 迁移失败的条目（散文也解析不出）保持原样，走容错路径（D8），下次整理任务作为治理信号处理；
- 回滚策略：本变更不删除散文约定解析能力（`read_entry_file` 保留散文 fallback 分支），回滚部署后旧代码可继续读写——**frontmatter 是增量信息，不是格式破坏**。回滚后 frontmatter 块会被旧解析器当作正文前缀读入（最多 8 行噪声），不致数据损坏。

### D8：frontmatter 容错——对齐既有「索引损坏行跳过」

YAML 解析失败 → 条目退化为无元数据条目：`read_entry_file` 回退散文解析（存量逻辑）；description 回退正文截断；type 取自目录路径。注入、检索、索引重建 SHALL NOT 因此失败。用户手改 frontmatter 写坏（缩进/冒号）是预期场景。

### Agent 侧提示词同步

`agents/prompts/memory.py` 中 Agent 主动写入的条目格式说明（现为「`# 标签` 开头 + 结论正文 + `**来源**` 小节」）改为 frontmatter 格式模板；Agent 写入仍经 HITL 确认，`memory_tools` 写入统一走 `upsert_entry`（现状即如此，无新路径）。

## Risks / Trade-offs

- [LLM 生成的 description 质量不稳定，退化成正文复读] → prompt 约束两段式结构（结论；何时调用）+ 评测 fixture 断言 description 含触发场景语义；选条质量有既有全量兜底（小记忆量跳过小模型）。
- [frontmatter 增加每条注入成本（约 7 行）] → 有界且换来主模型对选中条目的二次相关性自校验；注入预算判定（`_all_bodies`）按文件全文字符计算，天然涵盖。
- [journal 快照与决策块增加体积，影响 `search_memory` grep 命中噪声] → 快照块/决策块有固定块头标记，检索结果摘要可识别；`_recent_journal` 排除快照块（D5）。
- [tags 发散] → 默认不生成（D1：LLM 抽取不产 tags），仅用户手写或后续变更启用；字段存在但管线不主动填充。
- [用户手改 frontmatter 破坏 YAML] → D8 容错路径；治理信号在整理任务归位。
- [迁移中途崩溃留下半迁移状态] → 迁移按条目文件粒度幂等（已有 frontmatter 的跳过），重跑安全。

## Migration Plan

1. 发布含 D1–D8 的后端；启动后首次 sweeper 运行时按用户惰性迁移（D7）。
2. 迁移不设开关、不回填数据库——纯文件层变更，`.consolidation_state.json` 记录状态。
3. 回滚：直接回滚部署即可（D7 回滚分析）；已写入 frontmatter 的条目在旧代码下多一段前置噪声，无数据损坏。

## Open Questions

- 决策块与快照块是否需要在 `search_memory` 结果中默认过滤（按块头标记），还是原样返回交给模型判断？倾向后者（信息保留），实现时看检索噪声实测。
