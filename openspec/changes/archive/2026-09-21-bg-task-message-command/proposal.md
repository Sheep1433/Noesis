# 变更：后台任务追加消息命令化（多实例写面补全）

> 依赖：`bg-task-durable-facts`（本变更的 spec delta 以其归档后的主规格文本为基线，归档顺序须在其后）

## Why

`bg-task-durable-facts` 落地后，后台任务的查询面任意实例可答、停止走 durable command 任意实例受理，但**追加消息的执行仍隐式绑定 leader 进程**：执行器实例只在 leader 装配，`/subagent-messages` 请求落到 follower 时在取执行器实例处抛 `RuntimeError`（500）。且 `send_message` 的异常清理只捕 `ValueError`——pending 行已写、`RuntimeError` 不清理，**每次失败的追加都留下一行永无消费者的孤儿 pending 行**：占死容量配额，攒满 10 条后该任务的追加通道整体堵死（连 leader 上的正常追加也被拒），前端则永远显示一条「待执行」的消息。

该缺口在 `bg-task-durable-facts` 之前就存在（旧实现为 409「任务不存在」），不是本次回退；但多实例部署下它是用户可见的第一个故障面，长期方案必须在此收口。

## What Changes

- **受理/消费拆分**：追加消息拆为 accept（任意实例：归属与快速失败校验、DB 容量计数、**同一事务**写 pending 行 + 插命令行、wakeup 唤醒、有界等待）与 consume（仅 leader 命令消费者：读行、校验仍 pending、入执行镜像队列或冷恢复开新 turn）。
- **新命令类型 `bg_task_deliver`**：payload 仅携带引用 `(child_session_id, message_id)`，消息内容永驻 pending 行——不双写。
- **单一路径**：模型工具（leader agent loop 内）与用户 HTTP 同走命令表，wakeup 即时唤醒——消灭「leader 本地直通」双轨；消费前置校验由命令消费者的 token 机制天然保证（follower 不认领）。
- **消费拒绝即翻转 dropped + 已取消任务分流**：任务在受理与消费之间转为不可续终态时，消费者翻转 pending 行 dropped 并把命令标 rejected；已取消任务的遗留消息按受理时间分流（先于取消的 no_op 保留意图，不被动复活任务）。
- **认领租约**：claimed 命令超租约由补扫重置回 pending，晋升对账重置遗留 claimed 行——leader 认领后崩溃不再造成命令与 pending 行永久卡死。
- **响应降级**：等待窗口（沿用命令既有 5s 预算）内命令完成则返回含新 run_id 的任务投影；超时返回受理时投影 + accepted 语义，前端由既有轮询/事件兜底。
- **命名零残留清扫**：代码、测试、前端全部 followup 历史命名退出（`_arun_followup`、`FollowupQueue/`、`queuedFollowups.ts`、`followupInput` 等），以 `grep -ri followup` 零命中为验收门槛；不保留任何旧名兼容别名。

不做：执行面不开放多实例（消费是 leader 唯一新增职责，消费者基础设施复用）；命令不携带消息内容；单实例不设命令表旁路（统一单路径）。

## Capabilities

### Modified Capabilities

- `agent-background-tasks`：追加消息受理任意实例可用（命令化）、消费幂等（pending 行校验）、拒绝翻转 dropped、响应降级；追加消息命名一致性（零 followup 残留）。

## Impact

- 后端：`services/run_command_service.py`（`submit_deliver` + consumer dispatch 分支 + 租约重置）、`repositories/agent_run_command_repository.py`（拆出不自带提交的写入方法）、`services/subagent_session_service.py`（`send_message` 拆 accept/事务化）、`agents/background/executor.py`（`deliver_message` 收敛为 consume 路径 + 拒绝翻转 + 镜像无上限）、`agents/background/subagent/kernel.py`（改名）、`schemas/chat_vo.py`（请求模型改名）、`server/main.py`（消费者启动移到对账之后）。
- 前端：`components/FollowupQueue/` → `MessageQueue/`、`queuedFollowups.ts` → `queuedMessages.ts`、变量与调用点清扫（渲染行为零变化）。
- 测试：命令化双视角（leader/follower）、幂等消费、拒绝翻转、换主补扫、容量、命名门槛。
- 行为变更（非破坏）：follower 上的追加消息从 500 变为正常受理；多实例下不再产生孤儿 pending 行；单实例追加消息多一次本地命令往返（wakeup 即时，无可感知延迟）。
