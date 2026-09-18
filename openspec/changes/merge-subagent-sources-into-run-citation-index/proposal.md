# 提案：子 Agent 检索来源完整汇入主会话引用索引

## Why

深度研究场景中，主 Agent 撰写的报告大量引用子 Agent 抓取的来源，但主会话消息落库的子任务来源清单被截断为每任务 30 条，前端引用索引因此匹配不到，报告里相当比例的引用上标退化为「·」（无编号不可点击）。实测（drb-0478，MCP Streamable HTTP 调研）：桥接层实际登记 290 条来源（日志铁证），主消息落库只剩 89 条（3 任务 × 恰好 30 条），报告 37 个去重引用 URL 中 23 个（62%）渲染成点；这 23 个全部存在于子会话落库来源中——不是模型编造引用，是落库截断。根因：RunProjection 消费跨边界检索帧重建 parts 时未传任务级上界，落进缺省的单调用上限 `max_results_per_call=30`。

## What Changes

- 修复 RunProjection 的跨边界检索帧重建：`origin.kind=subagent` 的帧沿用登记侧同一任务级上界（`MAX_CROSS_BOUNDARY_SOURCES=200`），普通检索帧保持调用级上限不变。
- 前端行为不变：来源面板与引用编号索引已有 subagent origin 支持（弧面板按 origin 分组、不过滤），清单补全后未编号上标自动变为编号角标，无前端改动。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `platform-chat`：修改「平台 MAY 独立持久化 retrieval results」需求——跨边界来源登记 SHALL 传递完整去重清单（受 MAX_CROSS_BOUNDARY_SOURCES 上界约束），且 MAY 从子会话落库消息兜底重建清单；新增场景验证「子 Agent 来源汇入后，主消息引用编号索引对这些来源可解析」。

## Impact

- 后端：`noesis/chat/runs/projection.py`（跨边界帧重建上界，唯一代码改动）；`tests/test_subagent_sources_chain.py`（链路回归，新增）。
- 数据影响：主消息 retrieval parts 的 subagent 来源条数增大（每任务最多 200 条，excerpt 已有截断保护），单消息体积上界可控。
- 兼容性：已落库的历史消息不回填（旧数据保持 30 条截断现状）；不影响主 Agent 自检索（origin=main）路径。
