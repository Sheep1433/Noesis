## Why

深度研究会话中，来源（检索结果的溯源信息）的采集与展示存在系统性的结构断裂：

1. **投影双轨漂移**：主 run 的桥接层把 `web_search` / `web_fetch` / KB 检索输出解析为结构化 retrieval parts 持久化（前端「来源 N」面板的数据源），而子 Agent 会话的投影只生成 text / tool parts——同一套消息格式有两个生产者，检索解析只接了主轨。子 Agent 承担了深度研究中绝大部分检索（实测单任务 20–34 次），但子会话抽屉不展示任何来源。
2. **跨边界来源丢失**：子 Agent 终态回传主 Agent 的通道（通知注入、`check_task`）只回传小结文本，子会话内抓取的来源结构在边界处丢弃。最终报告实际引用的素材大多来自子 Agent，主 Agent 的来源面板却只含主 Agent 自检索（深度研究轮次中常常为空）。
3. **引用与检索不分层**：「来源 N」展示的是全部检索结果去重数——抓 8 条用 1 条也计 8 条，深度研究轮次的数字必然虚高，用户无法分辨「报告建立在多少证据上」。
4. **多段消息与研究弧错位**：异步续跑把一轮研究拆成多条 assistant 消息（派发、进度、交付），引用只发生在末端的交付消息上；按消息逐条挂来源面板永远挂不对位置，且缺乏作用域隔离的会话级聚合会把相邻多轮研究的来源混在一起。

需要一套完整的来源溯源设计：子会话与主会话同构采集、来源结构跨边界传递、按研究弧聚合展示、引用与检索分层、身份去重与多轮隔离。

## What Changes

- **检索解析下沉共享**：把「检索工具输出 → retrieval part」的解析从主 run 桥接层下沉为共享投影工具，子 Agent 投影管线同样生成 retrieval parts 落库；子会话详情视图展示该子会话的来源面板。
- **来源跨边界传递**：子 Agent 终态通知与 `check_task` 结果携带该子会话的**去重来源清单**（结构化数据，不混入正文文本）；主 run 侧将其登记为带 origin 标记（来源归属：主 Agent / 具体子 Agent 任务）的 retrieval parts，落在收取发生的那条消息上持久化。
- **研究弧聚合展示**：过程消息（派发 / 进度叙事）SHALL NOT 渲染来源面板；同一研究弧（以上一条真实用户消息为界，系统通知注入不算边界）的全部 retrieval parts 在弧内**最后一条**消息上聚合展示，面板**平铺**（引用项在前、其余折叠其后；不按贡献者分组——2026-08-31 用户裁决）。
- **引用分层**：来源面板区分「引用 M / 共检索 N」——引用子集由 URL 归因判定（交付消息正文中出现的来源 URL 记为引用）；结构化引用（模型协议化引用标记）作为后续精确路线按模型验证白名单放开，二者叠加不冲突。文件交付（报告写入 workspace 文件）的轮次降级为仅展示「共检索 N」。
- **来源身份与冲突消解**：来源身份 = canonical URL（归一化去参）；聚合与去重按 canonical URL 进行，被多个贡献者使用的来源为**单一条目**携带多 origin 徽标；分组视图下条目在涉及的组内重复出现，但面板计数始终为去重数。同内容不同 URL 的残余重复为已知限制，内容级身份（正文 hash）归后续 research harness。
- **多轮隔离不变量**：来源数据落库即定稿（只追加、不回写历史消息）；面板为持久化数据的纯函数（弧边界确定性可算，刷新重算结果不变）；相邻研究轮次的来源互不渗透。
- 演进关系：本变更是 research harness（`super-agent-research-harness`，未实现）final citations 前端形态的先行版；harness 落地时数据源从「收取时登记」升级为「证据升级链路」，本变更的展示模型与隔离不变量保持不变。

## Capabilities

### New Capabilities

（无——全部落在既有能力的需求演进上）

### Modified Capabilities

- `agent-background-tasks`: 子 Agent 投影生成 retrieval parts（同构）；终态通知与 `check_task` 携带去重来源清单；子会话详情视图来源展示。
- `platform-chat`: 消息内容格式扩展 retrieval part 的 origin 标记；研究弧定义与来源面板聚合展示规则（过程消息无面板、弧内末条消息聚合、贡献者分组、引用 M / 共检索 N 分层、多轮隔离）。

## Impact

- 后端：`noesis/chat/event_mapping/`（retrieval 解析下沉为共享模块，桥接层改为调用）、`noesis/agents/subagents/executor.py`（`_child_projection_content` 接入检索解析）、`noesis/agents/subagents/notifications.py` / `tools.py`（通知与 `check_task` 携带来源清单）、`noesis/agents/subagents/`（子会话来源聚合提取）。
- 前端：`SubagentConversationView`（传递 retrieval-results 给既有渲染器）、`chat.vue` / 来源面板组件（弧级聚合、贡献者分组、多 origin 徽标、引用 M / 共检索 N、URL 归因过滤）、`messageParts.ts`（retrieval part 的 origin 字段解析）。
- 数据：无新表；retrieval part 结构新增可选 `origin` 字段（向后兼容，旧消息无该字段）；子会话落库内容新增 retrieval parts（存量子会话无来源，不回填）。
- SSE：`retrieval-results-available` 事件负载新增可选 origin 字段；不新增事件类型。
- 兼容性：旧消息（无 origin、子会话无 retrieval parts）渲染行为不变；`check_task` 返回文本追加来源清单段为模型侧纯增益，受工具输出预算约束。
