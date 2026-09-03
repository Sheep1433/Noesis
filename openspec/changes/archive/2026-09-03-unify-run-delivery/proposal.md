# unify-run-delivery

## Why

主会话与子 Agent run 的事件投递、wire 协议、前端流消费是两套平行实现。同一个端点 `GET /api/chat/runs/{run_id}/stream` 在服务端按 `origin == "subagent"` 分叉成两套事件方言与两份投递总线（RunManager sequence buffer vs executor 内存 history deque），前端相应长出两套 SSE 消费实现（`useSSEStream` vs 子会话 `consumeStream`）与两套领域状态。

上一轮统一（unify-agent-run-pipeline，2026-08-30）只收了**执行内核**（`stream_agent_events` + mapper + bridge），投递层与前端 reducer 被明确延后。此后每个新能力都落在这条边界两侧各写一次或只在分叉侧打补丁——实时统计、流式 delta、断流自愈无一例外。债不是一次性历史遗留，而是随每次功能迭代**再生产**；不断根的原因就是 wire 方言与投递总线本身是两份。

本轮全量审计另坐实两类现实损伤：

- 已上线契约 bug：子 Agent `POST /runs/{run_id}/stop` 服务层返回 `TAgentRun` ORM 行，API 层调 `.to_dict()`——模型无此方法，真实请求 AttributeError→500；前端 fire-and-forget 忽略响应，故障被掩盖。
- 功能静默失效：chat 页给共享渲染器传的 `:citation-index` prop 不在渲染器 props 中，弧引用编号在统一渲染边界断链，MarkdownPreview 退回自算序号。

## What Changes

- **统一投递内核（后端）**：per-run 的 sequence 分配、有界重放 buffer、订阅 fanout、transient 通道、重连恢复语义收敛为一份实现；RunManager 与子 Agent executor 各持实例；`_RUN_EVENT_HISTORY` / `_RUN_SUBSCRIBERS` 平行总线退役。
- **BREAKING** 统一 SSE wire 词汇：`/api/chat/runs/{run_id}/stream` 单一实现、不再按 origin 分叉。子 Agent 流改用主链路帧级词汇（`text-delta` / `tool-input-*` / `stats-update` / `finish` 等）+ run 级生命周期事件（`run.started` / `run.finished` / `approval.*`）；`message.updated` 全量投影事件与五类事件序号豁免白名单退役；transient 标记成为两侧通用的一等协议概念。前端同仓同步替换，无外部消费者。
- **子侧投影/落库统一（后端）**：executor turn 循环挂 RunProjection，检查点走 latest-wins 语义；`persist_projection` 裸 SQL 与「边界全量投影」路径退役。
- **后端样板收敛**：run/消息骨架创建 factory（`RunService.create` / `launch` / `create_followup_run` 三处调用）；usage 跨轮合并函数一份（executor / repository finalize / chat_service 三处调用、键集统一）；assistant 终态映射表一份（现为三份拷贝）；HITL reject/respond 决策应用收敛为一份（主链路现有三份拷贝），并补齐子 Agent 侧缺失的合成投影；子会话创建改走 ChatService 公共入口（消除私有方法引用）；重启对账 scope 显式化（通用 recovery 排除 subagent，消除按调用顺序隐式切分）；child 目录摘要 dict 形状一份（现为三份）。
- **子侧错误契约对齐（后端）**：类型化异常替换字符串嗅探；stop / HITL resume 状态冲突返回 409（现 500）；修复 stop 响应 `to_dict()` bug（恢复 RunSnapshot 契约）；写操作族 CSRF 策略统一。
- **前端传输内核**：抽 `useRunStreamClient`（SSE 解析含 `[DONE]`/CRLF、45s 读超时、退避重连、sequence 记账与 gap 处理、终态判定、abort/代际隔离、断流→`getAgentRun` 权威收口、重试耗尽用户可见失败）。主 run 流、会话信令流、子会话流三处换用；主聊天补 `abort` 帧处理；子会话补读超时与重试耗尽可见失败（现静默卡死）。
- **前端子会话领域层**：改消费统一帧词汇，assistant 内容投影复用 `messageParts` appenders（消灭两份流式追加实现：现主侧富逻辑 vs 子侧简化版）；`runEventReducer` 收窄为 run 生命周期 + 统计状态。
- **前端宿主壳共享化**：run-meta 行（轮数/步数/耗时 + 折叠）、「本轮未完成」blocker、统计条、stop/send 单按钮、composer 容器、HITL 审批卡、来源面板各收敛为一份共享组件；子 Agent 时间线内联的并行组渲染（第三份）收敛到共享渲染器；秒级计时器三份收敛。
- **死代码清理**：`user_stopped` 死标记（全仓只读不写）、`tools.py` `check_task` pending 态不可达分支（恢复 RUNNING/AWAITING_APPROVAL 的 pending 提示输出）、`HitlClarificationCard`（无消费方）、`_event_generator`（无调用点）、`sendMessage` POST messages 死封装、子视图死样式、`activeRunStreams` 注释与实现不符。
- **修复 citation-index 断链**：共享渲染器接收并转发 `citationIndex` 至 MarkdownPreview，弧引用编号恢复生效。

## Capabilities

### New Capabilities

（无——本变更全部为既有能力的收敛与契约修复，不引入新能力。）

### Modified Capabilities

- `agent-delivery`：投递契约从主链路专属扩展为 main + subagent 双 origin 统一——transient 通道进入协议、重放改为统一连续性校验（snapshot 降级兜底）、订阅配额与 owner 不可达 503 对子 run 生效、`/runs/{run_id}/stream` 单一实现。
- `agent-background-tasks`：「子会话详情与事件流」需求改写为统一词汇契约（帧级事件 + 生命周期事件、客户端帧自组装投影、断流恢复与主链路同模式、重试耗尽可见失败）；stop / resume 错误码对齐主链路（409/404，替代 500 与字符串嗅探）；stop 响应恢复 RunSnapshot 契约。
- `agent-hitl`：HITL 决策应用（reject/respond 合成 tool-output 投影）收敛为单一实现并对子 Agent run 生效；超时自动拒绝的语义与文案两侧统一。
- `platform-chat`：客户端流恢复行为（读超时、权威快照收口、重试耗尽可见失败、abort/`[DONE]` 处理）从 chat 页专属扩展为 run 流客户端通用契约；子会话视图复用 chat 页宿主壳组件（run-meta / blocker / 统计条 / 停止按钮 / 审批卡 / 来源面板）成为规格要求。

## Impact

- 后端：`chat/runs/manager.py`、`chat/delivery/`、`agents/subagents/executor.py`（投递/事件发布/投影落库重写）、`services/subagent_session_service.py`、`services/run_service.py`、`services/chat_service.py`、`services/qa/service.py`、`server/api/chat_api.py`（stream / stop / hitl-resume 端点 origin 分支消除）。
- 前端：`views/chat/useSSEStream.ts`（内核抽离）、`views/chat/messageParts.ts`（appenders 升格为双侧共享投影层）、`views/chat/runEventReducer.ts`（收窄）、`components/SubagentConversationView/`（流消费重写 + 宿主壳换共享组件）、`components/ConversationPartsRenderer/`（citationIndex 转发）；新增 `views/chat/useRunStreamClient.ts` 与共享宿主壳组件目录。
- 契约与文档：`/api/chat/runs/{run_id}/stream` 子 Agent 事件词汇变更（BREAKING，前端同仓消化）；`docs/architecture/platform/chat-streaming.md` §4.2b 事件清单与 §4.2a 信令说明同步；`docs/architecture/subagent-sessions.md` 同步。
- 明确不在范围：协作停止 vs 按轮取消的停止语义差异（产品语义保留两套）、followup 生命周期（入队/冷恢复/turn 参数覆盖）、bg_continuation_service、TEST_CASE_QA 旧 SSE 字符串路径（独立遗留另行清理）、channel 通道运行时（仅收敛其 HITL 决策应用拷贝）、主聊天 conversationItems 领域状态机迁移（投影层共享后其形状差异是合理设计）。
