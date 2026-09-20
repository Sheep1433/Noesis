# 任务：后台任务追加消息命令化

## 1. 受理/消费拆分

- [x] 1.1 `RunCommandService.submit_deliver`：校验任务存在与 kind 支持（DB 投影，咨询性）、容量计数（超限 409 不写任何东西）、**同一事务**写 pending user message 行 + 插 `bg_task_deliver` 命令（dedupe_key = `bg:{task_id}:deliver:{message_id}`，payload 仅 `(child_session_id, message_id)`）、wakeup。注：`AgentRunCommandRepository.submit` 内嵌 commit，需拆出不自带提交的写入方法（或 flush 语义）以支撑跨表同事务——不得用两次 commit 伪装。
- [x] 1.2 `send_message` 重构为 accept：调用 `submit_deliver` + `submit_and_wait`；三出口——completed → 读 DB 投影返回（冷恢复含新 run_id）；rejected → 409 + 拒绝原因（result_summary）；超时 → 返回受理时投影 + `command_status: "accepted"`。删除现有「先写行再本地调用 deliver_message」路径与 ValueError 清理分支（孤儿已结构上不可能）。
- [x] 1.3 `RunCommandConsumer._dispatch` 新增 `bg_task_deliver` 分支：读 pending 行（非 pending → no_op）；任务非终态但热集 miss → no_op 延后（不翻转行）；cancelled 按 pending 行创建时间与取消时间分流（受理先于取消 → no_op 保留行；晚于取消 → 冷恢复）；不可续 → 翻转行 dropped + 标 rejected（摘要注明原因，构造可诊断文案而非笼统 ValueError 兜底）；其余 → `executor.deliver_message`（consume 语义）→ 成功标 completed。
- [x] 1.4 `deliver_message` 收敛为纯消费路径：删除「user_message_id 为空则写行」分支；幂等校验 =「行仍 pending」+ 镜像队列按 message_id 查重（重放窗口不重复入队）；非可续终态拒绝时翻转行 dropped。
- [x] 1.5 工具路径命令化：`asend_message` 改走 `submit_deliver` + `submit_and_wait`（返回体适配 `_command_with_identity` 所需任务快照；等待窗口外降级返回受理投影），删除对 executor.deliver_message 的本地直通——单一路径落地。
- [x] 1.6 认领租约：claimed 命令 `claimed_at` 超租约（数个 scan_interval）由补扫重置回 pending；`_on_promotion` 对账段重置全部遗留 claimed 行（旧 leader 认领必然未完成）。
- [x] 1.7 执行镜像 deque 取消 maxlen（容量由受理端承载；竞态超限条目全部保留消费），消费端镜像查重按 message_id。

## 2. 命令基础设施复用与换主时序

- [x] 2.1 wakeup 链路复用（`WAKEUP_TOPIC_RUN_COMMAND` + 消费者订阅）：`bg_task_deliver` 命令唤醒即时性验证；补扫兜底由既有 `_loop` 承担。
- [x] 2.2 `_on_promotion` 装配顺序确定性后移：`consumer.start()` 移到四段对账 + `restore_queued` 完成之后（对账先于消费，换主窗口排队任务的指示不被误翻转）。
- [x] 2.3 换主时序集成验证：对账（收口/重建/翻转）→ 消费（空转延后/拒绝翻转）→ 重建后按序消费的全链路断言。

## 3. 命名零残留清扫（grep 门槛）

- [x] 3.1 kernel：`_arun_followup` → `_arun_appended_turn`；变量 `next_followup` → `next_pending`；`_TurnParams` 及全部注释/docstring 的 followup 表述 → 追加消息。
- [x] 3.2 其余后端文件清扫（全仓 grep 命中即改）：executor/settle/ports/registry/kinds（`supports_followup`/`reject_followup_text` 协议字段）/shell/tools/task_state，及 super_agent.py（含任务名字符串）、schemas/chat_vo.py（`SubagentFollowupRequest` → `SubagentMessageRequest`）、chat/runs/skeleton.py（过期注释）、bg_continuation_service.py、llm/runtime_snapshot.py；registry `followup_factory` 字段 → `turn_factory`；事件名字符串 `"followup"` → `"message-appended"`。
- [x] 3.3 前端：`components/FollowupQueue/` → `components/MessageQueue/`（含 CSS 类与 testid 字符串 `followup-queue*` → `message-queue*`）；`hooks/useFollowupQueue.ts` → `hooks/useMessageQueue.ts`（导出 `useMessageQueue`）；`queuedFollowups.ts` → `queuedMessages.ts`（导出 `setQueuedMessages` / `clearQueuedMessages` / `getQueuedMessages`）；`followupInput` / `followupSending` / `sendFollowup()` → `messageInput` / `messageSending` / `sendAppendedMessage()`；全部引用点同步（含 `chat.vue`）。
- [x] 3.4 测试：`test_bg_subagent_executor.py` → `test_background_executor.py`；测试内 followup 字样清扫。
- [x] 3.5 验收门槛：`grep -ri followup backend/packages backend/server backend/tests frontend/src frontend/__tests__` 零命中；无旧名别名/转发层。

## 4. 测试

- [x] 4.1 单元：受理事务（行+命令同生共死，命令失败行回滚）；容量受理拒绝（不写任何东西）；消费幂等（已采纳/已 dropped/已在镜像队列 三种空转）；消费拒绝翻转 dropped；镜像队列无上限（竞态超限条目全保留）。
- [x] 4.2 单元：等待三出口（completed 读投影 / rejected 409+原因 / 超时 accepted 字段）；dedupe 幂等（同键重复提交返回既有命令）；cancelled 分流（受理先于取消 no_op 保留行；晚于取消冷恢复）；认领租约重置。
- [x] 4.2 单元：等待超时降级（返回受理投影 + accepted 语义）；dedupe 幂等（同键重复提交返回既有命令）。
- [x] 4.3 集成（真实 PG）：follower 视角受理（无执行器实例环境）→ 命令落库 → leader 消费 → 投影可见；换主补扫投递。
- [x] 4.4 工具路径：asend_message 经命令受理的双路径断言（leader 进程内亦经命令表）。
- [x] 4.5 回归：`tests/api_contract` 全绿；`test_run_api_contract` 停止链路、追加消息端点既有用例适配；全量非集成测试无回归。

## 5. 收尾

- [x] 5.1 决策记录（`docs/decisions/implemented/`）：命令即引用、单一路径、无 LeadershipPort、拒绝翻转责任、命名零残留（含被否方案）。
- [x] 5.2 工程文档同步（`docs/engineering/subagent-sessions.md` 追加消息小节补命令化描述）。
- [x] 5.3 前端行为验证（vitest 全绿 + eslint 零错误；渲染行为零变化）。
