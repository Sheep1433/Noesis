# Agent Memory（md 文件记忆层）

> 状态：Current
> 关联 OpenSpec：`openspec/specs/agent-memory/spec.md`（md 文件记忆层现行规格，旧 Memory Cortex 方案已删除）

Noesis 只有一套自动记忆方案：**md 文件记忆层**。会话终态后由后台 sweeper 增量抽取可复用结论，写入用户目录下的 md 文件（索引 + 条目 + journal）；Agent 经 `search_memory` 工具按需主动召回条目正文（索引注入稳定前缀，正文不被动注入）。旧 Memory Cortex 方案（PostgreSQL 权威条目 + Qdrant 派生索引 + worker/outbox + Bulletin）及其 `t_memory*` 表已整体删除，不做数据迁移（未上生产）。

`USER.md` 与 `AGENTS.md` 仍由用户显式维护，不属于自动记忆流水线。

## 用户能力

- 设置页只有单一"记忆"开关（`memory_settings.json` 文件级存储，零新表），默认关闭（fail-closed：评测门禁通过后再默认开启）。
- 开启后：此后完成的会话被抽取、整理，新 Run 自动注入选中条目。
- 关闭后：不抽取、不注入；已写入的 md 文件保留，可继续查看、修订、删除。
- 会话侧边栏展示记忆树（索引 / 条目 / journal）。

## 文件布局

全部位于 `get_user_root(user_id)/memory/`，由 `MemoryStore` 作为唯一读写通道（原子写 tmp+replace，写前重读，不基于陈旧内存覆盖）：

```text
memory/
├── MEMORY.md          # 索引：五类分组、一行一条（[标签] 一句话 → type/slug.md）
├── preference/        # 偏好
├── goal/              # 目标
├── decision/          # 决策
├── experience/        # 经验
├── gotcha/            # 注意事项
└── journal/           # 按日情景日志 YYYY-MM-DD.md，只追加，永不改写
```

- 条目一条一文件：正文、Why、适用条件、来源（可多条）、更新时间；条目文件是事实源，索引损坏行跳过、可从条目目录重建。
- 索引有行数 + 字节双保险预算（`index_max_lines` / `index_max_bytes`）。
- 类型集冻结为五类（`services/memory/types.py`）：新增类型须走新变更提案。
- journal 与条目时间戳使用本地时区；抽取标记（水位列）不改变会话排序。

## 抽取：水位增量（`services/memory/extraction.py`）

```text
sweep（每 sweep_interval_minutes）
  → 找 idle（session_idle_minutes 无新消息）且 最新合格序号 > 水位 的 root 会话
  → 读取水位之后的新消息段（附水位前 2 条衔接背景做指代消解；保头 20% / 保尾 60% 截断，上限 max_message_chars）
  → 附加本轮注入清单（run.memory_context，防自强化：已注入内容不再成为新证据）
  → LLM 五选一判定（合并既有条目 or 新建，每次 ≤ max_entries_per_extraction 条）
  → 轻量合并/新建条目 + journal 追加
  → 成功才推进水位（t_chat_session.memory_extracted_seq = 已成功抽取的最大消息序号）
```

关键语义（对齐 Claude Code cursor）：

- **水位 = 已成功抽取的最大消息序号**；失败保留原水位等下次重试，成功才推进，关闭期间不回溯补抽。
- 同一用户串行执行（asyncio lock，防并发写覆盖文件）。
- subagent 会话（`kind='subagent'`）不抽取：结论经父会话终态通知回流。
- 抽取模型 `extraction_model`（空 = 默认对话模型）。

## 注入：每 Run 选条（`agents/middlewares/memory_entries_middleware.py` + `services/memory/selection.py`）

- 每 Run 由小模型（`selection_model`，可用廉价模型）从索引选条或全量，注入预算 `inject_budget_tokens`。
- 走 late-context 追加通道（不改 system prompt 稳定前缀）；**Run 级冻结**——同一 run_id（含 tool loop 与 HITL resume）注入相同快照。
- alreadySurfaced：本 Run 注入过的条目下一 Run 不重复注入。
- 注入清单回写 `run.memory_context`，作为下次抽取"防自强化"的输入。
- 超过 `stale_warning_days` 的条目注入时附 stale 警告。

## 整理：AutoDream 门控（`services/memory/consolidation.py`）

门控对齐 Claude Code AutoDream，双条件**同时满足**才跑（无活动日不空转）：

1. 距上次整理超过 `consolidation_min_interval_hours`（默认 24h）；
2. 期间新抽取会话数 ≥ `consolidation_min_new_sessions`（默认 5）。

门控检查挂在抽取 sweep 循环尾部。整理内容：全局去重、矛盾裁决、淘汰（goal 完结检查）、索引压缩，并把近 7 天 journal 情景信号（反复主题、用户纠正）纳入整理依据。自动执行无确认；journal 永在，条目可重建。

## 配置（`config.yaml` `memory:` 段）

```yaml
memory:
  extraction_model: ""       # 空 = 默认对话模型（会话终态抽取）
  selection_model: ""        # 空 = 默认对话模型（每 Run 注入选条，可用廉价模型）
  enabled_by_default: false  # fail-closed：评测门禁通过后再默认开启
  session_idle_minutes: 10   # 会话 idle 判定（终态触发抽取）
  sweep_interval_minutes: 30 # 未抽取会话补扫间隔
  max_entries_per_extraction: 3
  index_max_lines: 200       # 索引预算双保险
  index_max_bytes: 25600
  stale_warning_days: 2      # 超龄条目注入时附 stale 警告
  inject_budget_tokens: 2000
  max_entry_chars: 4000
  consolidation_min_interval_hours: 24   # AutoDream 门控：距上次整理最小间隔
  consolidation_min_new_sessions: 5      # 且期间新抽取会话数达此值才整理
  max_message_chars: 120000  # 抽取输入上限（会话消息截断）
```

## 可靠性边界

- 抽取失败不阻塞对话主链路：sweeper 记日志保留水位，下轮重试。
- 崩溃安全：md 写入原子（tmp + os.replace）；水位推进在条目落盘成功之后。
- 无外部依赖：不使用 Qdrant、不建新表；唯一持久化状态是 `memory_extracted_seq` 水位列与 md 文件本身。
