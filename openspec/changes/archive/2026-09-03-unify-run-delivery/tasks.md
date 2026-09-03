# unify-run-delivery · Tasks

> 阶段 0-2、3a、4 可独立合入；阶段 3b 为原子切换（后端发布与前端消费同一提交），顺序不可调换。

## 1. 阶段 0：立即修复（独立小 PR）

- [x] 1.1 修复 `SubagentSessionService.stop_run` 返回 `TAgentRun` 导致 `chat_api.py` `to_dict()` AttributeError→500：返回 `RunSnapshot`（受理态构造或经 `RunService.get` 重取），契约测试改用真实快照断言（替换带 to_dict 的假对象 mock）
- [x] 1.2 `ConversationPartsRenderer` 增加 `citationIndex` prop 并转发至 MarkdownPreview，恢复 chat 页弧引用编号（`chat.vue:3377` 现有传参生效）；补渲染断言
- [x] 1.3 `agents/subagents/tools.py` 删除 `_format_task` 不可达 pending 分支（无条件 `return head` 之后），把 `_CHECK_PENDING_HINT` 接回正常路径，恢复 RUNNING/AWAITING_APPROVAL 的 check_task 输出；补单测覆盖 pending 态文案
- [x] 1.4 子会话流消费 `[DONE]` 特判（消除每次正常结束的 `console.warn` 解析噪音）

## 2. 阶段 1：后端样板收敛 + 错误契约（独立可合）

- [x] 2.1 抽 `chat/runs/skeleton.py` `create_run_skeleton()`：收敛 `run_service.py` `RunService.create`、`subagent_session_service.py` `launch` / `create_followup_run` 三处骨架样板（reserve sequences → user 行 → assistant 骨架 → queued run），差异参数（launch_payload/模型解析/origin）显式化
- [x] 2.2 `usage_normalize.py` 增 `merge_usage()`（键集=USAGE_FIELDS，含 model_calls 拼接与 step 重编号），替换 `executor._merge_usage`、`agent_run_repository.finalize`、`chat_service.update_assistant_message` 三处各自实现；补三处调用点的合并语义单测（键集不一致回归）
- [x] 2.3 assistant 终态映射 `{COMPLETED: completed, PARTIAL: partial, ERROR: error, INTERRUPTED: partial}` 收敛为一份（`subagent_session_service` / `run_service` 两处三份拷贝）
- [x] 2.4 子会话创建改走 `ChatService.create_session` 公共入口，删除对私有 `_normalize_session_title` 的引用
- [x] 2.5 `RunRecoveryService` 显式排除 `origin=subagent`；`reconcile_orphaned_runs` 保留为唯一子 run 对账；启动处补注释钉住两 pass 的 scope 边界（不再依赖调用顺序隐式切分）
- [x] 2.6 child 目录摘要 dict 形状收敛为一份构造函数（`chat_api.py` 两处 + `executor.py` 一处）
- [x] 2.7 `SubagentSessionService` 类型化异常：`stop_run` / `resume_hitl` / `send_followup` 状态冲突 → `ConflictException`(409)，删除 `chat_api.py:672-676` 字符串嗅探；越权/不存在 → `NotFoundException`(404)
- [x] 2.8 写操作族 CSRF 统一：`/runs/{id}/stop`、`/runs/{id}/test-case/resume`、`/sessions/{id}/subagent-followup` 加 `require_csrf`（前端 `authHttp` 已全局注入 X-CSRF-Token，无需前端改动）；契约测试覆盖三端点（无 token 拒绝、带 token 通过）
- [x] 2.9 死代码清理：`user_stopped` 死标记（qa 侧 7 处读取点）、`chat_api._event_generator`、`api/chat.ts` `sendMessage` POST messages 死封装与 `StreamSendMessageParams`、子视图死样式、`HitlClarificationCard`

## 3. 阶段 2：前端传输内核（词汇未动，独立可合）

- [x] 3.1 新建 `views/chat/useRunStreamClient.ts`：SSE 行解析（CRLF/多行 data/`[DONE]`）、45s 读超时（Promise.race + reader.cancel）、退避重连策略参数化（maxRetries/公式/抖动/无终态无限重连模式）、代际隔离与 abort、断流先 `getAgentRun` 快照收口再重订阅、重试耗尽回调；纯传输内核，无领域分派
- [x] 3.2 `useSSEStream.followRun` 内部换用内核：对外 17 回调与导出面零变化；`useSSEStream.test.ts` 15 用例全绿为门禁
- [x] 3.3 `useSSEStream` 内会话信令循环换用内核（无限重连无终态模式）

## 4. 阶段 3a：后端投递统一（wire 词汇不变，独立可合）

- [x] 4.1 抽 `chat/runs/delivery_bus.py` 投递内核：**被动数据结构**（无自有锁，持有方锁内调用——RunManager 用 asyncio lock、executor 用 threading lock，保住 sequence/投影原子一致不变量），提供 sequence 分配/有界 buffer/订阅 fanout/transient 旁路/连续性重放与快照降级；RunManager 改为组合内核，主链路零行为回归测试为门禁
- [x] 4.2 executor 事件发布改走内核：`_RUN_EVENT_HISTORY` / `_RUN_SUBSCRIBERS` 退役；`_publish_run_event` 内耦合的父会话 child-session 目录推送（`publish_session_event`）**保留**并移到生命周期状态变更点，父会话目录实时更新不受影响
- [x] 4.3 端点订阅管理合一：`/runs/{run_id}/stream` 内抽象投递句柄（主=RunManager、子=ExecutorPort 取内核），配额 429 / owner 503 / queued 等待对子 run 生效；编码此阶段仍按 origin（词汇切换在 3b）
- [x] 4.4 子侧检查点事务收敛（实施修订：RunProjection 满挂载否决——与 3b 前端组装架构冗余且引入双装配，见 design D5）：`persist_projection` 裸 SQL 退役，改经 `AgentRunRepository.save_checkpoint`（主/子同一事务实现）+ 主链路同款有界重试；死 status 参数删除；回归覆盖 cooperative-stop 部分成果提取、HITL 种子合并、followup 冷恢复（executor 全量 73 绿）
- [x] 4.5 审计主链路 service builder 双装配：**确认**——typed happy path 上 qa/service 层 builder 由 bridge 逐 token 装配，但仅异常兜底路径（exec_query error fallback）消费；权威快照来自 RunManager 侧 projection.builder。双装配浪费成立，结论记入决策记录（7.4），修复列为后续小改不在本变更强改

## 5. 阶段 3b：词汇切换（原子提交，前后端同 PR）

- [x] 5.1 契约测试先行：钉住新词汇（帧事件 + `run.started`/`run.finished`/`approval.*`、transient 标记、`run.finished`→`[DONE]` 唯一终止、`finish`/`abort`/`error` 编码名退役、无 `message.updated`），两侧（主/子）同一词汇断言
- [x] 5.2 `delivery/sse.py` 终态统一编码：RunCompleted/RunAborted/RunError → `run.finished(status, finish_reason, usage, model_calls)`；`finish`/`abort`/`error` 编码名退役；RunPaused(hitl_pending) 维持 run-status 非终态形态
- [x] 5.3 executor `_consume` 转发全部 bridge 帧（`text-delta`/`reasoning-delta`/`stats-update` 标 transient，其余 durable），`message.updated` 全量投影事件与五类事件序号豁免白名单退役
- [x] 5.4 前端 `useSSEStream` 终态判定挂 `run.finished`（原 `finish`/`abort`/`error` 分支迁移，abort 静默丢弃洞消灭）；`useSSEStream.test.ts` 终态用例同步
- [x] 5.5 前端子会话 `consumeStream` 换统一词汇 + `useRunStreamClient` 内核：`runEventReducer` 收窄（run 生命周期/统计/快照游标，删 `assistantContent`）、流式投影改用 `messageParts` appenders（`appendStreamingDelta` 退役）、断流恢复=快照 replace + durable 重放、重试耗尽展示可见失败/重连入口（消灭静默卡死）
- [x] 5.6 `runEventReducer.test.ts` 扩展帧词汇用例；`useSSEStream.test.ts` 补 transient 不触发 gap 用例
- [x] 5.7 阶段验收：`backend` `uv run pytest tests/ -q` 全绿 + `frontend` `pnpm lint` / `pnpm build` / vitest 全绿 + 手动验证清单（子会话流式/停止/审批/追问/断网重连，主聊天全矩阵回归）

## 6. 阶段 4：宿主壳共享组件（组件粒度分批）

- [x] 6.1 `RunMetaLine`（run-meta 行 + 折叠状态机 + 耗时文本），主/子两视图换用
- [x] 6.2 `AssistantToolFailureBlocker`（两份逐字符相同的 markup+样式合一）
- [x] 6.3 `SessionStatsLine`（统计条壳合一；计算层已共享）
- [x] 6.4 `StopSendButton`（单按钮状态机+光环样式合一；乐观/等往返经 prop 策略传入）
- [x] 6.5 子会话 composer 复用 `ChatComposerToolbar` 容器：新增 `showToolsMenu` 可选 prop（默认 true，子会话收窄为纯模型/档位工具栏），子视图经 `#right` 槽组合上下文环与单按钮，手写同构工具栏与三块布局样式删除
- [x] 6.6 `HitlComposerPanel` 吸收 `HitlApprovalCard`：新增 `sessionGrantPolicy` prop（auto=主聊天网络类 execute / always=子会话），子视图换面板（decideHitl 适配 decisions 列-table 载荷），`HitlApprovalCard` 删除；`TaskCatalogPanel` 内联「批准/拒绝」为任务卡列表操作行（载荷同决策词汇），非审批卡重复
- [x] 6.7 来源面板合一：`ResearchSourcesPanel` 增 `results` 扁平模式（canonical key 去重、首见序编号、无引用分组，复用既有「检索来源」折叠组），子视图换面板，`CitationSources` 删除
- [x] 6.8 `ConversationPartsRenderer` 导出 ParallelToolsGroup，SubagentCollapse 时间线复用（消灭第三份并行组渲染与样式）
- [x] 6.9 `useTicker` composable 收敛三份 setInterval 秒级时钟
- [x] 6.10 组件迁移后删除两侧被替换的同构实现与样式；`pnpm lint` / `pnpm build` + 每组件手动验证清单

## 7. 阶段 5：文档与规格同步

- [x] 7.1 重写 `docs/architecture/platform/chat-streaming.md` §4.2b 事件清单：统一词汇、durable/transient 两类、`run.finished` 唯一终止、子 Agent 流并入；§4.2a 信令说明核对
- [x] 7.2 `docs/architecture/subagent-sessions.md` 同步投递/投影/事件章节；`docs/architecture/` 其余引用旧词汇（`message.updated` 等）处排查更新
- [x] 7.3 契约测试固化最终事件词汇（阶段 5.1 的先行测试转为长期 gate）；`python3 scripts/verify-md-links.py` 与 `verify-decision-format.py` 通过
- [x] 7.4 决策记录：`docs/decisions/` 新增本变更记录（含被否方案：子 run 并入 RunManager / 保留双方言只统一传输 / 主聊天迁 run 级投影词汇，及 4.5 双装配审计结论），核实为 implemented 口径
