# Tasks: 子 Agent 来源完整汇入主会话引用索引

## 1. 根因钉住与修复

- [x] 1.1 链路级回归测试（tests/test_subagent_sources_chain.py）：钉住传递链每一环——turn 级合并累积、通知链传递、子会话内容提取、投影重建跨边界帧、普通帧调用级上限
- [x] 1.2 修复 RunProjection 跨边界帧重建：`origin.kind=subagent` 的帧传 `max_results=MAX_CROSS_BOUNDARY_SOURCES`，普通帧保持缺省；根因（桥接层登记 290 条、落库只剩 30/任务）经运行日志与数据指纹（首见序前 30）钉死

## 2. 回归与验证

- [x] 2.1 既有套件回归：链路 5 条 + message_builder / bg_subagent_executor / automation_runs / api_contract（108 条）+ research_source_provenance / run / bridge 契约（118 条）全绿；ruff 干净
- [x] 2.2 实跑验证：后台子任务模式冒烟——桥接层登记 40 条（运行日志）、主消息落库 40 条、子会话去重来源 40 条，三方一致，30 条截断指纹消失；前端零改动（弧面板对 subagent parts 按代码审计确认不过滤、只分组，来源清单补全即恢复编号）
