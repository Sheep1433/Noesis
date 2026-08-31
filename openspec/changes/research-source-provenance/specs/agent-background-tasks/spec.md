# agent-background-tasks Delta

## MODIFIED Requirements

### Requirement: 子会话详情与事件流

系统 SHALL 提供子会话消息历史与 run 事件流订阅（打开详情时建立、终态释放）；子会话的 assistant 消息内容 SHALL 与主会话使用同一 multipart 格式，并由共享投影逻辑生成：检索类工具（`web_search` / `web_fetch` / `search_knowledge_base`）的输出 SHALL 被解析为结构化 retrieval parts 持久化（与主 run 桥接层同构），检索工具 part 的展示输出 SHALL 为「检索到 N 条来源」摘要。子会话详情视图 SHALL 展示该子会话的来源面板（基于其落库 retrieval parts，会话内按 canonical URL 去重）。

#### Scenario: 子会话来源同构落库

- **WHEN** 子 Agent 执行 `web_search` 并获得结果
- **THEN** 子会话 assistant 消息内容 SHALL 含对应 retrieval part（query、results、truncated），与主会话同格式
- **AND** 该工具 part 的输出 SHALL 为「检索到 N 条来源」而非原始结果文本

#### Scenario: 子会话详情展示来源

- **WHEN** 用户打开子会话详情（抽屉 / 任务目录）
- **THEN** 视图 SHALL 基于子会话落库 retrieval parts 渲染来源面板，会话内按 canonical URL 去重
- **AND** 存量子会话（无 retrieval parts）SHALL 不渲染来源面板，其余展示不变

### Requirement: 完成通知注入

后台任务到达终态时 SHALL 向所属会话的待送达通知队列写入一条通知（task_id、终态、结果预览 ≤80 字），且通知负载 SHALL 附带该子会话的**去重来源清单**（canonical URL 归一化去重、有界；结构化字段，不混入预览文本）。该会话下一次 run 启动组装输入前 SHALL drain 队列并以 `[系统通知]` 前缀注入本轮上下文（注入文本以小结为主、来源清单以有界附录段携带），注入一次性且 SHALL NOT 写入消息落库内容；主 run 桥接层 SHALL 将通知携带的来源清单登记为带 origin 标记（归属该子 Agent 任务）的 retrieval parts，落在收取发生的 assistant 消息上持久化。系统 SHALL NOT 主动为通知启动 run；`awaiting_approval` SHALL NOT 注入模型通知。

#### Scenario: 通知携带来源清单并登记

- **WHEN** 子 Agent 终态且其子会话含检索来源
- **THEN** 终态通知负载 SHALL 携带去重来源清单（有界）
- **AND** 通知注入轮的 assistant 消息 SHALL 落库带 origin（该子 Agent 任务）标记的 retrieval parts
- **AND** 注入的用户消息落库内容 SHALL 与通知注入前一致（来源只进 assistant parts）

#### Scenario: 下一轮收到通知

- **WHEN** 后台任务完成，用户随后发送新消息
- **THEN** 本轮模型输入 SHALL 以 `[系统通知]` 前缀包含该任务完成提示、有界来源附录与 check_task 指引
- **AND** 再下一轮 SHALL NOT 重复出现该通知

#### Scenario: 用户消息原文不受污染

- **WHEN** 通知被注入某轮上下文
- **THEN** 该轮用户消息的数据库持久化内容 SHALL 与用户原始输入一致

#### Scenario: 续跑通知不伪装用户输入

- **WHEN** continuation run 因通知自动创建
- **THEN** 其 user 消息落库时 SHALL 携带来源标记（`extra.source_kind = bg_task_notice`）
- **AND** 前端 SHALL 将带该标记的消息渲染为系统通知条，SHALL NOT 渲染为用户消息气泡
- **AND** 续跑事件（`bg-continuation`）SHALL 携带通知全文与 child session 引用，前端据此实时插入同形态通知条

#### Scenario: 不主动唤醒

- **WHEN** 后台任务完成但用户未再发消息
- **THEN** 系统 SHALL NOT 自行启动模型调用；前端任务面板 SHALL 显示终态

## ADDED Requirements

### Requirement: check_task 携带来源清单

`check_task` 收取子任务结果时，返回文本 SHALL 在终态小结后附该子会话的**去重来源清单段**（有界，受工具输出预算约束）；主 run 桥接层 SHALL 将其登记为带 origin 标记（归属该子 Agent 任务）的 retrieval parts，落在收取发生的 assistant 消息上持久化。清单为模型侧纯增益：模型 SHALL NOT 被要求在正文中复述来源清单。

#### Scenario: check_task 收取登记来源

- **WHEN** 主 Agent `check_task` 收取一个含检索来源的终态子任务
- **THEN** 返回文本 SHALL 附有界来源清单段
- **AND** 收取轮的 assistant 消息 SHALL 落库带 origin（该子 Agent 任务）标记的 retrieval parts

#### Scenario: 无来源子任务

- **WHEN** 子任务无任何检索来源
- **THEN** `check_task` 返回与通知负载 SHALL NOT 携带空清单占位

### Requirement: 来源身份与跨边界去重

来源身份 SHALL 为 canonical URL（去 tracking 参数、统一协议与 host 大小写等归一化）；子会话来源清单提取与主会话研究弧聚合 SHALL 按同一归一化规则去重，前后端 SHALL 共享同一规则（含测试对齐）。同一 canonical URL 被多个贡献者检索或引用时 SHALL 合并为单一条目并携带完整 origin 列表；去重作用域为单个子会话内与研究弧内（跨弧不去重、不渗透）。

#### Scenario: 多子 Agent 同源合并

- **WHEN** 两个子 Agent 均检索并引用了同一 canonical URL
- **THEN** 主会话研究弧聚合面板中该来源 SHALL 为单一条目，origin 列表含两个子 Agent 任务
- **AND** 面板计数 SHALL 只计一次

#### Scenario: 跨弧不渗透

- **WHEN** 相邻两个研究弧（两次真实用户消息发起）均使用了同一来源
- **THEN** 该来源 SHALL 在两个弧的面板中各出现一次，SHALL NOT 相互合并或递增对方计数
