## Context

`SUPER_AGENT_QA` 当前复用通用 SuperAgent。`deep-research-v2` Skill 负责研究问题拆分、检索策略、来源质量和报告结构；外层 Agent runtime 负责工具执行、SSE、checkpoint、assistant message 与上下文预算。

当前链路把三类不同数据混在一起：

```text
搜索候选 → tool output → RetrievalPart → assistant message → 前端来源列表
```

这会导致候选结果、已验证证据和最终引用没有稳定的身份关系。重复 URL 会重复进入消息，长搜索 JSON 会触发工具结果 offload，子 Agent 的失败难以区分为“某个工具失败”还是“研究结论不可用”。

本变更参考 ChatGPT Deep Research 的公开产品行为：先规划，展示进度，输出结构化报告和 Sources used；不假设其内部数据库实现，也不要求 Noesis 复制其内部模型或检索策略。

## Goals / Non-Goals

**Goals:**

- 只在 `SUPER_AGENT_QA` 的 research context 中启用外层 research harness。
- 保留 Skill 对研究行为的权威性，harness 不决定研究问题、查询矩阵、质量评分或报告章节。
- 区分 candidate、source identity、evidence、citation 和 activity。
- 通过 canonical URL、正文 hash 和 provenance 处理重复来源，同时保留查询与子 Agent 关系。
- 将完整候选和原始工具结果放到独立 trace/artifact，不把它们全部塞入模型上下文或 assistant parts。
- 最终报告只投影实际使用且可验证的引用来源。
- 使用动态上下文预算，不保留固定并行 `48K` 字符批次上限。
- 让工具、子 Agent、阶段和整轮研究拥有独立状态，支持部分完成和缺口说明。

**Non-Goals:**

- 不重新设计 `deep-research-v2` Skill 的研究协议、查询策略、来源质量评分或报告模板。
- 不为普通 `COMMON_QA`、`FAULT_OPERATION_QA` 或非 research 的 `SUPER_AGENT_QA` 工具调用建立完整 research trace。
- 不把所有候选结果默认展示在聊天正文中。
- 不在本变更中实现新的搜索供应商、爬虫、向量检索算法或来源质量模型。
- 不以固定的来源数量门禁替代 Skill 的研究完成判断。

## Decisions

### 1. Research context 由 Skill/运行入口显式激活

Harness 不通过用户关键词猜测是否为深度研究。运行入口或 Skill integration 提供稳定的 research context 标记，包含 `research_run_id`、`skill_id` 与当前阶段。未激活时，现有普通 SuperAgent 行为保持不变。

### 2. 研究数据分层

```text
Candidate：搜索返回的可能相关项
SourceIdentity：规范化 URL / 内容身份
Evidence：被抓取或验证、可支撑结论的内容片段
Citation：报告中的引用标记到 Evidence 的绑定
Activity：工具、子 Agent、阶段的执行记录
```

Candidate 可以被淘汰、合并或标记重复；Evidence 必须能回指 SourceIdentity 和原始快照；Citation 只能引用已存在的 Evidence。`RetrievalManifest` 作为现有聊天兼容投影，不再作为 research trace 的唯一事实来源。

### 3. 重复来源保留 provenance

先对 URL 做 scheme/host/path/query 规范化，再用内容 hash 判断同一正文。重复候选不重复展示，但保留 `query_id`、`tool_call_id`、`parent_task_call_id`、rank 和 provider 关系。正文高度相似但 URL 不同的内容建立 duplicate cluster，不直接删除任何原始记录。

### 4. 上下文预算与 trace 存储分离

原始 tool output 和 candidate trace 可以落盘；模型上下文只接收当前调用需要的有界摘要、来源身份和必要证据片段。单工具结果限制使用 token-aware 或等价估算；整体预算依据当前 model request 的剩余上下文动态计算。并行调用不再单独使用固定 `aggregate_max_chars`。

### 5. 终态和失败按层归属

子工具失败只更新对应 Activity/Candidate，不自动升级父 task。父 task 的明确成功或失败结果优先。研究只有在最终结论无法满足 Skill 要求时才进入 partial/error，并记录未覆盖问题、失败原因和重试结果。

### 6. 持久化使用元数据与原始 artifact 分离

Research metadata 需要可查询，采用 run/source/activity/evidence/citation 的关系结构或等价 JSONB 结构；大段原文和原始工具响应存入 session workspace artifact，通过 hash/path 引用。工具流式 chunk 不逐 chunk 写数据库；工具终态、证据升级、阶段变更和最终引用绑定才形成持久化检查点。

### 7. 对用户呈现采用 ChatGPT 式三段式

运行中展示研究活动和阶段进度；完成后展示结构化报告与 Sources used；候选、重复、淘汰和失败详情放在可选的 research trace/debug 入口，不默认污染聊天正文。

## Risks / Trade-offs

- [Risk] Research trace 增加持久化结构和清理成本 → 以 run 绑定、artifact retention 和会话删除联动，避免进入通用 assistant message。
- [Risk] 内容 hash 不能识别语义近似但文字不同的转载 → 保留 duplicate cluster 为辅助关系，不把近似判断作为删除依据。
- [Risk] 动态上下文预算依赖 provider usage 或 tokenizer 不可用 → 使用模型目录上下文上限和保守估算，并把是否发生截断写入 trace。
- [Risk] Skill 没有发出 research context 标记 → research harness 不应通过关键词猜测；运行入口需要在集成阶段明确失败或降级为普通 SuperAgent。
- [Risk] 报告引用未绑定到 evidence → 终态校验标记未解析引用，保留报告但将 run 标记为 citation_incomplete，不静默伪造来源。

## Migration Plan

1. 先增加 domain schema、trace recorder 和 URL/content identity 的离线测试，不改变现有 SSE。
2. 在 `SUPER_AGENT_QA` research context 中旁路记录 candidate/activity，验证与现有 `RetrievalManifest` 的一致性。
3. 增加 evidence/citation 终态投影，前端继续读取兼容的来源结构。
4. 切换 tool result budget 到动态上下文预算，移除固定并行 aggregate 限制。
5. 观察一批真实研究任务的 trace 大小、重复率、引用完整率和失败归因；异常时关闭 research harness，回退到现有消息投影。

## Open Questions

- research context 的标记由 Skill loader、SuperAgent run 入口还是独立 research plan 文件产生，需要在实现阶段选定唯一 owner。
- trace 查询 API 是否在本变更内提供，还是先通过 session workspace artifact 和内部诊断接口验证。
- 内容 hash 采用原始正文、规范化 Markdown 还是抽取后的正文作为 canonical content。
