# 决策：记忆分层/类型/存储权威 + Agentic 召回（md 权威 vs DB、被动注入 vs Agentic）

状态：implemented
日期：2026-08-29
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **问题**：① 条目文件引入 frontmatter 元数据后，为什么不干脆用数据库权威？② 记忆召回应该在 Run 起点被动注入，还是让 Agent 主动检索？③ 记忆分几层几类、存哪、元数据用哪个载体？
- **记忆分层 4 层**（稳定→动态，对应写入方/生命周期）：USER.md（纯手写，引擎永不改）/ AGENTS.md（Agent 可写惯例层）/ 语义记忆层（`memory/<type>/*.md` + MEMORY.md 索引，引擎抽取整理，可合并改写淘汰）/ 情景记忆层（`journal/YYYY-MM-DD.md`，引擎自动追加，永不改写、证据兜底）。认知科学对应：语义 vs 情景；USER/AGENTS 手写基线层承担「每轮都适用、不能依赖检索」内容——是 Agentic 召回切换后防静默漏召回的第一道覆盖。
- **类型冻结五类**：`types.py` 目录即枚举，回答固定问题类型。
- **存储权威结论（md 文件不是模拟数据库）**：frontmatter 的动机是让文件自描述（引擎精确改 `updated`/`sources`、索引机械重建），不是模拟 DB。当前数据形态仅 7 个字段、无外键/join/跨条目查询，DB 关系模型优势落空；DB 权威 + 前台渲染 md 会撞上「md 还能不能编辑」的双向同步死结（能编辑=双向同步，不能=违反用户编辑最高权限）。查询需求的正确打开方式是**真相在文件、索引做派生**（Qdrant 影子索引，写后同步 + 定期对账）——老 Memory Cortex（PG 权威 + Qdrant 派生）正是被这套设计取代删掉的。
- **召回模式结论（Agentic 主动召回是更好终态）**：现状是「廉价模型在 Run 起点、只看最新一条用户消息做一次性盲选」；改为「稳定前缀 = USER.md + MEMORY.md 索引（即菜单），Agent 任务中带完整上下文按需 `search_memory`」。理由：① 检索时机更对——记忆需求常在任务中途浮现（SuperAgent 深度研究跑到一半才撞到相关 gotcha），Run 起点盲选在信息量上严格占优的只有"不用工具"；② 发现能力不输——索引在稳定前缀里每轮可见，frontmatter 落地后每条记忆挂「是什么 + 何时调用」描述，Agent 看一行就能决定读不读。删除 `selection.py` + `memory_entries_middleware.py` 及挂载点，`run.memory_context` 写入方改 `search_memory`。
- **落地澄清（8/31）**：`insert_late_context` 工具函数**保留**——它另有使用方 `dynamic_context_middleware`（current_time 通道每 Run 刷新），删除的只是记忆对它的引用；Agentic 召回后记忆正文就是一条普通工具调用 + ToolMessage，进对话历史随会话持久化，无需特殊插入机制。
- **写路径修正（8/31）**：Agent HITL 主动写入走 `GuardedFilesystemBackend.write/edit` 直写文件（`backends/memory.py:178/197`），用户 UI 编辑走 `PUT /memory/entry` 直写——**不**都走 `upsert_entry`。三条写入面（引擎 upsert / Agent 直写 / 用户 UI 直写）不共享渲染，但全收敛于两个点：`read_entry_file`（唯一解析点）+ `_sync_index_line`（唯一索引同步点）；frontmatter 落地后三条路径自动获得结构化投影，漂移防护靠这两个收敛点承担。
- **落进 spec**：`openspec/changes/memory-quality-and-auditability`（frontmatter 七字段冻结 / description 索引投影 / 时效性禁入稳定类型 / 抽取决策落 journal / 整理前快照 / D9 Agentic 召回切换）。4/4 artifacts 保持齐备，与近期代码变更（d36b97f3 is_memory_writable_path）对齐。
- **可迁移**：
  - 存储介质判断先问「有没有真正的关系查询需求」，没有就 md 自描述 + 影子索引，别把权威搬进 DB 再渲染同步；
  - 召回机制判断看「记忆需求出现在任务哪个时刻」，任务中途才需要的就交给 Agentic，不要为省一次工具调用牺牲检索时机；
  - 用「一个函数=唯一写路径」表述前先核实调用方——不成立的收敛假设会让机制描述失真，改成「唯一解析点 + 唯一索引同步点」更稳。
