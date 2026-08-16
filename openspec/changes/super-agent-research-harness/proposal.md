## Why

`SUPER_AGENT_QA` 的深度调研行为由 `deep-research-v2` Skill 约束，但当前外层运行时把搜索结果、引用 evidence、工具输出和 assistant 消息混在同一条链路中处理。结果是重复 URL、重复正文、过大的工具结果、引用清单膨胀和子 Agent 局部失败难以解释，最终报告的来源追溯也不稳定。

需要增加一个只负责运行时治理的 research harness：保留研究 Skill 的策略自主权，同时把候选结果、已验证证据、最终引用和执行活动分层管理。产品行为参考 ChatGPT Deep Research 的“计划—进度—结构化报告—Sources used”模式，但不把所有中间搜索结果塞入聊天消息或前端正文。

## What Changes

- 为 `SUPER_AGENT_QA` 增加深度调研运行时上下文，区分 research trace、candidate、evidence 和 final citation。
- 为搜索结果建立 canonical URL、正文 hash 和来源 provenance，支持跨查询、跨子 Agent 去重但保留命中关系。
- 将完整工具结果从 assistant message / model context 中分离；原始结果按需落盘，模型只接收有界摘要或引用指针。
- 将 evidence 记录为可验证的来源片段，并保存其来源、抓取版本、定位信息及支持的报告引用关系。
- 最终报告只展示实际使用的来源；候选结果和活动记录保留在 research trace 中，不默认展开为聊天正文。
- 移除固定的并行工具合计字符预算，改为基于当前模型上下文剩余空间的动态工具结果治理。
- 明确工具和子 Agent 的局部失败、重试、跳过、部分完成与研究缺口，不因一个子工具失败自动判定整轮研究失败。
- 增加研究活动、来源去重、证据绑定和引用完整性的可观测字段与回归测试。

## Capabilities

### New Capabilities

- `research-trace`: 管理深度调研的候选结果、工具活动、去重关系、证据升级和最终引用映射。

### Modified Capabilities

- `agent-runtime`: 为 `SUPER_AGENT_QA` 增加 research harness 的运行时治理、工具结果预算和 provenance 生命周期；研究策略仍由 Skill 负责。
- `agent-delivery`: 补充 research activity、候选/证据状态和局部失败的事件语义，保持现有 SSE 兼容。
- `platform-chat`: 明确报告引用与 research trace 的展示边界，最终消息只展示实际使用的来源。

## Impact

- 后端：SuperAgent 装配、web_search/web_fetch 结果适配、evidence/manifest、工具结果 offload、运行时事件和 assistant 持久化边界。
- 数据：可能新增 research trace、source identity、candidate/evidence/provenance 结构；现有 assistant 消息 API 不应把全量候选直接塞入 `parts`。
- 前端：保留当前报告和来源展示；可增加研究进度与 Sources used 的数据映射，候选和调试轨迹不作为默认聊天正文。
- 配置：搜索条数、抓取长度、证据摘要和上下文预算需要从固定耦合值改为分层配置；不改变 Skill 内的研究协议。
- 兼容性：`/api/chat` 现有 SSE 基础事件和普通 `SUPER_AGENT_QA` 对话保持兼容；新增 research 字段和事件应为可选字段。
