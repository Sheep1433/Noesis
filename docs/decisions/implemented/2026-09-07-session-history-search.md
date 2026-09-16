# 决策：会话历史检索选确定性全文（pg_trgm）+ 两个窄工具 + 压缩边界存会话列

状态：implemented
日期：2026-09-07

## 问题

上下文压缩上线后，被压缩掉的历史在数据库里原地未动，但 Agent 无法访问：摘要丢掉的事实（路径、错误串、决策细节）一旦离开活跃上下文就找不回来，用户只能重述。需要给压缩恢复一个查询入口，且这个入口不能反过来摧毁压缩的意义（读回全量）。

## 决策

四个互相咬合的决定（openspec: session-history-search）：

1. **检索技术选 pg_trgm 确定性全文匹配，不掺 LLM、结果即原文**。`t_chat_message.content` 建 `(content::text) gin_trgm_ops` 索引，ILIKE 预筛 + `word_similarity` 排序 + Python 侧渲染文本精滤（淘汰 JSON 结构键噪声）。中文三元组开箱即用，无需分词器。
2. **两个窄工具而非一个万能工具**：`search_history`（单会话事件级，含 `before_compaction` 遮蔽区过滤与 `around_sequence` 滚动深读）与 `search_sessions`（跨会话发现，返回 session_id 供定点跟进）。单条截断 2000 字符 + 总量上限，检索不得成为读回全量的通道。
3. **压缩边界写入 `t_chat_session.compaction_cutoff_seq` 新列，不依赖摘要消息携带元数据**。摘要消息是注入上下文的 HumanMessage、不落库；边界在压缩完成时由中间件回调写入，口径为「排除活跃 run streaming 骨架行后的最大已终态消息序号」（保守上界，对齐 `memory_extracted_seq` 列先例）。
4. **SuperAgent 默认挂载、GeneralQA 不挂；数据源直接查 `t_chat_message`**，不给压缩归档文件建索引。SOURCE-FIRST 规则内建工具描述：历史只证明「曾经说过」，不构成外部事实的证据。

业界取向参考：三条主流 harness 先例全部选择确定性全文检索（无一用向量），其中一家的实证数字是闭卷 recall 40% → 加检索兜底 68%——这是 recovery 评测臂要本地复刻的对照。

## 备选方案

- **Qdrant 向量检索**：语义召回与记忆蒸馏层（`search_memory`）职责重叠；嵌入结果非原文需引用映射；「找回说过的原话」是精确匹配问题，语义近似反而引入噪声。输在职责与复杂度不匹配。
- **单工具承载单会话/跨会话两种语义**（可选参数切换）：模型对可选参数的决策不稳定，schema 模糊、默认行为不可预期。先例共识也是拆窄工具。
- **摘要消息 additional_kwargs 携带边界**：初稿假设，不成立——摘要消息不落 `t_chat_message`（该表只有 user/assistant 行，assistant 按骨架—检查点—终态单行落库），边界无处可挂。
- **graph 消息逐条映射 DB 行算精确边界**：graph 消息 id 由 LangGraph reducer 生成、与 DB 行 uuid 零关联，ToolMessage 无独立行——映射不可行，退而取保守上界。
- **给 `/conversation_history/*.md` 归档建索引**：归档是快照副本（压扁、无索引、每压缩一份），检索走它等于绕开权威源，且与库内状态可能漂移。归档保留审计用途，检索不消费。
- **Web 端会话搜索 UI**：前端已有会话列表；本能力只做 Agent 侧，UI 有真实需求再立项。
- **时间范围过滤参数（先例有 created_at_from/to）**：YAGNI，首版不加，避免 schema 膨胀。

## 后果与代价

- 短关键词（<3 字符）trigram 索引无法加速，退化为顺序扫描——工具描述引导用具体词，已知取舍。
- 边界列是保守上界：`before_compaction` 可能略含仍在保留尾的近期消息（宁多标不漏标）；极端 case（当前轮工具链超长被部分压缩）下当前轮 user 行可能漏标，普通 `search_history`（不带过滤）仍可覆盖。
- 存量已压缩会话无边界标记，`before_compaction` 退化为全历史（可接受的降级：边界缺失时宁可多搜）。
- task-worker（隔离 loop）不挂检索工具——pg_manager 连接池绑定主 loop；子 Agent 需要历史时结论经父会话回流。
- GIN 索引有一次建索引成本与写入侧维护成本（`fastupdate=off`，调参依据见 `services/history_search.py` 模块注释）；若实测模型滥用检索（每轮都查），后续可加调用预算，不在首版。
- 语义召回仍归记忆蒸馏层：两层各自演进，两层的使用率由 recovery 评测臂观察。
