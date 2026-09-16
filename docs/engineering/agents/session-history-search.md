# Session History Search（会话历史检索：Agent 侧原文层召回）

压缩摘要保宏观弃细节是刻意取舍，但「弃掉的细节」此前没有出口——原始消息仍在 `t_chat_message`（压缩只改上下文、不动库），缺的只是查询入口。本专题给出这个入口：两个窄工具 + 一个压缩边界列 + 一个 trigram 索引，确定性全文检索、结果即原文、零 LLM 成本。

## 组成

| 件 | 位置 | 职责 |
|---|---|---|
| `search_history` / `search_sessions` 工具 | `noesis/agents/tools/history_search_tool.py` | 单会话事件级检索 / 跨会话发现；闭包绑定 user_id 与当前 session_id，模型不可传用户标识 |
| 检索服务 | `noesis/services/history_search.py` | pg_trgm SQL + parts 纯文本渲染 + 截断预算 + 边界过滤 + 边界写入 |
| `compaction_cutoff_seq` 列 | `t_chat_session`（迁移 202609070001） | 压缩遮蔽边界：最近一次压缩完成时已终态消息的最大序号，NULL=从未压缩 |
| trigram GIN 索引 | `t_chat_message (content::text) gin_trgm_ops`，`fastupdate=off` | ILIKE 预筛走索引，中文三元组开箱即用 |
| 评测 recovery 组 | `evals/compression/agent_path.py`（`ARM_FLAGS`） | 闭卷压缩 vs 检索兜底对照，量化该能力收益 |

挂载面：SuperAgent 默认挂载（与 `search_memory` 蒸馏层成对，prompt `<history_recall>` 段教分工）；GeneralQA 轻工具面不挂；task-worker 不挂（隔离 loop 触不到主 loop 绑定的 pg_manager 连接池，且子任务场景不需要跨 run 召回）。

## 数据流

**边界写入**（压缩完成时）：`CompactionMiddleware` 接受可选 `boundary_writer` 回调（async 路径 `awrap_model_call` / `acompact_state` 调用；同步 `wrap_model_call` 是离线评测路径不接 DB），factory 按 session_id 构造回调 → `record_compaction_boundary`：排除活跃 run 的 streaming 骨架行后取该会话最大 `message_sequence`，单调写入会话行。失败只记日志——边界缺失时 `before_compaction` 降级为全历史检索（标注「边界未知」），不阻断压缩主流程。

**边界口径是保守上界**：被压缩遮蔽的消息必然 ≤ cutoff，反向不保证精确——cutoff 可能略含仍在保留尾的近期消息。精确逐条映射不可行的原因：graph checkpoint 消息的 id 由 LangGraph `add_messages` reducer 生成（随机 uuid），与 DB 行的 uuid 零关联；ToolMessage 在 DB 无独立行（工具轨迹嵌在 assistant 行 parts 里）。宁多标不漏标：`before_compaction` 的语义是「只搜旧历史」，多含一条近期消息无害，漏标一条被遮蔽消息才是事故。

**检索路径**（`search_session_history`）：

1. 归属校验：会话不存在与不归属当前用户返回同一错误（不泄露存在性）；
2. SQL 预筛：`content::text ILIKE '%kw%'`（走 GIN trigram 索引）+ `word_similarity` 排序取候选（limit×3）；
3. Python 精滤：ILIKE 会命中 JSON 结构键（`"type"`、`"output"` 等），按渲染后的纯文本过滤关键词真实出现；
4. 渲染：parts 提取 text/tool 内容（跳过 reasoning/retrieval），截断窗口围绕命中位置；
5. 输出按序号升序，单条截断（默认 2000 字符）+ 总量上限（默认 12000 字符）——检索不得成为读回全量历史的通道（实测单条 assistant 消息可达 147 万字符）。

**滚动形态**：`around_sequence` 给定时忽略 query，返回目标序号 ± window 条原文（window 服务端钳制，默认上限 10）——检索形态单条截断后的深读出口。

**跨会话发现**（`search_sessions`）：按当前 user_id 过滤消息 join 会话，排除当前会话，按 `word_similarity` 候选序取每会话最强命中分组（含 kind/parent_id 血缘，识别 subagent 会话）；返回的 session_id 供 `search_history` 定点跟进。

## 行为卫生

SOURCE-FIRST 规则内建在两个工具的 description 里：只证明「曾经说过」，不构成外部事实的证据；用户给出 URL/文件/账号等直接来源时先查原来源。与 `search_memory` 的分工（原文细节 vs 偏好结论）同时写进工具描述与 SuperAgent prompt `<history_recall>` 段。

## 运维（GIN 索引）

- `fastupdate=off`：消息表写入为会话节奏（每轮 2 行 + assistant parts checkpoint 更新），低写入速率下关闭 pending list——避免首次检索触发 pending 清理的延迟毛刺与内存膨胀。写入速率显著上升时重估。
- 计划退化排查：检索走 Seq Scan 时先 `ANALYZE t_chat_message`；仍异常查 `pgstattuple('t_chat_message')` 的死元组占比，膨胀明显则 `REINDEX CONCURRENTLY`。
- 短关键词（<3 字符）trigram 索引无法加速，退化为顺序扫描——工具描述引导用具体词（路径、错误码、函数名），已知取舍。
- 回滚 = 摘除工具挂载，索引可保留（无行为影响）。

## 评测口径

`evals/compression` 走真实 Agent 路径：fixture 按生产行状落库（`ChatService` 建会话 + 写消息），压缩用生产 `/compact` 宿主链路触发，每题每组经 LangGraph checkpoint fork 从同一压缩产物分叉作答。三组单变量链：uncompacted（不压缩）↔ current（压缩闭卷）只差压缩；current ↔ recovery 只差 `search_history` 挂载——recovery 组的检索即线上 pg_trgm 全文检索本身（fixture 已落库），不是进程内简化版。summary 单列「检索兜底收益」（recovery recall% − current recall%）。

实测（契约版压缩：上下文契约 + 摘要原子性 + 用户原话装回；5 fixture × 20 题分层 10:5:5 = 100 题，作答 huoshan/glm-5.3-flash、判卷 huoshan/deepseek-v4-flash，judge 解析失败率 0%、零未完成回合，结果目录 `evals/compression/results/final-100p/`）：

| 组 | recall%（5 fixture 均值） | retained tokens（中位） |
|---|---:|---:|
| uncompacted（不压缩） | 82.0% | 437385 |
| current（压缩闭卷） | 61.5% | 23064 |
| recovery（+search_history） | 78.0% | 23064 |

检索兜底收益 **+15pp，五份全部为正**（+10/+12.5/+25/+15/+20）——上下文契约（压缩后向模型声明上下文构成/盲区/恢复通道）消除了检索反降，检索定向找回被遮蔽区原文。分层看检索的价值集中在微观层（精确串/错误串，recovery detail 90% 反超不压缩 80%）；压缩净损失（闭卷 vs 不压缩 −20.5pp）经检索收窄到 −4pp。演进链：无兜底闭卷 35% → 原话装回 66%（单 fixture 口径）→ +上下文契约后检索组 78%（100 题口径）。
