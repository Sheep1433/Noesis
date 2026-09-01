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
- 召回模式切换：删除被动注入，Agent 主动检索为唯一主动通道；稳定前缀（USER.md + 索引）不变。

**Non-Goals:**

- 技能/过程记忆层、命中计数驱动的晋升与淘汰（依赖 `run.memory_context` 聚合，独立立项）
- Qdrant 影子索引、privacy 字段、历史会话导入（均维持 md-memory-layer §9 阶段划分；「持久追加式 reminder」项随注入通道删除失效）
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

### D2：description 权威在 frontmatter，索引行是投影，靠「单一解析点 + 单一索引同步点」消灭漂移

写入面实际有三条（核对于 2026-08-31 代码）：引擎写入（抽取 `_apply`、整理 `_apply_action`）走 `MemoryStore.upsert_entry`；Agent HITL 主动写入走 `GuardedFilesystemBackend.write/edit` 直写文件（`agents/backends/memory.py:178/197`，HITL 审批判定见 `is_memory_writable_path`）；用户 UI 编辑走 `PUT /memory/entry` 直写文件（`user_settings_api.py`）。三条路径**不共享渲染**，但全部收敛于两个点：`read_entry_file`（唯一解析点）与 `_sync_index_line`（唯一索引同步点）——漂移防护由这两个收敛点承担，frontmatter 落地后三条写入面无需逐路径改造即自动获得结构化投影：

- `ExtractedEntry` 增加 `description` 字段（LLM 显式生成，prompt 约束为「一句话结论 + 分号 + 何时调用」），`upsert_entry` 增加 `description` 参数写入 frontmatter；**更新路径未提供 description 时保留既有 frontmatter 值**（防清空权威字段）；Agent/用户直写路径的 description 由直写内容中的 frontmatter（或散文 fallback 截断）经解析点自然取得；
- `IndexEntry` 不变（本就含 label/description），三个写入面的索引同步均经 `_sync_index_line` 用解析出的 description——**文件是权威，索引行是投影**，漂移是代码 bug 而非库状态；
- **投影覆盖语义**：用户只手改索引行 description（不动条目文件）时，下次注入仍生效（读到改后索引），但该条目下次引擎写入时索引行被 frontmatter 覆盖——持久修改入口是条目 frontmatter，此语义写入 spec；
- `rebuild_index` 改为读 frontmatter 的 label/description（不再 `body[:60]` 截断），索引重建成为纯机械投影；
- 无 frontmatter 的存量/手写条目：description 回退现状（正文前 60 字），行为不劣化。

召回（`selection.py` 每 Run 廉价模型选条 + `memory_entries_middleware.py` 注入）被 D9 整体删除后，`MEMORY.md` 索引成为 Agent 召回的**唯一路由面**——description 的「何时调用」语义从「提升选条 precision」升格为「召回质量的成败所系」，这也是 D1/D2 与 D9 必须同一变更落地的原因。

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

### D7：存量条目不迁移——永久走散文容错，写入时自然升级

存量无 frontmatter 的条目**不做迁移任务**：`read_entry_file` 的散文解析 fallback 长期保留（见 D8），存量条目照常可读、可检索、可注入索引（description 回退正文截断）。条目在被引擎**下次写入时**（抽取更新、整理改写或 Agent HITL 写入）经 `upsert_entry` 渲染 frontmatter，自然完成格式升级——惰性升级发生在正常写路径上，不引入专门迁移代码与迁移状态。

- 回滚策略：本变更不删除散文约定解析能力（`read_entry_file` 保留散文 fallback 分支），回滚部署后旧代码可继续读写——**frontmatter 是增量信息，不是格式破坏**。回滚后 frontmatter 块会被旧解析器当作正文前缀读入（最多 8 行噪声），不致数据损坏。

### D8：frontmatter 容错——对齐既有「索引损坏行跳过」

YAML 解析失败 → 条目退化为无元数据条目：`read_entry_file` 回退散文解析（存量逻辑）；description 回退正文截断；type 取自目录路径。注入、检索、索引重建 SHALL NOT 因此失败。用户手改 frontmatter 写坏（缩进/冒号）是预期场景。

### D9：召回模式切换——删除被动注入，Agentic 检索为唯一主动通道

**保留**：稳定前缀不变（`RefreshingMemoryMiddleware`：USER.md 全文 + MEMORY.md 索引，会话内不变）——索引即菜单，Agent 每轮可见，按 description 的「何时调用」判断读全文还是直接用索引行信息（Claude Code skills 同款模式）。

**删除**：`services/memory/selection.py`（整个文件）、`agents/middlewares/memory_entries_middleware.py`（整个文件）、挂载点（`super_agent.py:282` 的 `build_memory_entries_middleware`、`factory.py` 透传、`stack.py:146` 装配、`middlewares/__init__.py` 导出）、alreadySurfaced 记账与 Run 级冻结语义、MemoryConfig 中仅注入使用的项（`selection_model`、注入预算全量判定）。`insert_late_context` 工具函数**保留**——`dynamic_context_middleware`（current_time 通道）仍在使用，删除的只是记忆对它的引用；此后记忆召回就是**普通的工具调用与结果消息**（ToolMessage 进对话历史、随会话持久化，后续 Run 天然可见——alreadySurfaced 的等价物免费获得），不存在任何特殊插入机制。

**召回清单回写**：`search_memory` 工具装配参数增加 `run_id` 与 db 句柄（root run 装配；subagent 只读不写，结论经父会话回流，与抽取语义一致），命中后**合并写入** `run.memory_context['entries']`（读-合并-写，去重追加）。防自强化守卫语义不变，输入从「注入清单」变为「工具召回清单」。Agent 经通用 `read_file`/`grep` 直接读条目不入清单——防自强化为尽力而为，主通道是 search_memory。

**stale 警告搬家**：从注入时附加改为 `search_memory` 返回结果附条目年龄提示（沿用 `stale_warning_days` 阈值与文案）。

**召回纪律（prompt）**：`agents/prompts/memory.py` 记忆使用指引增加——涉及用户偏好、历史决策、既往经验、当前目标时，先检索再产出；索引每轮可见，按条目 description 判断是否需要读全文。静默漏召回是新模式的已知失效模式，由三层覆盖：USER.md 基线层（每轮适用的核心偏好不依赖检索）、索引即菜单、召回纪律 + 评测断言。

**回滚**：纯代码删除与装配改动，数据格式、文件结构、API 均不变——直接还原代码即回滚。md-memory-layer §9 的「持久追加式 reminder」后续优化项随注入通道删除而失效，从后续阶段清单移除。

**cache 影响澄清**：现状注入走 late-context 通道、不碰稳定前缀，且该槽位本因 `current_time` 每 Run 失效——切换的收益不在 cache，在检索精度（全上下文 + 任务中途按需，SuperAgent 长工具循环场景尤其受益）、上下文经济与机制简化。

### Agent 侧提示词同步

`agents/prompts/memory.py` 中 Agent 主动写入的条目格式说明（现为「`# 标签` 开头 + 结论正文 + `**来源**` 小节」）改为 frontmatter 格式模板。现状写入路径：Agent 经 `GuardedFilesystemBackend` 直写条目文件（白名单 + HITL `memory_write_when` 审批，`is_memory_writable_path` 判定），写入后 `_sync_index_line` 自动重解析投影索引——该机制不变，frontmatter 化后投影自动从结构化字段取得（Agent 写坏 frontmatter 走 D8 容错）。

## Risks / Trade-offs

- [Agentic 召回的静默漏召回——Agent 未想到检索时记忆不生效，用户感知为「它不记得我」] → 三层覆盖：USER.md 基线层承载每轮适用的核心偏好（不依赖检索）；索引每轮可见且 description 含「何时调用」；召回纪律 prompt + offline-evals 召回行为断言（应召回场景必须调用检索）。漏召回概率由评测量化，不靠假设。
- [run.memory_context 由工具调用时合并写入，Run 中途崩溃丢部分记录] → 逐次持久化（write-on-call），崩溃前已召回的条目已入库；防自强化为尽力而为语义，部分清单可接受。
- [LLM 生成的 description 质量不稳定，退化成正文复读] → prompt 约束两段式结构（结论；何时调用）+ 评测 fixture 断言 description 含触发场景语义；索引行 gist 多数场景可直接使用，读全文是兜底而非必经。
- [frontmatter 增加每条注入成本（约 7 行）] → 有界且换来主模型对选中条目的二次相关性自校验；注入预算判定（`_all_bodies`）按文件全文字符计算，天然涵盖。
- [journal 快照与决策块增加体积，影响 `search_memory` grep 命中噪声] → 快照块/决策块有固定块头标记，检索结果摘要可识别；`_recent_journal` 排除快照块（D5）。
- [tags 发散] → 默认不生成（D1：LLM 抽取不产 tags），仅用户手写或后续变更启用；字段存在但管线不主动填充。
- [用户手改 frontmatter 破坏 YAML] → D8 容错路径；治理信号在整理任务归位。

## Migration Plan

1. 发布含 D1–D9 的后端；无迁移步骤——存量条目走散文容错（D7），引擎下次写入该条目时自然升级为 frontmatter 格式。
2. 回滚：直接回滚部署即可（D7 回滚分析）；已写入 frontmatter 的条目在旧代码下多一段前置噪声，无数据损坏。

## Open Questions

- 决策块与快照块是否需要在 `search_memory` 结果中默认过滤（按块头标记），还是原样返回交给模型判断？倾向后者（信息保留），实现时看检索噪声实测。
