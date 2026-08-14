## MODIFIED Requirements

### Requirement: 流式问答入口 SHALL 经 Run Fan-out 投递

`POST /api/chat/runs` SHALL 在任意Web worker事务性创建queued Run与消息骨架并快速返回，由唯一execution leader异步claim并创建RunManager producer。独立SSE端点 SHALL 通过leader本地RunEvent或Redis跨进程RunEvent输出。问答编排 SHALL NOT 在HTTP generator内拥有producer生命周期，非leaderworker SHALL NOT 启动producer。

旧 `POST /api/chat/sessions/stream` SHALL 被删除，问答编排 SHALL NOT 保留第二条发送路径。

#### Scenario: follower创建新Run
- **WHEN** 已认证用户通过非leaderworker调用 `/api/chat/runs`
- **THEN** 服务端 SHALL 在Run和消息骨架落库后返回Run身份
- **AND** execution leader SHALL 异步启动producer，请求worker SHALL NOT 等待Agent完成

#### Scenario: 非leader接受订阅
- **WHEN** 已鉴权SSE请求落到不持有producer的worker
- **THEN** 该worker SHALL 使用subscribe-first握手从PostgreSQL snapshot和Redis RunEvent恢复
- **AND** SHALL NOT 返回伪终态或启动第二producer

#### Scenario: 旧入口不可用
- **WHEN** 客户端请求 `/api/chat/sessions/stream`
- **THEN** 系统 SHALL 返回404或路由不存在的等价结果
- **AND** SHALL NOT 通过隐藏包装创建Run

### Requirement: 停止生成 SHALL 走统一 Run 生命周期

停止接口 SHALL 先持久化已鉴权且幂等的Run command，再由execution leader调用统一RunManager/cancel入口，使Persistence与所有本地或跨进程Delivery观察到一致中止语义。系统 SHALL NOT 使用session全局布尔量或让请求worker直接操作其它进程内存。

#### Scenario: follower接收停止请求
- **WHEN** 用户通过follower对所属active Run调用停止接口
- **THEN** command SHALL 持久化并由leader至多执行一次
- **AND** assistant SHALL 进入partial，所有在线Delivery SHALL 收到一致终态

### Requirement: HITL 分段流 SHALL 经同一 Fan-out

`hitl-required` / `finish_reason=hitl_pending` 与 `POST .../hitl/resume` 启动的新segment SHALL 经同一RuntimeEventMapper → leader RunHandle → 本地/Redis RunEvent → SseDelivery/PersistWriter路径。resume command SHALL 先持久化并由leader幂等执行，继续更新同一 `assistant_message_id`；系统 SHALL NOT 在请求worker另起producer或落库分支。

#### Scenario: follower接收HITL resume
- **WHEN** 用户通过follower对pending HITL调用resume
- **THEN** command SHALL 按同一Run与interrupt identity持久化并由leader执行
- **AND** 客户端 SHALL 重新订阅同一Run，PersistWriter SHALL 更新同一assistant直至终态

### Requirement: 多 Tab SHALL 独立订阅同一 Run

同一用户多个Tab SHALL 使用独立SSE subscription，且 MAY 位于不同Web worker。断开、刷新、Redis重连或任一subscription溢出 SHALL 只移除或恢复自身，不取消leader producer、Persistence或其它Delivery；所有worker SHALL 以同一PostgreSQL snapshot、sequence和终态为准。

#### Scenario: 关闭创建Run的Tab
- **WHEN** Tab A与Tab B位于相同或不同worker并订阅同一Run，随后关闭Tab A
- **THEN** producer SHALL 继续，Tab B SHALL 收到权威终态

#### Scenario: Tab重连到另一worker
- **WHEN** Tab重连后被路由到与此前不同的follower
- **THEN** 新worker SHALL 先订阅缓冲、再读取snapshot并按sequence合并
- **AND** 客户端 SHALL NOT 重复或永久遗漏正文、工具块和终态

### Requirement: stop 与 HITL resume SHALL 按 Run 鉴权且幂等

stop与HITL resume SHALL 按 `(run_id,current_user_id)` 鉴权并验证session/assistant关联。请求worker SHALL 使用持久化command identity与幂等键交给execution leader；重复stop SHALL 最多取消一次producer并产生一个terminal transaction，重复或过期HITL SHALL NOT 启动第二producer。旧Run或旧command SHALL NOT 作用于同session后续Run。

API数据 SHALL 区分 `command_status=accepted` 与 `completed`。accepted时chat页 SHALL 保留当前内容并继续订阅同一Run，展示进行中状态；只有权威Run snapshot/SSE发生状态变化后才能显示已经停止或已经继续。

#### Scenario: 旧Tab不能停止新Run
- **WHEN** 旧Tab对已终态R1发stop，而同session已有新Run R2
- **THEN** R2 SHALL 不受影响

#### Scenario: command请求重试
- **WHEN** 请求worker未及时收到leader ack并使用同一幂等键重试
- **THEN** 系统 SHALL 返回或等待同一command结果
- **AND** SHALL NOT 重复取消、审批或启动producer segment
