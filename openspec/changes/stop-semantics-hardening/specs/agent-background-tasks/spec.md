# agent-background-tasks Delta

## ADDED Requirements

### Requirement: 主 run 用户停止与任务的级联

主 run 被用户停止时，级联范围按任务执行模式分流：前台等待中的 subagent 任务（`run_in_background=false` 且启动工具协程仍在等待其终态）SHALL 级联取消——取消传播至等待协程时同步受理任务停止（乐观落 cancelled，走既有协作退出、宽限硬杀与部分产出回收路径），随后取消继续向上传播；前台等待超时转后台 SHALL 维持既有不取消语义。后台任务（`run_in_background=true` 的 subagent 任务与后台命令任务）归属会话而非主 run，用户停止主 run SHALL NOT 影响它们，其停止仅经任务目录 / `cancel_async_task` / 停止 API 单独发起。级联取消的受理失败 SHALL NOT 阻塞主 run 停止传播。

#### Scenario: 前台等待任务随主 run 级联取消
- **WHEN** 主 run 在 `start_async_task` 前台等待期间被用户停止
- **THEN** 被等待的 subagent 任务 SHALL 被受理为 cancelled 终态
- **AND** 主 run SHALL 照常落 partial 终态并返回终态快照

#### Scenario: 前台超时转后台不级联
- **WHEN** 前台等待超过阈值自动转后台后，主 run 被用户停止
- **THEN** 已转后台的任务 SHALL NOT 因主 run 停止被取消

#### Scenario: 后台任务不随主 run 停止
- **WHEN** 主 run 被用户停止且该会话存在 `run_in_background=true` 的运行中任务或后台命令
- **THEN** 这些任务 SHALL 继续运行，状态与进度不受影响

### Requirement: 手动停止压制自动续跑

用户停止主 run SHALL 置该会话的停止标记：未触发的去抖唤醒定时器 SHALL 被取消，标记有效期内任务终态 SHALL NOT 触发 `auto_continue` 创建 continuation run。标记为内存态，会话的下一条用户真实消息 SHALL 清除标记并恢复既有自动续跑资格。

#### Scenario: 停止后任务终态不自动续跑
- **WHEN** 用户停止主 run 后，该会话一个后台任务到达 completed 终态
- **THEN** 系统 SHALL NOT 创建 continuation run
- **AND** 终态通知 SHALL 保持入队，随用户下一次消息或手动查看送达

#### Scenario: 用户消息解除压制
- **WHEN** 用户停止后再次在会话中发送消息，随后又有任务到达终态
- **THEN** auto_continue SHALL 恢复既有去抖唤醒行为
