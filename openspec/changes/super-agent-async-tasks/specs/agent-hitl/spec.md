## ADDED Requirements

### Requirement: 后台子 Agent 工具审批

后台 task-worker 带 `interrupt_on` 编译；遇审批工具时 LangGraph SHALL 落 checkpoint 并 interrupt，executor SHALL 捕获 `__interrupt__` 将任务转为 `awaiting_approval` 并保留审批载荷（interrupt_id / action_requests）。审批决策 SHALL 经 `POST /bg-tasks/{task_id}/decisions` 提交，executor 用 `Command(resume={"decisions": [...]})` 在同一 thread 续跑——与主 run HITL 的 resume 契约一致。非 awaiting_approval 状态提交决策 SHALL 报错。审批超时（`hitl.ask_timeout_seconds`）SHALL 自动按拒绝续跑。前端 SHALL 在会话任务面板展示待审批卡（批准 / 拒绝），经 5s 轮询触达（不依赖主 run 存活）。

#### Scenario: 后台任务触发审批

- **WHEN** 后台子 Agent 调用需审批工具且任务暂停于 interrupt
- **THEN** 任务状态 SHALL 为 awaiting_approval，审批载荷 SHALL 含该工具调用的 name / args / tool_call_id
- **AND** 主 run（若已结束）不受影响，前端面板经轮询 SHALL 展示审批卡

#### Scenario: 批准续跑

- **WHEN** 用户在面板点击批准
- **THEN** 决策 SHALL 以 `Command(resume={"decisions": [{"type": "approve"}]})` 在同一 thread 续跑
- **AND** 任务回到 running 直至终态

#### Scenario: 拒绝

- **WHEN** 用户拒绝并附说明
- **THEN** 子 Agent SHALL 收到拒绝结果并继续推理（可改道或汇报），任务最终到达终态

#### Scenario: 审批超时

- **WHEN** 任务 awaiting_approval 超过 `hitl.ask_timeout_seconds`
- **THEN** executor SHALL 自动按拒绝续跑（不挂起不失败），任务最终到达终态

#### Scenario: 越权访问

- **WHEN** 用户 A 对用户 B 的后台任务提交决策或查询
- **THEN** SHALL 返回 404 语义（不泄露存在性）
