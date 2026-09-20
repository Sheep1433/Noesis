# 设计：后台任务追加消息命令化

对照实物：`services/run_command_service.py`（提交/等待/消费者）、`repositories/agent_run_command_repository.py`（幂等键 / claim_pending）、`services/subagent_session_service.py`（pending 行与投影）、`agents/background/executor.py`（deliver_message）、`chat/runs/bus.py`（WAKEUP_TOPIC_RUN_COMMAND）。前序：`bg-task-durable-facts`（pending 行为队列事实）。

## 结论

追加消息的受理与执行分离：**受理（accept）任意实例可用——一个数据库事务里写 pending 行 + 插 `bg_task_deliver` 命令行；执行（consume）只在 leader 的命令消费者**。命令不携带消息内容，仅携带引用 `(child_session_id, message_id)`——pending 行就是队列事实与命令载荷的合一。模型工具与用户 HTTP 同走命令表（单一路径），wakeup 即时唤醒使单实例无可感知延迟。

与前期口头设计的一个修正：**不引入 LeadershipPort**。原设想受理端需要感知「我是不是 leader」来决定直通还是命令化；核对消费者实现后发现两点使其多余——消费者的 `claim_pending` 前置 token 有效性校验（follower 天然不认领），且 wakeup 订阅让命令近实时到达（单实例亦无感知延迟）。受理端因此完全不需要知道自己的角色，删除了这个判据也就删除了「换主后旧 leader 误判」整类风险。

## 受理（accept）——任意实例

`SubagentSessionService.send_message` 的受理序列：

1. 归属校验（`_owned_child`，DB 查询，任意实例可答）。
2. 快速失败校验（咨询性）：DB 投影读任务状态，不存在 / kind 不支持 / 已不可续终态 → 直接 404/409，不写任何东西。
3. 容量执法：该 child session 的 pending 行计数 ≥ 上限 → 409（落库前拒绝）。
4. **同一事务**：写 pending user message 行（turn 参数随行）+ 插 `bg_task_deliver` 命令行（dedupe_key = `bg:{task_id}:deliver:{message_id}`，payload 仅含引用）+ 提交。两行同生共死，孤儿 pending 行在结构上不可能。
5. wakeup（fire-and-forget，失败由消费者补扫兜底）。
6. `submit_and_wait`（沿用命令既有 5s 预算），三个出口：
   - 命令 `completed` → 读 DB 投影返回（冷恢复场景投影已含新 run_id）；
   - 命令 `rejected` → 409 + 拒绝原因（来自命令 result_summary），响应对齐写操作错误契约；
   - 超时（仍 pending/claimed）→ 返回受理时投影 + `command_status: "accepted"` 字段，前端由既有轮询与事件流兜底。
   响应体 SHALL 携带 `command_status` 字段（completed / accepted / rejected 由 HTTP 层映射），前端据此区分「已生效（含新 run_id）」与「排队中」。no_op（幂等重放）按 completed 同路径读投影返回。

受理时的状态校验是**咨询性**的：受理与消费之间任务可能转变（completed → 用户停止为 cancelled、running → 对账 error）。权威裁决在消费端。

## 消费（consume）——仅 leader

`RunCommandConsumer._dispatch` 注册 `bg_task_deliver` 分支，按目标任务状态四分派：

1. **行已非 pending**（已采纳 / 已翻转 dropped）→ `no_op`（幂等：failover 重放、重复提交空转）。
2. **任务非终态但热集 miss**（换主窗口：对账尚未收口/重建该任务）→ `no_op` **延后语义**——不翻转行、不标 rejected，留给对账收口后的状态裁决（running 收口 error 后行翻转 dropped；queued 重建后随任务保留）。SHALL NOT 在此分支把行翻转 dropped——那会丢掉排队任务重启后应继续消费的指示。
3. **任务可续 / 活跃**：调 `executor.deliver_message`（consume 语义）——入执行镜像队列（running/queued）或冷恢复开新 turn（completed/cancelled）。
4. **任务不可续**（failed/timed_out/error）→ **翻转 pending 行 dropped**（消费端权威）+ 命令 `rejected` + 摘要注明原因。

`deliver_message` 收敛为纯消费路径：删除「user_message_id 为空则写行」分支；幂等校验从「行状态」扩展为「行状态 + 镜像队列查重」（按 message_id 在 `entry.pending_messages` 查重）——重放窗口内「已入镜像队列、行仍 pending」的中间态不得重复入队。

### 已取消任务的被动消费分流（停止不被覆写）

cancelled 是可续终态，但**用户停止后遗留的排队消息不得被命令消费反向复活**——否则「任务已停止」被一条早前受理的消息静默覆写（settle 既有禁区：停止被覆写丢失，甚至反向新开 run）。消费对 cancelled 分流：**pending 行的创建时间早于任务取消时间** → 命令 `no_op`（摘要注明「任务已停止，待续聊触发」），行保留 pending（意图保留，待下次续聊触发时一并消费）；**创建时间晚于取消** → 用户对已停任务的主动追问，正常冷恢复复活。completed 无此问题（自然终态，排队消息应执行），保持现状。

### 认领租约：claimed 命令的回收

`claim_pending` 是持久事实（status 固化为 claimed），leader 在 claim 与 mark_terminal 之间崩溃会让命令永久卡 claimed——补扫只扫 pending，行将永久滞留。补两组回收：

- **租约重置**：`claimed_at` 超过租约时长（沿用命令补扫周期，数个 scan_interval 量级）的 claimed 行由补扫重置回 pending，重新认领；
- **晋升对账兜底**：`_on_promotion` 对账段把全部 claimed 行重置回 pending（旧 leader 已死，其认领必然未完成）。

幂等由 consume 的行状态校验 + 镜像查重承担，重置安全。

## 关键判断

### 单一路径消灭双轨

模型工具（leader agent loop 内）与 HTTP 同走命令表。表面上 leader 给自己发命令是绕路，实际收益是：受理代码只有一份（不存在「leader 走 A 路径、follower 走 B 路径」的漂移面），消费代码只有一份（executor 只暴露 consume 语义），且命令表的幂等键让重试天然安全。wakeup 订阅使唤醒近实时，5s 等待预算内 leader 几乎必然完成——单实例无可感知延迟。被否的「leader 本地直通」会在两处入口各留一份容量/校验/翻转逻辑，正是「历史兼容双轨」的温床。

### 容量执法点在受理，消费不复查；镜像队列取消上限

受理时 DB 计数超限直接 409（快速失败，什么都没写）；消费对已受理行不复查容量——受理过的行必须被消费，否则制造新的滞留。count-then-insert 的并发竞态（至多超限 K-1 条）论证沿用前序变更。**执行镜像 deque SHALL 取消 maxlen**：现有 `deque(maxlen=10)` 满后 append 会静默淘汰最旧条目，而条目对应的 pending 行仍在（命令已 completed）——被淘汰的消息永无消费者也不翻转，恰是本变更要消灭的滞留形态。镜像规模由受理端容量承载，竞态超限量级无害。

### 拒绝的翻转责任在消费端

受理与消费之间任务转为不可续（用户停止为 cancelled 之外的所有终态化：failed/timed_out/对账 error），消费拒绝时**消费端翻转 pending 行 dropped**。受理端不做预清理——受理时它无法知道未来状态。这与前序变更「不可续终态收口即翻转」共同构成完备覆盖：终态转换与消费拒绝两个时机，二选一必然发生。

### 换主装配顺序：消费必须在对账与重建之后

现行 `_on_promotion` 中 `RunCommandConsumer.start()` 位于四段对账之前，消费者首次认领与对账的 DB 操作交错——「对账先于消费」在现行代码不成立。本变更将其**确定性后移**：consumer 启动移到四段对账 + `restore_queued` 完成之后。否则换主窗口内：排队任务的 deliver 命令先被认领 → 热集 miss 抛错 → 误走「拒绝翻转」丢掉排队指示（违反基线「排队任务的指示随任务保留」）。配合 consume 分支 2（非终态 + miss → no_op 延后）构成双保险。

### 命名零残留是一次性清扫，不是渐进迁移

`grep -ri followup backend/packages backend/server backend/tests frontend/src` 零命中为验收门槛（历史叙述仅存于决策记录与已归档 openspec 变更）。改名清单（全仓 grep -i followup 命中即改，含但不限于）：kernel `_arun_followup` → `_arun_appended_turn`、变量 `next_followup` → `next_pending`、`_TurnParams` docstring；kinds 协议字段 `supports_followup` / `reject_followup_text` → `supports_message_append` / `reject_append_text`；registry 字段 `followup_factory` → `turn_factory`；事件名字符串 `"followup"` → `"message-appended"`（前端无按名消费，已核实安全）；`super_agent.py` 任务名 `subagent-followup-launch` 等；schema `SubagentFollowupRequest` → `SubagentMessageRequest`；`chat/runs/skeleton.py` 过期注释；`services/bg_continuation_service.py`、`llm/runtime_snapshot.py` 注释。前端：目录 `FollowupQueue/` → `MessageQueue/`、`hooks/useFollowupQueue.ts` → `hooks/useMessageQueue.ts`（导出 `useMessageQueue`）、`queuedFollowups.ts` → `queuedMessages.ts`（导出 `setQueuedMessages` / `clearQueuedMessages` / `getQueuedMessages`）、变量 `followupInput` / `followupSending` → `messageInput` / `messageSending`、`sendFollowup()` → `sendAppendedMessage()`、组件内 CSS 类与 testid 字符串 `followup-queue*` → `message-queue*`；测试文件 `test_bg_subagent_executor.py` → `test_background_executor.py`，`frontend/__tests__/childCatalogRealtime.test.ts` 同步改名与断言。已核实**不可改名处为空**：localStorage 键为 `noesis:subagent-queue:` 前缀（不含 followup，存量数据不破坏）、路由已是 `/subagent-messages`、无第三方依赖命中。不保留任何旧名别名或转发层。

## 备选方案

**命令携带消息内容（payload 存 text/params）——否。** 内容双写（pending 行 + 命令 payload）引入「哪份是权威」的问题；行已是队列事实，命令只需引用。引用化的额外收益是幂等校验天然落在行状态上。

**leader 本地直通（工具路径不走命令）——否。** 双入口双逻辑，容量/校验/翻转规则在三处漂移；且换主后旧 leader 的「本地直通」会在非 leader 上执行任务，破坏单 leader 执行不变量。单路径 + wakeup 的延迟代价（毫秒级）可忽略。

**LeadershipPort / 受理端感知领导权——否。** 消费者 token 校验 + wakeup 已使受理端无需知道角色；引入判据反而制造「换主后旧 leader 误判」的风险面。

**Redis pub/sub 直发执行信号（不走命令表）——否。** pub/sub at-most-once，丢失后需要补扫轮询兜底——最终等价于命令表轮询，却多了一条不可靠路径；命令表的幂等键与 FOR UPDATE SKIP LOCKED 认领是现成的正确性保障。

**受理端预清理孤儿行（捕所有异常）——否。** 只治标：follower 仍不可用（500），且「先写行再失败再清理」的窗口内行已对外可见（前端待执行标注闪现）。事务化行+命令让孤儿不可能存在，比事后清理正确。

## 语义消费者说明

| 新增产物 | 本变更内的真实消费者 |
|---|---|
| `bg_task_deliver` 命令类型 | `/subagent-messages`（任意实例受理）与 `send_message` 工具（leader）共用；消费者分支出 execute 派发 |
| accept 事务（行+命令同生共死） | 多实例下追加消息不产生孤儿 pending 行（容量配额不被假消息堵死） |
| 消费端拒绝翻转 dropped | 受理与消费之间任务转不可续的窗口：pending 行有明确结局，投影计数可见 |
| 超时降级（受理投影 + accepted） | 命令等待窗口外的冷恢复：前端由既有轮询与事件流兜底，SHALL NOT 500 |
| 命名零残留门槛 | 全仓 followup 命名退出；后续维护者单一词汇（追加消息） |

## 风险与边界

- **命令表成为追加消息的必经点**：命令表不可用 = 追加消息不可用（PG 同库同事务，无新增可用性边界）；claimed 卡死由认领租约 + 晋升对账重置收口（见消费节）；命令保留期（7 天）即去重窗口，超窗重复提交理论上可重复消费——由 pending 行状态 + 镜像查重兜底（行已消费则 no_op）。
- **等待窗口的语义降级**：5s 内未完成（换主瞬间、leader 繁忙）返回受理投影，前端该次交互拿不到即时新 run_id——drawer 重开时经 active-run 发现恢复，属可感知但可恢复的降级。
- **执行面边界不变**：任务执行仍单 leader；本变更只补全写面路由，不扩展执行吞吐。
- **滚动升级窗口**：旧实例的命令消费者不认识 `bg_task_deliver`，会把窗口期内的命令标 rejected（消息行保留 pending，由对账/人工收口）——升级窗口短暂且合并后重启即恢复；新实例全部就位后自动消失。
