Delta: agent-background-tasks — followup turn 参数与子会话用量统计

## MODIFIED Requirements

### Requirement: Followup 续话（子会话追加 turn）

`send_message(task_id, message)`（模型侧）与 `POST /api/chat/sessions/{id}/subagent-followup`（用户侧，人 / 模型同路径）SHALL 为同一 child session 追加一条 user message 与一个新的标准 `TAgentRun`，SHALL NOT 以中途注入方式改写当前 turn 的模型输入：

- 任务 running：消息排队（FIFO，上限 10）；当前 turn 结束后 executor SHALL 同 thread 链式开新 turn，队列清空前任务保持 running。
- 任务 awaiting_approval：消息入队，审批 resume 完成本 turn 后由同一条链消费。
- 任务 completed：SHALL 冷恢复——同 thread 追加消息开新 turn，任务回到 running，结束后更新结果。
- 任务 failed / timed_out / cancelled：SHALL 返回错误说明，不可续。
- 每条 followup turn SHALL 支持逐 turn 覆盖执行参数：`model_id`（现有）与 `reasoning_effort`（新增，可选）；用户侧 API 请求体新增可选 `reasoning_effort` 字段，缺省 SHALL 继承任务创建时的档位，旧客户端不传字段时行为不变。turn 参数在排队期间 SHALL 与消息绑定，链式开新 turn 时逐条生效。

#### Scenario: 运行中追加指示

- **WHEN** 任务 running 时投递「聚焦中文源」
- **THEN** 当前 turn 结束后子 Agent SHALL 以该消息为新 turn 接续推理（可多轮工具调用）
- **AND** 新 turn 结束前消息 SHALL NOT 消失或重复

#### Scenario: 完成后继续追问

- **WHEN** 向 completed 任务 send_message 追问
- **THEN** 任务 SHALL 回到 running 并开新 turn，结束后结果 SHALL 更新

#### Scenario: 失败任务拒绝续话

- **WHEN** 向 failed / timed_out / cancelled 任务 send_message
- **THEN** SHALL 返回「任务已结束（原因）」类错误说明

#### Scenario: 逐 turn 切换推理档位

- **WHEN** 用户在子会话抽屉选择「高」档位后发送 followup，任务处于 running
- **THEN** 该消息入队并在成为新 turn 时以「高」档位执行
- **AND** 队列中未指定档位的其他消息 SHALL 按各自绑定参数执行（缺省继承创建时档位）

## ADDED Requirements

### Requirement: 子会话用量统计 SHALL 与主会话同口径

子会话详情 SHALL 基于子会话 assistant 消息 `extra.usage` 重建统计（turns / steps / LLM 耗时 / 输入输出 tokens / 缓存命中），渲染复用主会话统计条的组件与模板配置；统计值 SHALL 与主会话统计条采用同一计算函数。运行中的子会话 SHALL 随终态事件更新统计；历史回放 SHALL 仅凭标准消息接口重建，无需新增专用统计 API。

#### Scenario: 历史回放

- **WHEN** 打开已完成的子会话详情抽屉
- **THEN** 统计条 SHALL 从子会话消息 usage 重建并显示（如「3 轮 · 12 步 | 输入 84K · 输出 2.1K | 缓存命中 79%」）
- **AND** 无额外网络请求

#### Scenario: 流式终态对齐

- **WHEN** 子 Agent run 从 running 到达终态
- **THEN** 详情抽屉统计 SHALL 更新为与终态落库值一致的结果
