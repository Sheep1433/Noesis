## Context

来源数据的现行链路：主 run 桥接层（`langgraph_bridge._on_tool_end`）对检索类工具输出调用 `retrieval_payload` 解析并经 `register_retrieval_results` 生成 retrieval parts 持久化；前端 `retrievedResults(parts)` 平铺全部结果、`buildCitationIndex` 按 URL 去重编号，「来源 N」= 全部检索结果去重数。子 Agent 路径（`executor._child_projection_content`）是消息折叠的第二个独立实现，只生成 text / tool parts，不接检索解析。子 Agent 终态回传（`notifications.record` 注入、`check_task` 收取）只携带小结文本。

实测断点（深度研究会话）：主 Agent 自检索 0 次时主消息来源面板为空，而 6 个子 Agent 各检索 20–34 次、合计抓取 900+ 条结果全部不可见；子会话抽屉把检索过程显示为纯工具输出流水。

## Goals / Non-Goals

**Goals:**

- 子会话与主会话的检索来源同构采集与展示（消灭投影双轨在检索维度的漂移）。
- 来源结构跨过「子 Agent → 主 Agent」收取边界，在主会话持久化且带贡献者归因。
- 展示模型对齐真实使用节奏：过程消息无来源面板，交付消息聚合研究弧的来源。
- 引用与检索分层（引用 M / 共检索 N），先以 URL 归因实现，结构化引用留升级位。
- 来源身份（canonical URL）去重与多贡献者合并展示。
- 多轮研究的严格隔离：落库即定稿、面板纯函数重算。

**Non-Goals:**

- 不实现内容级来源身份（正文 hash）与证据分层（candidate → evidence → final citation）——归 research harness；本变更接受同内容不同 URL 的残余重复。
- 不改变研究过程的消息形态（续跑多段消息、任务卡、通知条保持现状）；不做独立 research trace 面板（harness 范畴）。
- 不改动检索工具本身（web_search / web_fetch / KB 检索的行为与输出格式不变）。
- 不做历史数据回填（存量子会话无来源数据，不补投影）。

## Decisions

### D1. 检索解析下沉：一个解析器，两条管线

把 `retrieval_payload(raw) → {query, results, truncated}` 与 retrieval part 构造从桥接层抽为共享模块（如 `noesis/chat/event_mapping/retrieval.py`）。桥接层与 executor 的 `_child_projection_content` 都调用它：对 `web_search` / `web_fetch` / `search_knowledge_base` 的工具结果生成结构化 retrieval parts（含 query、results、truncated），与既有 part 格式完全一致。工具 part 的展示输出同步替换为「检索到 N 条来源」（与主轨现状一致）。

**边界界定**：只下沉「part 构造」这一层——桥接层其余职责（SSE 分帧、状态机、usage、step_id）不下沉。全面统一两条投影管线是 harness 落地时的事（其证据分层本就要同时动两条管线），此处只消除检索维度的漂移。

### D2. 跨边界传递：来源清单在收取点结构化落位

子 Agent 终态时，从其子会话投影的 retrieval parts 提取**去重来源清单**（canonical URL 归一化后去重，有界）。两条既有回传通道携带该清单：

- **终态通知**（`notifications.record` → 注入文本 / `bg-continuation` 事件）：通知负载增加结构化 `sources` 字段（注入给模型的文本仍以小结为主，来源清单以附录段有界携带）。
- **`check_task` 收取**：返回文本在终态小结后附有界来源清单段（受 `tool_output_max_chars` 预算约束）。

主 run 侧（桥接层）：对通知注入与 `check_task` 输出中携带的来源清单，登记为 retrieval parts 并打 **origin 标记**（`{kind: "subagent", label: 任务标题}`；主 Agent 自检索 origin 为 `{kind: "main"}`，缺省视为 main，向后兼容旧数据），落在**收取发生的那条消息**上持久化。数据落位与展示位置解耦：收取消息（通常是过程消息）落库来源数据，但不渲染面板（D3）。

### D3. 研究弧聚合展示：过程无面板，交付聚合

**研究弧定义**：从一条**真实**用户消息开始到下一条真实用户消息前的全部 assistant 消息。系统通知注入的消息（`source_kind = bg_task_notice`，落库为 user）SHALL NOT 作为弧边界——续跑产生的多段消息同属一弧。弧边界仅依赖消息历史既有字段，确定性可算。

**展示规则**：

- 弧内的过程消息（派发、进度叙事）SHALL NOT 渲染来源面板；
- 弧内**最后一条**消息（交付消息；被打断的弧为其末条消息）渲染该弧全部消息 retrieval parts 的聚合面板；
- 面板结构：**平铺展示**（引用项在前、其余检索来源折叠其后；2026-08-31 用户裁决——不按贡献者分组，origin 归因仅落库不用于展示）；**面板计数为去重数**（D5）；
- 弧内无 retrieval parts 时不渲染面板（自然降级）。

### D4. 引用分层：URL 归因先行，结构化引用留升级位

- **URL 归因（本变更实现）**：交付消息**正文中出现的来源 URL** 记为已引用（canonical URL 匹配）。面板标题「引用 M · 共检索 N」，默认展开 M 条引用项，N 为次要信息。引用判定只看交付消息正文——过程消息正文不参与（避免进度叙事中的 URL 误标）。
- **结构化引用（白名单路线，不在本变更）**：模型协议化引用标记（`structured_citations` 机制已有配置位）按模型验证白名单放开后，作为引用判定的精确来源，与 URL 归因叠加（结构化命中优先，URL 归因兜底）。
- **文件交付降级**：交付物为 workspace 文件、交付说明正文不含来源 URL 时，引用子集不可判定，面板降级为仅「共检索 N」；结构化引用启用后此降级自然消除。

### D5. 来源身份：canonical URL + 多 origin 合并

- **身份键** = canonical URL：去 tracking 参数、统一协议与 host 大小写、移除尾部冗余分隔符。前端 `citationKey` 归一到同一实现，前后端共享归一化规则（常量模块或前后端各持同一规则测试对齐）。
- **去重作用域**：子会话内各自去重（子 Agent 视图）；主会话研究弧内去重（聚合面板）。
- **多贡献者合并**：同一 canonical URL 被多个贡献者（主 Agent + 多个子 Agent）检索 / 引用时为**单一条目**（origin 归因数据保留在落库 parts 内，供 harness 消费；展示层不渲染多源徽标——2026-08-31 用户裁决）。冲突消解发生在引用过滤之后：仅被检索未被引用的来源折叠在「其余检索来源」区（留在对应子会话视图内）。

### D6. 多轮隔离不变量：落库即定稿，面板纯函数

三条硬不变量，保证相邻研究轮次互不渗透、历史消息刷新后引用不变：

1. **只追加、不回写**：retrieval parts 持久化在收取发生的消息上；后续轮次产生新消息上的新 parts，对历史消息零触碰。系统 SHALL NOT 维护会话级可变来源累加器（会话级可变集合正是多轮渗透的根源）。
2. **弧边界确定性**：仅由消息历史（真实用户消息位置、`source_kind` 标记）决定，无时间窗、无运行时状态。
3. **面板纯函数**：某消息的面板 = 该消息所属弧的全部消息落库 parts 合并去重（+ origin 分组 + 引用过滤）的纯函数计算；同输入必同输出，刷新重算不变。

**followup 语义**：追问式续跑（「针对第 X 章再深挖」）构成新弧；冷恢复子 Agent 续跑产生的来源登记到新弧消息上，同一 URL 在两个弧的面板中各出现一次（两轮均真实使用），去重作用域为弧内。

### D7. 演进：harness final citations 的先行版

本变更交付的「弧级聚合 + origin 分组 + 引用过滤」与 research harness 的 final citations 展示形态一致。harness 落地时：数据源从「收取时登记清单」升级为「证据升级链路（candidate → evidence → citation）」，来源身份从 canonical URL 升级为 + 正文 hash（消除同内容不同 URL 残差），前端展示模型与 D6 不变量不推翻。因此本变更的所有数据字段（origin、sources 清单）按 harness 可消费的形态设计。

## Risks / Trade-offs

- **来源清单跨边界传输的体积**：单子 Agent 去重后来源可达百条级；通知注入文本与 `check_task` 返回文本须有界（预览级截断 + 工具输出预算），完整清单始终以子会话落库数据为准。主会话结构化登记携带完整去重清单（上限 200，见 MAX_CROSS_BOUNDARY_SOURCES）——面板「共检索 N」须反映真实检索量，按单工具调用条数上限（30）截断会让计数失真（2026-08-31 用户裁决）。
- **URL 归因的召回缺口**：模型转述事实不附 URL 时该来源不会被记为引用（引用数偏低）；文件交付整轮降级。结构化引用是完整解，按模型验证节奏补齐。
- **子会话落库内容体积增长**：retrieval parts 落入子会话 assistant 消息，投影体积上升；沿用既有 `tool_output_max_chars` 与结果条数上限约束。
- **弧内最后一条消息的判定**：弧闭合于下一条真实用户消息出现时——历史弧聚合稳定；进行中的弧随新消息推进，面板位置随之移动（交付前的中间态），属预期行为。
- **前端聚合复杂度**：弧级聚合、分组、去重、归因过滤为纯前端计算（数据已落库），复杂度集中在来源面板组件；旧消息（无 origin）按主 Agent 归组，不破坏渲染。
