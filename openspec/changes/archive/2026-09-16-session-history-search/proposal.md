# session-history-search

## Why

上下文压缩上线后，被压缩掉的历史在数据库里原地未动，但 Agent 无法访问：摘要丢掉的事实（路径、错误串、决策细节）一旦离开活跃上下文就找不回来，用户只能重述。调研业界三条主流 harness 先例后结论明确：确定性全文检索（不掺 LLM、结果即原文）是压缩恢复的标准答案，其中一家的实证数字是闭卷 recall 40% → 加检索兜底 68%。Noesis 的消息全量存在 Postgres、压缩边界已在摘要消息里留了标记——数据条件齐备，缺的只是查询入口。

## What Changes

- **新增 `search_history` 工具**（单会话事件级检索）：对指定会话（默认当前会话）的 `t_chat_message` 做全文检索，返回 top-k 命中片段（单条截断、总量有上限）；`before_compaction` 过滤器可只搜"被压缩遮蔽的区域"（按会话级压缩边界列 + message_sequence 过滤）。
- **新增 `search_sessions` 工具**（跨会话发现）：按关键词检索当前用户的历史会话、按会话分组返回（会话标题/时间 + 最强匹配片段），排除当前会话。
- **检索层实现**：Postgres `pg_trgm`（中文三元组开箱即用），确定性匹配，无 LLM 参与；与 `search_memory`（蒸馏记忆层）形成"原文层 / 蒸馏层"双层召回。
- **挂载策略**：默认挂载 SuperAgent（与 `search_memory` 并列）；GeneralQAAgent 等轻工具面不挂。
- **行为卫生约束**：工具描述内建 SOURCE-FIRST 规则——历史会话只证明"曾经说过"，不构成外部事实的证据。
- **评测接入**：`evals.compression` 增加 `recovery` 臂（闭卷作答 vs 加 `search_history` 兜底的对照），量化该能力的实际收益。

### 非目标

- 不改 `search_memory` 与记忆蒸馏层（两层各自演进，prompt 里的选用指引只做增量说明）。
- 不做向量/语义检索（先例一致选择确定性全文检索；语义召回由记忆蒸馏层承担）。
- 不做 Web 端会话搜索 UI（前端已有会话列表；本 change 只做 Agent 侧能力）。
- 不提供血缘追踪/精确事件读取类工具（先例中的 trace/read 工具族等有真实需求再立项）。

## Capabilities

### New Capabilities

- `agent-session-history`: Agent 侧会话历史检索——单会话（含压缩遮蔽区）与跨会话两层全文检索工具、挂载策略与行为卫生约束。

### Modified Capabilities

- `offline-evals`: 消息压缩评测新增 `recovery` 臂 Requirement（闭卷 vs 检索兜底对照）。

## Impact

- 代码：新增 `backend/packages/noesis-core/src/noesis/agents/tools/history_search_tool.py`（工具构造，对齐 `memory_tools.py` 形态）；新增 `noesis/services/history_search.py`（检索服务：pg_trgm 查询 + 压缩边界过滤 + 权限过滤）；`super_agent.py` 工具装配点挂载两个新工具；一次 Alembic 迁移（`t_chat_message.content` 建 pg_trgm GIN 索引 + `t_chat_session` 增加 `compaction_cutoff_seq` 列）。
- 评测：`evals/compression/` 的 arms 增加 `recovery`（作答模型挂 `search_history`、数据源接 fixture transcript 的进程内索引），spec delta 同步。
- 安全：跨会话检索强制 `user_id` 归属过滤（只搜当前用户的会话）；单会话检索校验会话归属。
- 成本：pg_trgm GIN 索引一次性建立；查询为确定性检索，无 LLM token 消耗。
