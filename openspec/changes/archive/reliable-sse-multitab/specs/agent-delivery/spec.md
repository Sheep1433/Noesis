## ADDED Requirements

### Requirement: Run 状态写入 SHALL 保证 sequence 与 projection 原子一致

系统 SHALL 使每个 Run 的 sequence 分配、projection reduce、status 变更、replay buffer 写入和 snapshot 复制通过同一 RunHandle 串行写入边界完成。一个 `snapshot_sequence=N` 的 snapshot SHALL 只包含已经 apply 到 N 的 projection，SHALL NOT 包含 N+1 或更新事件的结果。

#### Scenario: apply 与 subscribe 并发

- **WHEN** 事件 N 正在 apply 时另一个 subscriber 加入
- **THEN** 该 subscriber SHALL 在 snapshot 或后续 tail 中有效观察到事件 N 一次
- **AND** SHALL NOT 丢失或重复 apply 事件 N

#### Scenario: snapshot revision 与内容一致

- **WHEN** 系统产生 `snapshot_sequence=N`
- **THEN** snapshot parts/status SHALL 精确反映 sequence N 之后的 projection
- **AND** SHALL NOT 预先包含 sequence N+1 的文本、tool、HITL 或终态变更

### Requirement: Runtime raw event SHALL 只经过一条 typed 主路径

对本 change 适用的 Agent Run，Runtime raw event SHALL 由唯一 `RuntimeEventMapper` 映射为 typed `RunEvent`，再交给 RunHandle 和 Delivery。SSE 字符串 SHALL 只在 SseDelivery 边界编码；系统 SHALL NOT 保留 `raw event → SSE string → parser → RunEvent` 内部往返、Web 专用重复 EventBus 或与 RunHandle 重复的 active registry。

#### Scenario: Web 事件不再内部 SSE 往返

- **WHEN** LangGraph 产生文本、reasoning、tool、usage、context 或 HITL raw event
- **THEN** 系统 SHALL 在 SSE 编码前得到 typed `RunEvent` 和 sequenced envelope
- **AND** 主路径 SHALL NOT 再解析自己生成的 SSE 字符串

#### Scenario: 未知 raw event 不污染投影

- **WHEN** RuntimeEventMapper 收到未支持的 raw event
- **THEN** 系统 SHALL 记录可定位日志或指标并丢弃该事件
- **AND** SHALL NOT 为该事件分配业务 sequence 或修改 projection

### Requirement: PersistWriter SHALL 使用 immutable checkpoint 与 latest-wins 合并

PersistWriter SHALL 只消费已与 `snapshot_sequence` 绑定的 immutable snapshot，SHALL NOT 在异步写入时重新读取可变 projection。同一 Run 可合并的待写 checkpoint SHALL 最多保留 sequence 最大的一个；迟到的旧 checkpoint SHALL NOT 覆盖数据库中更新的 snapshot。

#### Scenario: 高频文本 checkpoint 合并

- **WHEN** PersistWriter 尚未写完 checkpoint N 时又收到 N+1 至 N+20 的可合并 snapshot
- **THEN** writer SHALL 允许合并未写入的中间 snapshot
- **AND** 最终待写 snapshot SHALL 至少为已收到的最大 sequence

#### Scenario: 迟到 checkpoint 不回退数据库

- **WHEN** 数据库已存储 sequence 50，随后收到 sequence 45 的迟到 checkpoint
- **THEN** repository SHALL 忽略该写入并记录 metric
- **AND** `last_sequence` 与 snapshot SHALL 保持 sequence 50

#### Scenario: semantic checkpoint 优先唤醒

- **WHEN** Run 进入 tool completed 或 HITL pending 等语义边界
- **THEN** PersistWriter SHALL 立即尝试写入当前最新 snapshot
- **AND** SHALL NOT 等待普通文本节流周期结束

### Requirement: 权威 terminal SHALL 先持久化再投递

completed、partial、error 或 interrupted terminal SHALL 使用 Run 与 assistant 同事务 compare-and-set，并同时写入 final snapshot 与 `last_sequence`。只有事务成功后，系统才 SHALL 更新 live projection、fan-out terminal event 并发送 `[DONE]`。

#### Scenario: terminal 事务成功

- **WHEN** Run 产生 completed terminal intent 且 PostgreSQL transaction 成功
- **THEN** Run row 与 assistant row SHALL 先存储相同终态内容与 sequence
- **AND** 在线 Delivery 随后 SHALL 收到权威 terminal 与 `[DONE]`

#### Scenario: terminal CAS 未赢

- **WHEN** terminal compare-and-set 失败，因为其它合法路径已终态化该 Run
- **THEN** 当前路径 SHALL 读取并采用数据库权威 terminal snapshot
- **AND** SHALL NOT 发布当前路径准备的冲突 terminal event

#### Scenario: terminal 持久化持续失败

- **WHEN** terminal transaction 在配置的同步 budget 内始终失败
- **THEN** 系统 SHALL 停止 producer 并保留最多一个 immutable terminal candidate 低频重试
- **AND** SHALL NOT 对任何 Delivery 发布 completed、partial、error 或 interrupted 伪终态

### Requirement: Delivery 背压 SHALL 隔离慢消费者与持久化

每个 SSE/Channel subscriber SHALL 使用独立、同时限制事件数和 bytes 的 queue。单个 subscriber 溢出 SHALL 只注销该 subscriber 并触发其自身恢复，SHALL NOT 阻塞 RunHandle publisher、PersistWriter 或其它 subscriber。Persistence SHALL 使用上一要求的合并语义，SHALL NOT 被当作可注销的普通 subscriber。

#### Scenario: 单个慢 Tab 溢出

- **WHEN** Tab B 的 queue 达到事件数或 bytes 上限
- **THEN** 系统 SHALL 移除 Tab B 的 subscription 并记录 overflow
- **AND** Tab A、PersistWriter 与 producer SHALL 继续处理后续事件

#### Scenario: subscription 配额超限

- **WHEN** 新 stream 请求超过单 Run、单用户或全局 subscription 上限
- **THEN** 系统 SHALL 在建立 SSE 响应前返回 HTTP 429 和 `SSE_SUBSCRIPTION_LIMIT`
- **AND** 已存在的 subscription 与 producer SHALL 不受影响

### Requirement: 迟到 producer 与 model attempt 事件 SHALL 被拒绝

每次初始 producer 或 HITL resume producer 启动前，RunHandle SHALL 产生新的进程内 producer generation；每次新模型尝试 SHALL 使用递增 `attempt_id`。generation 或 attempt 不匹配的迟到事件 SHALL 在 projection reduce 之前被拒绝，SHALL NOT 获得 sequence、进入 replay buffer 或写入 snapshot。producer generation SHALL NOT 进入对外 SSE payload 或持久化 snapshot。

#### Scenario: HITL resume 后旧 producer 迟到

- **WHEN** HITL resume 已启动新 producer generation，暂停前的旧 task 随后发出 text delta
- **THEN** 系统 SHALL 丢弃该 delta 并增加 stale generation 指标
- **AND** `last_sequence` 与 snapshot SHALL 不变

#### Scenario: model retry 后旧 attempt 迟到

- **WHEN** Run 已进入新 `attempt_id`，旧 attempt 随后发出 reasoning 或 text delta
- **THEN** 系统 SHALL 丢弃该事件并增加 stale attempt 指标
- **AND** 当前 assistant SHALL NOT 出现重复内容
