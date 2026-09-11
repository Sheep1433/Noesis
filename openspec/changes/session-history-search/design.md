# Design: session-history-search

## Context

压缩中间件已上线：超过阈值的会话前缀被替换为八节 checkpoint 摘要，`post_tokens` 从数十万降到数千。摘要保宏观弃细节是刻意取舍，但"弃掉的细节"没有出口——原始消息仍在 `t_chat_message`（压缩只改上下文、不动库），摘要消息的 `additional_kwargs` 里已有压缩边界标记（`compact_boundary` digest、`compaction_mode`），数据条件完备。业界三条主流 harness 先例（两条做了完整模型侧检索、一条仅人工恢复）给出一致的实现取向：确定性全文检索、结果即原文、不掺 LLM；其中单会话"被压缩区"专项检索与跨会话发现分属两个窄工具；实证收益为闭卷 recall 40% → 检索兜底 68%。

## Goals / Non-Goals

**Goals:**

- 压缩丢掉的事实可通过一次工具调用从原文找回（单会话、含压缩区专项过滤）。
- 跨会话的"我们之前讨论过什么"可发现、可定位（按会话分组返回）。
- 检索确定性、零 LLM 成本；中文可检索。
- 该能力的收益可被压缩评测量化（recovery 臂）。

**Non-Goals:**

- 语义/向量检索（记忆蒸馏层 `search_memory` 承担语义召回，两层分工）。
- Web UI、血缘追踪、事件级精确读取工具族。
- 记忆层任何变更。

## Decisions

### D1. 数据源：直接查 `t_chat_message`，不建新存储、不走压缩归档文件

压缩前完整历史本就在 Postgres（权威存储、有 message_sequence 与 user_id）；压缩中间件另写的 `/conversation_history/*.md` 归档是快照副本（压扁、无索引、每压缩一份），检索走它等于绕开权威源。归档写入曾按「审计/调试用途」保留，2026-09-08 删除——零运行时消费方，且实测构成 Agent 可 grep 的会话全文旁路（见 `docs/decisions/implemented/2026-09-08-压缩归档删除.md`）。被否方案：给归档文件建索引——冗余存储 + 与库内状态可能漂移。

### D2. 检索技术：pg_trgm 确定性全文匹配

`content` 列建 trigram GIN 索引，`%keyword%` / `similarity()` 查询。理由：① 三条先例全部选择确定性全文检索（FTS5/等价物），无一用向量——工具结果即原文、可解释、零 token 成本；② pg_trgm 对中文天然可用（字符三元组，无需分词器），这正是先例中需要额外做 CJK trigram 适配才能解决的问题，PG 内建消化了；③ 数据已在 PG，不引入第二个索引存储。被否方案：Qdrant 向量检索——语义召回与记忆层职责重叠，且结果非原文需引用映射，复杂度不匹配"找回说过的原话"这个需求。

### D3. 两个窄工具而非一个万能工具

- **`search_history(query, session_id=None, before_compaction=False, limit=5, around_sequence=None, window=5)`**：单会话事件级。`session_id` 省略时为当前会话；指定时校验会话归属（跨会话发现后定点跟进的入口——没有它，`search_sessions` 返回的会话标识就无人能消费）。`before_compaction=True` 时只搜压缩边界之前——"被压缩遮蔽区"在 Noesis 即"message_sequence 早于最近压缩边界的消息"，库内消息从不删除，无需额外遮蔽标记。两种形态：**检索形态**（query 必填，返回 top-k 命中）与**滚动形态**（`around_sequence` 给定时，返回目标消息 ± window 条原文，query 忽略）——滚动形态是截断后的深读出口：检索形态单条截断 2000 字符，模型需要完整原文或前后文时按序号滚动（窗口有上限，仍是"片段语义"而非读全量）。返回按 sequence 升序；**每条命中截断（默认 2000 字符）且总返回有上限**——实测单条 assistant 消息可达 147 万字符（工具轨迹嵌在消息 parts 里），不截断的检索等于变相读回全量，直接摧毁压缩的意义。命中展示从 JSON parts 提取纯文本（text/tool 内容），不渲染原始 JSON。检索语义为**当前快照 top-k**：查询执行期间会话追加新消息不影响本次结果，也不做跨调用的结果集续读（无 cursor）。
- **`search_sessions(query, limit=5)`**：跨会话发现。按当前 `user_id` 过滤、排除当前会话，按会话分组返回（会话标识、`title` 列标题、时间、最强匹配片段、`kind` 与 `parent_id` 血缘信息——识别 subagent 会话）；返回的 session_id 供 `search_history` 定点跟进。刻意排除当前会话——检索目标是"先前工作"，当前会话由 `search_history` 默认行为覆盖。

窄拆分对齐先例共识：合并成一个操作选择器会让模型 schema 模糊、默认行为不可预期。被否方案：单工具承载两种语义（模型对可选参数的决策不稳定）。会话表已有完整血缘字段（`parent_id` / `kind` / `created_by_run_id` / `created_by_tool_call_id`），本 change 只在返回中透出识别信息；完整的祖先链/后代树追踪工具（对齐业界 trace 工具族）数据条件已备，等真实需求立项。

### D4. 压缩边界：写入 `t_chat_session` 新列 `compaction_cutoff_seq`

摘要消息是注入上下文的 HumanMessage，**不落 `t_chat_message`**（该表只有 user/assistant 两类，assistant 按骨架—检查点—终态单行落库）——初稿"摘要消息元数据携带边界"的假设不成立。改为：压缩中间件完成压缩时，把边界写入会话表的专用列（对齐既有 `memory_extracted_seq` 列的先例，Alembic 加列，NULL=从未压缩）。

实施期核实的口径修正：graph checkpoint 消息的 id 由 LangGraph `add_messages` reducer 生成（随机 uuid），与 DB 行的 uuid 零关联，且 ToolMessage 在 DB 无独立行（工具轨迹嵌在 assistant 行 parts 里）——"被压缩前缀的最大 message_sequence"无法逐条精确映射。落地的边界口径为**保守上界**：压缩完成时刻、排除活跃 run 的 streaming 骨架行后，该会话已终态消息的最大 `message_sequence`。性质：被压缩遮蔽的消息必然 ≤ 边界值（压缩只遮蔽已终态的库内消息）；反向不保证精确——边界可能略含仍在保留尾的近期消息。宁多标不漏标：`before_compaction` 的语义是"只搜旧历史"，多含一条近期消息无害，漏标一条被遮蔽消息才是事故。极端 case（当前轮工具链超长被部分压缩）下当前轮 user 行可能漏标，普通 `search_history`（不带过滤）仍可覆盖。`search_history(before_compaction=True)` 读该列过滤 `message_sequence <= cutoff`（滚动形态同样生效）；列为 NULL 时降级为全历史并标注"边界未知"。

### D5. 挂载：SuperAgent 默认挂载，GeneralQA 不挂

SuperAgent 已默认挂 `search_memory`（蒸馏层），补原文层成对；GeneralQA 轻工具面（KB 检索为主）不扩。先例中"默认不挂载"的教训针对的是无人要求的 prompt 教学成本——Noesis 的 SuperAgent 场景（深度研究、长会话）恰是压缩恢复的主战场，需求成立。prompt 段落控制在一段内：教"何时查原文层 vs 蒸馏层"（找说过的话/原始细节 → `search_history`/`search_sessions`；找偏好与结论 → `search_memory`）。

### D6. 行为卫生：SOURCE-FIRST 内建在工具描述

工具 description 首段写明：本工具只检索对话历史，只证明"曾经说过"，不构成外部事实的证据；用户给出 URL/文件/账号等直接来源时先查原来源。防止模型把历史会话当事实来源引用。

### D7. 权限与隔离

跨会话检索强制 `user_id` 归属（构建工具时绑定当前用户，模型不可传 user_id）；单会话检索按会话归属校验。对齐 `memory_tools` 的绑定形态（工具构造期闭包用户身份，运行期不可伪造）。

### D8. 评测 recovery 臂

`evals.compression` 的 `--arms` 增加 `recovery`：压缩后上下文作答时，作答模型额外挂 `search_history` 工具（数据源为 fixture transcript 的进程内索引——评测语境无 DB 会话，对 fixture 原文建内存索引即可）。口径注明：**工具 schema 与产品一致，检索实现为进程内简化版**（产品是 pg_trgm 相似度排序，评测是同语义的简化匹配），因此 recovery 臂量化的是"检索兜底的价值上限"而非产品工具的精确效果。对照产出：闭卷 recall vs 检索兜底 recall 的差值 = 该能力的量化收益。作答侧仍闭卷于"压缩后上下文 + 检索结果片段"，不允许直接读全量 transcript。

## Risks / Trade-offs

- [pg_trgm 对短关键词（<3 字符）召回弱] → 工具描述引导用具体词（路径、错误码、函数名）而非单字；评测题库本就是具体事实题，天然匹配。
- [大会话全量 LIKE 扫描慢] → GIN trigram 索引覆盖；message-level 分页截断返回（top-k 按 similarity 排序）。
- [模型滥用检索（每轮都查）] → SOURCE-FIRST 约束 + prompt 一段教学；若实测滥用率高，后续可加调用预算（对齐 tool_result_budget 思路），不在首版。
- [与 search_memory 的选择困惑] → prompt 明确分工（原文 vs 蒸馏）；评测 recovery 臂同时观察两层的使用率。

## Migration Plan

Alembic 迁移一处（`t_chat_message.content` 建 pg_trgm GIN 索引，幂等）；`compaction_cutoff_sequence` 写入点随中间件发布，存量已压缩会话无该标记时 `before_compaction` 退化为"全历史"（可接受的降级：边界缺失时宁可多搜）。回滚 = 摘除工具挂载，索引可保留（无行为影响）。

## Open Questions

- ~~会话标题来源~~ 已确认：`t_chat_session.title` 列（默认"新对话"），无需兜底设计。
- 跨会话检索是否需要时间范围过滤参数（先例有 `created_at_from/to`）——首版不加，有真实需求再加（YAGNI，避免 schema 膨胀）。
- GIN 索引在消息表高频写入下的维护策略（`fastupdate` / pending list 上限、定期 analyze）——实施时按实测写入速率定参，tasks 中列为实施项。
- 血缘追踪工具（祖先链/后代树/派生 run 定位）：会话表字段已备（`parent_id`/`kind`/`created_by_run_id`/`created_by_tool_call_id`），等真实需求立项。
