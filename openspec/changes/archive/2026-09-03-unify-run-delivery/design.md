# unify-run-delivery · Design

## Context

上一轮统一（unify-agent-run-pipeline）把边界画在执行内核：`stream_agent_events`（`runtime/stream.py`）+ `RuntimeEventMapper`（`chat/event_mapping/mapper.py`）+ `LangGraphSseBridge`（`chat/event_mapping/langgraph_bridge.py`）现为双侧唯一 raw→typed 映射。边界之外是三层分叉，每层都已被本轮审计钉住：

| 层 | 主链路 | 子 Agent 链路 |
|---|---|---|
| 投递总线 | RunManager（`chat/runs/manager.py`）：lock 内单调 sequence + 2000 事件 buffer + 连续性重放 + 订阅配额/503 | executor（`agents/subagents/executor.py`）：`_RUN_EVENT_HISTORY` deque(128) + `_RUN_SUBSCRIBERS` 队列 + 五类事件序号豁免白名单 |
| wire 词汇 | 帧级事件（`text-delta` / `tool-input-*` / `stats-update` / `finish`…，`chat/delivery/sse.py`） | run 级投影（`message.updated` / `run.started` / `run.finished` / `approval.*` + transient delta） |
| 落库状态机 | RunProjection + PersistWriter latest-wins + `_persist_checkpoint`（`services/run_service.py`） | executor 边界全量投影 + `persist_projection` 裸 SQL（`services/subagent_session_service.py`） |

同一 URL `GET /api/chat/runs/{run_id}/stream` 在 `server/api/chat_api.py:986` 按 origin 分叉为两份端点实现；前端相应两套 SSE 消费（`views/chat/useSSEStream.ts` vs `components/SubagentConversationView/index.vue` 内联）、两套领域状态（conversationItems + `messageParts.ts` appenders vs `runEventReducer.ts`）、两份宿主壳（`runEventReducer.ts:6` 头注释自认主会话接入为「后续独立小步」——计划过，未发生）。

约束：子 Agent 在隔离 asyncio loop 执行，DB 访问须经 `run_on_main_loop` 桥接（executor 现状已如此）；dispatcher 明确排除 subagent run（`repositories/agent_run_repository.py:123-129`）；主链路多 Tab / 409 排队 / 跨窗口信令的恢复矩阵是既有产品行为，不可回退。

## Goals / Non-Goals

**Goals:**

- 投递语义一份：sequence、重放、订阅、transient、终态标记、配额，主/子 run 走同一协议与同一实现内核。
- wire 词汇一套：帧级事件 + run 级生命周期事件；前端传输层与投影层函数族各只有一份。
- 后端样板收敛：骨架创建、usage 合并、终态映射、HITL 决策应用各一份。
- 子侧错误契约对齐主链路（类型化异常、409/404、stop 响应 RunSnapshot、CSRF 统一）。
- 修复合并范围内已坐实的 bug：stop `to_dict()` 500、citation-index 断链、`check_task` pending 提示不可达。

**Non-Goals:**

- 停止语义统一（协作停止 vs 按轮取消是产品语义，保留两套状态机）。
- followup 生命周期（入队/冷恢复/turn 参数覆盖）、bg_continuation_service。
- TEST_CASE_QA 旧 SSE 字符串路径（`langgraph_bridge.py` `process_item`/`finalize`）与 channel 通道运行时（仅收敛其 HITL 决策应用拷贝）。
- 主聊天 conversationItems 领域状态机迁移（投影层共享后，其消息级形状差异是合理设计）。

## Decisions

### D1 投递内核抽取，而非子 run 并入 RunManager

抽 per-run 投递内核（落位 `chat/runs/delivery_bus.py`，暂名 `RunDeliveryBus`）：单调 sequence 分配、有界 ring buffer、订阅队列 fanout、transient 旁路、`subscribe(after_sequence)` 重放（连续性校验，不连续降级为要求快照恢复）与终态判定。RunManager 组合它（producer/resume/配额管理职责不变），executor 每任务持一个实例。

被否方案：子 Agent run 并入 RunManager 注册表。理由：RunManager 的 producer 生命周期管理（cancel_grace、resume、HITL producer 重建）与 executor 的 turn 循环生命周期是两种所有者模型；executor 在隔离 loop 运行而 RunManager 在主 loop，并入需全面 `run_on_main_loop` 桥接；dispatcher 排除 subagent 是既有设计。并入等于把两套复杂性焊成更大的单体，且不消灭任何一份语义。

loop 模型：内核实例由发布方所在 loop 持有（子侧=隔离 loop）；API 层经 `subagent_runtime_port.py` 端口取内核句柄读取——现状 `_RUN_SUBSCRIBERS` 的桥接模式保留，替换其背后的实现。

锁契约（关键）：RunManager 现状在 RunHandle lock 内完成 sequence 分配 + checkpoint 快照复制 + 输出上限判定 + buffer 写入（`manager.py` `_assign_and_buffer`），`agent-delivery` 规格「Run 状态写入 SHALL 保证 sequence 与 projection 原子一致」正依赖这一点。因此内核 SHALL 设计为**被动数据结构**：无自有锁，全部方法在持有方锁内调用（RunManager 持 asyncio lock、executor 持 threading lock），sequence 与投影的原子性由持有方锁纪律保证。内核若自带锁即破坏该不变量——这是实现时最容易犯的错。

### D2 wire 词汇统一到「帧级事件 + run 级生命周期事件」

统一词汇 = 主链路现有帧词汇全保留（`text-delta` / `reasoning-delta` / `tool-input-start|available` / `tool-output-available` / `context-update` / `stats-update` / `message-start` / `finish` / `run-snapshot` / `run-status`）+ 子侧生命周期事件升格为双侧词汇（`run.started` / `run.finished` / `approval.required` / `approval.resumed`）。退役：`message.updated` 全量投影事件、子侧五类事件序号豁免白名单、子侧独立 `context-update` 发布点（并入 bridge 帧词汇）。

被否方案 A：保留两方言、只统一传输契约。改动小，但词汇分叉正是「每个新能力写两次」的再生产根源，不除根。

被否方案 B：主聊天迁到 run 级投影词汇（向 `message.updated` 收敛）。投影词汇表达不了主聊天需要的细粒度语义（redacted-thinking 缓冲、`parent_task_call_id` 嵌套、压缩边界）；且 894 行 useSSEStream 与 4828 行 chat.vue 的咬合面重写风险倒挂。

子侧发布路径：executor `_consume` 不再丢弃 bridge 产出的 part 边界帧，全部经 D1 内核转发（按 D3 策略标注持久性）；生命周期事件（run.started/run.finished/approval.*）由 executor 照旧发布，进同一词汇流。

### D3 持久性是投递策略参数：durable / transient 两类，恢复模型一份

协议定义两类事件：**durable**（占 sequence、进 buffer、可重放、参与连续性校验）与 **transient**（不占 sequence、不进 buffer、仅投在线订阅者）。恢复模型两侧同构且唯一：重连 = `getAgentRun` 快照 replace + durable 事件重放续传 + live 接收。

发布策略：主链路 delta 类事件维持 durable（现状零变化，重放含 delta，多 Tab 二连对齐无回退）；子链路 `text-delta` / `reasoning-delta` / `stats-update` 标 transient（内容权威在服务端投影与 DB）。策略差异收敛在服务端发布点一处，前端不感知——前端协议只承诺「durable 必重放、transient 尽力而为、快照权威」。

子 worker 的 SessionStatsMiddleware 保持现状：其 registry 写入服务父会话「主+子合并」口径；其 stats-update WireFrame 在子侧被忽略属可容忍成本（纯内存、单次模型调用一帧），不为此改造中间件——曾考虑让 executor 消费该帧替代自算，但跨轮累计基座（`accumulated_usage`）仍在 executor，消费帧不减少职责，反而多一条耦合。

### D4 终态标记统一为 `run.finished`

现状核实：mapper（`mapper.py:_normalize`）已把 bridge 的 `finish` / `abort` / `error` 帧归一化为 typed 终态事件（RunPaused / RunAborted / RunError / RunCompleted），wire 上的 `finish` / `abort` / `error` 帧只是 SseDelivery（`delivery/sse.py`）对这些 typed 事件的编码名——**并不存在独立的「消息级 finish 帧」**。

统一方案因此是纯编码层改动：终态 typed 事件（RunCompleted / RunAborted / RunError）在 SseDelivery 统一编码为 `run.finished`（载荷：status / finish_reason / usage / model_calls），三个旧编码名退役；`hitl_pending` 的 RunPaused 维持非终态语义（run-status 形态）。`run.finished` 后随 `[DONE]` 为唯一流终止信号，双侧同词汇。前端 useSSEStream 现在不处理 `abort` 编码的静默丢弃洞随之消灭（终态判定改挂 `run.finished`）。

### D5 子侧投影/落库与主链路收敛（实施期修订）

原计划「executor 挂 RunProjection（双 builder 镜像）」在实施时**否决**：3b 词汇切换后，子会话内容投影在前端组装（messageParts appenders），服务端权威内容=落库检查点（bridge 写入的 executor builder 产物）；executor 再挂 RunProjection 会引入与主链路相同的双装配（每 token 两份内存写），且其 run 状态机与 BgTaskStatus 状态机平行、无进程内消费者。实质收敛件是**检查点事务单点**：`persist_projection` 裸 SQL 退役，改经 `AgentRunRepository.save_checkpoint`（与主链路 `_persist_checkpoint` 同一事务实现：sequence guard 单事务更新 run 快照与 assistant 内容），DB 抖动按主链路同款超时有界重试。

接线结论（保留，防双装配）：`bridge.map_item` 直接向传入 builder 写内容，`RunProjection.apply` 也向 projection.builder 写同样内容——共用一个 builder 即每条 delta 双写。主链路即靠「两个 builder 对象」避开（见 D6 审计任务）。executor 侧 builder 是 bridge 喂的唯一内容装配（单装配，优于主链路现状）。

注意两处既有依赖：cooperative-stop 的部分成果提取以投影边界为静止判据——改为事件驱动投影后，静止判据不变（仍在 on_chat_model_end / on_tool_end 边界），但投影内容来源变为 RunProjection 快照，回归测试须覆盖停止场景的部分成果提取；followup 链每 run 一个 RunProjection 实例（与主链路 per-run 语义一致），跨轮 usage 累计继续走 executor `accumulated_usage`（子侧生命周期能力，不进投影）。



### D6 后端样板收敛清单

| 收敛点 | 单一实现落位 | 现有三处 |
|---|---|---|
| run/消息骨架创建 | `chat/runs/skeleton.py` `create_run_skeleton()`，差异参数显式化（launch_payload/模型解析仅主链路传入） | `run_service.py:186-265`、`subagent_session_service.py:337-423`、`subagent_session_service.py:494-582` |
| usage 跨轮合并 | `chat/event_mapping/usage_normalize.py` 增 `merge_usage()`（键集=USAGE_FIELDS，含 model_calls 拼接与 step 重编号） | `executor.py:1795-1803`、`agent_run_repository.py:279-305`、`chat_service.py:449-471` |
| assistant 终态映射 | 一份 `{COMPLETED: completed, PARTIAL: partial, ERROR: error, INTERRUPTED: partial}` 表 | `subagent_session_service.py:754-759`、`run_service.py:541-546`、`run_service.py:602-607` |
| HITL 决策应用 | `chat/runs/projection.py` `apply_hitl_decisions` 扩展为完整投影变换（含 reject/respond 合成 tool-output）；qa/service 与 channel_run_service 改为调用；executor resume 时同样调用（补齐子会话被拒工具 part 状态投影——现状子侧缺位，被拒工具在子会话消息里无终态展示） | `qa/service.py:536-614`、`projection.py:267-294`、`channel_run_service.py:572-601` |
| 子会话创建 | 走 `ChatService.create_session` 公共入口（kind/extra 由参数传入），消除对私有 `_normalize_session_title` 的引用 | `subagent_session_service.py:354-375` 直构 ORM |
| 重启对账 | `RunRecoveryService` 显式排除 `origin=subagent`；子侧 `reconcile_orphaned_runs` 保留为唯一子 run 对账，消除「按 main.py 调用顺序隐式切分 scope」 | `run_recovery_service.py:30-129`、`subagent_session_service.py:61-98` |
| child 目录摘要 | 一份 dict 形状构造函数 | `chat_api.py:1341-1355`、`chat_api.py:1371-1385`、`executor.py:543-556` |

### D7 错误契约对齐

- `SubagentSessionService` 全部抛类型化异常：`stop_run`/`resume_hitl` 状态冲突 → `ConflictException`（409），不存在 → `NotFoundException`（404）；`chat_api.py:672-676` 的 `"不存在" in message` 字符串嗅探删除。
- 修复 stop 契约 bug：`stop_run` 返回 `RunSnapshot`（取消受理后经 `RunService.get` 重取或按受理态构造），恢复 `snapshot.to_dict()` 语义。
- 写操作族 CSRF 统一：`/runs/{id}/stop`、`/runs/{id}/test-case/resume`、`/sessions/{id}/subagent-followup` 与 `/runs/{id}/hitl/resume` 一致加 `require_csrf`。前端 `utils/authHttp.ts` 已对所有非 GET 请求全局注入 `X-CSRF-Token`，无需前端改动，仅补契约测试（无 token 拒绝、带 token 通过）。

### D8 端点合一

`/runs/{run_id}/stream` 单一实现：端点内抽象「取 run 投递句柄」接口（`subscribe(after_sequence)` / `snapshot()` / `is_terminal()`），主链路从 RunManager 取、子链路经 ExecutorPort 取内核句柄。统一行为：订阅配额与 429、owner 不可达 503 对子 run 生效；transient 旁路、keepalive、`run.finished`→`[DONE]` 唯一实现。queued 有界等待（主链路 `_subscribe_with_queued_wait`）对子 run 同样适用（子 run 也有 QUEUED 态）。

### D9 前端传输内核 `useRunStreamClient`

新增 `views/chat/useRunStreamClient.ts`：SSE 行解析（CRLF 兼容、`[DONE]` 一等处理）、45s 读超时（`Promise.race` + `reader.cancel`）、退避重连（策略参数化：maxRetries、退避公式、抖动）、断流先 `getAgentRun` 快照收口再重订阅、终态判定（`run.finished` / 快照终态集合）、代际隔离与 abort。对外为纯传输内核：`createRunStreamClient({ url, afterSequence, signal, policy, onEvent, onTerminal, onUnrecoverable })`，不含任何领域分派。

三处换用：主 run 流（`useSSEStream.followRun` 内部换内核，17 个回调签名与导出面不变，chat.vue 零改动）；会话信令流（`useSSEStream` 内信令循环，policy=无限重连无终态）；子会话 `consumeStream`（含补齐读超时与重试耗尽用户可见失败——现状静默卡死）。子 Agent 目录流（`childCatalogStream.ts`，EventSource 原生）不动：EventSource 内建重连 + 服务端连接快照对齐已够用，强行换内核是无收益改写。

### D10 前端子会话领域层：帧词汇 + 共享 appenders 投影

子会话流消费改统一帧词汇，assistant 内容投影复用 `messageParts.ts` appenders（`appendTextDelta` / `appendReasoningDelta` / `upsertToolInputPart` / `applyToolOutput`…——本来就是 `(parts, delta) => parts` 纯函数，与宿主容器解耦）；`appendStreamingDelta` 与「服务端 `message.updated` 全量投影」退役。`runEventReducer` 收窄为 run 生命周期 + 统计 + 快照游标状态（`assistantContent` 字段删除）。断流恢复与主聊天同模式：快照 replace + durable 重放。

### D11 前端宿主壳共享组件

| 组件 | 吸收两侧的 | 备注 |
|---|---|---|
| `RunMetaLine` | chat.vue run-meta 行 vs SubagentConversationView 同构实现（含折叠状态机） | 耗时文本四态归一 |
| `AssistantToolFailureBlocker` | 两份逐字符相同的 blocker markup+样式 | |
| `SessionStatsLine` | 两份统计条 markup+样式（计算已共享） | |
| `StopSendButton` | 单按钮 stop/send 状态机 + 光环样式 | 乐观/非乐观语义经 prop 策略传入（主=乐观，子=等往返） |
| `ChatComposerToolbar` | 子会话手写同构工具栏改为复用容器（props 收窄） | |
| `HitlComposerPanel` | 吸收 `HitlApprovalCard`（subagent 模式：无 clarification、逐条审批、恒 allow-session-grant）；`HitlClarificationCard` 死代码删除 | TaskCatalogPanel 第三套审批按钮同轮收敛 |
| 来源面板合一 | `ResearchSourcesPanel` 增加 flat 模式吸收 `CitationSources` | 子会话获得引用分组能力 |
| 并行组渲染 | `ConversationPartsRenderer` 导出 ParallelToolsGroup 供 SubagentCollapse 时间线复用 | 消灭第三份 |
| `useTicker` | 三份 setInterval 秒级时钟 | |

修复 citation-index 断链：`ConversationPartsRenderer` 增加 `citationIndex` prop 并转发至 MarkdownPreview（chat.vue:3377 现有传参即恢复生效）。

### D12 死代码与噪音清理

`user_stopped` 死标记（全仓只读不写，qa 侧 7 处读取点一并删除）；`tools.py:76-77` 不可达分支删除并把 pending 提示接回正常路径（恢复 RUNNING/AWAITING_APPROVAL 的 check_task 输出）；`_event_generator`（chat_api.py:680-715）；`api/chat.ts` 的 `sendMessage` POST messages 死封装与 `StreamSendMessageParams`；子视图死样式（`__run-meta-status`、`subagent-generating-pulse`）；`activeRunStreams` 声明改模块级与注释对齐。

## Risks / Trade-offs

- [主聊天流回归] useSSEStream 内核替换触碰核心流路径 → 对外 API（17 回调、导出面）不变、`useSSEStream.test.ts` 15 用例全绿为门禁；`abort` 帧洞修复有独立用例。
- [内核自带锁破坏原子不变量] 投递内核若做成自锁组件，会破坏「sequence 与 projection 原子一致」（RunHandle lock 内分配语义）→ D1 锁契约：被动数据结构、持有方锁内调用；RunManager 侧零行为回归测试为门禁。
- [RunProjection 双装配] executor 挂 RunProjection 时若共用 builder，bridge 与 apply 各写一次内容 → D5 接线规定双 builder 镜像主链路；单测断言投影 parts 无重复正文。
- [词汇切换窗口] 后端换词汇与前端子会话消费必须同步 → 原子提交（同一变更内后端发布 + 前端消费一起切），契约测试先行钉住新词汇；单仓单版本部署，无外部消费者。
- [transient 策略差异再分叉] durable/transient 双策略可能被误读为「两套协议」→ 协议文档（chat-streaming.md §4.2b）显式定义两类与恢复模型；策略只存在于服务端发布点一处。
- [RunProjection 子侧挂载改变检查点时序] 部分成果提取、HITL 种子合并依赖边界投影 → 专项回归：cooperative-stop 场景（硬取消对账）、HITL 中断/恢复 usage 种子、followup 冷恢复；真 DB 集成测试保留既有用例并按新投影源断言。
- [子会话 UI 大改] 宿主壳组件替换触碰 1053 行视图 → 每组件独立替换独立验证，交付手动验证清单；`runEventReducer.test.ts` 扩展帧词汇用例。
- [CSRF 收紧致 403] 前端封装未同步 → 同 PR 内前端统一携带 CSRF 头，契约测试覆盖三端点。

## Migration Plan

| 阶段 | 内容 | 合入粒度 |
|---|---|---|
| 0 | 立即修复：stop `to_dict()`、citation-index 转发、`check_task` pending 分支、`[DONE]` 解析噪音 | 独立小 PR，先行合入 |
| 1 | 后端样板收敛（D6 全表）+ 错误契约（D7）| 独立可合，行为变化仅错误码与 stop 响应 |
| 2 | 前端传输内核（D9）：useRunStreamClient 抽取 + 主 run 流/信令流换内核 | 独立可合，词汇未动 |
| 3a | 后端投递统一（词汇暂留）：D1 内核 + executor 发布改走内核 + 端点订阅管理合一（配额/503/queued 对子 run 生效，编码仍按 origin）+ 子侧挂 RunProjection（D5 双 builder 接线）| 独立可合，wire 词汇不变、前端零改动 |
| 3b | **原子切换**：终态统一编码 `run.finished`（D4，sse.py）+ executor `_consume` 转发全帧（`message.updated` 退役，D2/D3）+ 前端 useSSEStream 终态适配 + 子会话消费换帧词汇（D10）| 单一提交，前后端同切 |
| 4 | 宿主壳共享组件（D11）+ 死代码清理（D12） | 组件粒度分批 |
| 5 | 文档同步：chat-streaming.md §4.2 事件清单重写、subagent-sessions.md、契约测试固化 | 随阶段 3b/4 收尾 |

回滚：阶段 3b 为唯一前后端耦合点，DB 无 schema 变更（事件与 buffer 均为内存态），`git revert` 该原子提交即整体回退；阶段 0/1/2/3a/4 均可独立 revert。

## Open Questions

- 信令流（`/sessions/{id}/events`、`/events/stream`）的前端消费是否也换 useRunStreamClient：阶段 2 只换 useSSEStream 内的信令循环；两个 EventSource 流保持原生（见 D9），若后续信令协议复杂化再评估。
- statsline 模板编辑弹窗是否对子会话开放：产品决策，本变更不扩（子会话统计条已消费同一模板 key）。
