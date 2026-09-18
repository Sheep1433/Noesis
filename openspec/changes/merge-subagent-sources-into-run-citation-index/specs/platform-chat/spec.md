# Delta: platform-chat — 子 Agent 来源完整汇入

## MODIFIED Requirements

### Requirement: 平台 MAY 独立持久化 retrieval results

平台 MAY 使用独立 retrieval part 和 `retrieval-results-available` 交付工具来源，供恢复及来源抽屉展示。retrieval part SHALL NOT 声称其中每条结果都被最终答案引用。

子 Agent 后台任务的跨边界来源登记（通知 / check_async_task 双通道 → 主 run finish 时落主消息 retrieval parts）SHALL 传递该子任务的去重来源完整清单，上界为 `MAX_CROSS_BOUNDARY_SOURCES`（200 条/任务）；清单数据源 SHALL 为子会话落库消息的 retrieval parts（DB 权威），进程内存快照仅作无标准 run 场景的降级路径。登记路径 SHALL NOT 因单工具调用级条数上限（`max_results_per_call`）截断任务级清单。

#### Scenario: 刷新恢复研究回答

- **WHEN** 带 Markdown 引用的回答在生成中刷新
- **THEN** 普通 text snapshot SHALL 恢复已经生成的引用文本
- **AND** retrieval part SHALL 独立恢复

#### Scenario: 子任务来源超单调用上限时完整登记

- **WHEN** 子 Agent 任务经多轮检索累计去重来源 N 条（30 < N ≤ 200），主 run finish
- **THEN** 主消息该任务的 subagent retrieval part SHALL 登记 N 条来源
- **AND** part SHALL NOT 被 `max_results_per_call` 截断为 30 条

#### Scenario: 子会话来源支撑主报告引用编号

- **WHEN** 主 Agent 报告引用的 URL 存在于子会话落库来源中，且该来源已按上一场景完整登记进主消息
- **THEN** 客户端引用索引 SHALL 将对应引用渲染为编号角标（可跳转来源面板）
- **AND** 无匹配来源的引用 SHALL 继续渲染为无编号兜底标记

#### Scenario: 无标准 run 的任务降级

- **WHEN** 子任务无标准 run（shell / 测试任务），终态通知构建时无子会话落库消息可提取
- **THEN** 通知 SHALL 携带进程内存快照的任务级清单，登记行为与既有现状一致
- **AND** DB 提取失败 SHALL 记录 warning 日志且不阻断通知发送
