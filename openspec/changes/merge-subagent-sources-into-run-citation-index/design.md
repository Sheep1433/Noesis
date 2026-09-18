# 设计：子 Agent 检索来源完整汇入主会话引用索引

## Context

跨边界来源传递链：

```
子 Agent 流式执行（executor._run_turn_via_pipeline）
  └─ on_tool_end / on_chat_model_end → _projection_boundary
       └─ _merge_task_sources(task, builder.to_dict())      # task.retrieval_sources 累积（≤MAX_TASK_SOURCES=200）
终态收口（executor._finalize_task → _publish_terminal_events → _notify_terminal）
  └─ notifications.record(sources=task.retrieval_sources.values())
主 Agent 下一次模型调用（BgNotifyMiddleware._injected_messages）
  └─ register_pending_sources(session_id, label, notice["sources"])  # _PENDING 合并（≤200/任务）
主 run finish（LangGraphSseBridge._emit_finish → register_cross_boundary_sources）
  └─ 桥接层 builder 登记（max_results=200）+ 补发 retrieval-results-available 帧
帧流 → RunProjection.apply（重建 parts）→ RunSnapshot → DB 落库（权威）
```

缺陷实测（drb-0478）：桥接层登记 290 条（`cross boundary sources registered` 日志），主消息落库只剩 89 条（3 任务 × 30）。根因在最后一环：**RunProjection 消费 `retrieval-results-available` 帧重建 parts 时，`register_retrieval_results` 未传 `max_results`，落进缺省的单调用上限 `max_results_per_call=30`**——任务级去重清单被按调用级上限截断，且 `results[:30]` 恰好取首见序前 30 条（与数据指纹一致）。下游链（通知→注入→drain→桥接层登记）经组件级测试逐一验证无损。

## Goals / Non-Goals

**Goals:**

- 主消息登记的每任务子 Agent 来源清单 = 桥接层登记清单（≤`MAX_CROSS_BOUNDARY_SOURCES=200`/任务上界不变）。
- 报告引用对这些来源可解析为编号角标（前端零改动：弧面板对 origin=subagent parts 不过滤，只分组）。
- 普通（非跨边界）检索帧的重建行为不变，不放大常规落库体积。

**Non-Goals:**

- 不改前端渲染（点号兜底逻辑保留——它服务「引用确实无来源」的合法场景）。
- 不回填历史消息（旧数据 30 条截断保持现状）。
- 不改 `MAX_CROSS_BOUNDARY_SOURCES=200` / `MAX_TASK_SOURCES=200` 上界与 excerpt 截断。
- 不处理模型编造 URL 的引用（仍渲染为点，属正当降级）。

## Decisions

### D1：投影重建按 origin 区分上界，跨边界帧沿用任务级上界

**决策**：`RunProjection` 重建跨边界检索帧（`origin.kind == "subagent"`）时传 `max_results=MAX_CROSS_BOUNDARY_SOURCES`；普通检索帧保持缺省调用级上限。

**理由**：登记侧（`register_cross_boundary_sources`）与重建侧必须同一上界，否则帧数据完整、落库截断——两侧任何一处缺参都会复现。按 origin 区分而不是全局放宽，避免普通检索帧（单工具调用输出）借道放大落库体积。

**备选**：投影重建全局传 200——否，普通帧的调用级上限是落库体积的既有保护；跨边界帧已有 origin 标记，语义上就该走任务级上界。

**备选**：登记侧把清单拆成多个 ≤30 条的帧——否，制造多 part 语义（前端弧面板聚合按 part 分组贡献者），且治标不治本（任何后续重建方仍会踩缺省上限）。

### D2：链路级回归测试钉住每一环

**决策**：`tests/test_subagent_sources_chain.py` 五条测试分别钉住：turn 级合并累积、通知链传递、子会话内容提取、**投影重建跨边界帧不截**（根因钉子）、普通帧仍守调用级上限。

**理由**：30 截断的教训是「登记侧修过上限（注释在案），重建侧漏了」——同一条数据流上的两个消费方各自调用 `register_retrieval_results`，上界约定没有单一事实来源。测试把整条链的每一环钉死，任何一环回退即红。

## Risks / Trade-offs

- [单消息体积增大（3 任务 × 200 条 × excerpt）] → excerpt 已有 `max_excerpt_chars/bytes` 截断；200/任务上界维持；实跑验证项核对主消息 parts 总量。
- [普通帧与跨边界帧的判别依赖 origin.kind] → 无 origin 的旧帧按普通帧处理（保守）；跨边界帧自 `register_cross_boundary_sources` 起必带 origin，链路内自洽。

## Migration Plan

后端单点发布（projection.py 一处 + 测试），无 schema 变更、无前端协调。回滚即 revert 提交（历史消息未回填，无数据迁移）。

## Open Questions

（无）
