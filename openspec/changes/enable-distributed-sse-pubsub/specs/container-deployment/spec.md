## MODIFIED Requirements

### Requirement: 容器化运行环境与配置注入

Docker（或Docker Compose）部署 SHALL 通过环境变量或env文件提供PostgreSQL业务库、LangGraph checkpoint、Qdrant、必填 `NOESIS_RUN_BUS_BACKEND` 和模型API连接信息；redis模式额外必填 `REDIS_URL` 与 `NOESIS_CLUSTER_ID`，键名 SHALL 与 `config/env.py` 对应。Compose的分布式模板 SHALL 为PostgreSQL和Redis配置健康检查，并使backend在依赖就绪后启动；memory模板 SHALL 明确限制为一个backend。持久业务状态 SHALL 继续由PostgreSQL保存。镜像与版本控制文件 SHALL NOT 包含真实生产密钥，也不得提供强制leader角色的开关。

#### Scenario: 启动多worker依赖
- **WHEN** 运维配置PostgreSQL、checkpoint、Qdrant和Redis连接及凭证
- **THEN** 多个backend进程 SHALL 在依赖健康后启动并连接同一协调服务
- **AND** 仓库内 SHALL 无明文生产密钥

### Requirement: 可观测的健康检查

backend SHALL 暴露liveness与readiness语义及实际 `run_bus_backend`。进程存活时liveness SHALL 成功；PostgreSQL或HTTP核心依赖不可用时readiness SHALL 返回503。redis模式下Redis不可用时ready端点 SHALL 保持可路由并报告degraded，由创建Run接口单独返回503；redis模式的leader与follower均可ready，未持leader lock SHALL NOT 构成不健康。memory模式 SHALL 报告不支持多backend，且不探测Redis健康。

#### Scenario: follower健康探测
- **WHEN** backend未持execution leader lock但PostgreSQL、Redis和Web依赖正常
- **THEN** readiness SHALL 表示可接收Web/API/SSE流量

#### Scenario: Redis模式下Redis不可用
- **WHEN** backend配置redis模式且无法连接Redis分布式实时通道
- **THEN** readiness SHALL 保持可路由并报告degraded，创建新Run SHALL 返回503
- **AND** liveness SHALL 继续表达进程仍存活
- **AND** 已有Run的查询、snapshot恢复、stop与HITL command SHALL 继续可用

### Requirement: Run recovery SHALL 在单实例所有权确立后执行

Run recovery与Agent producer SHALL 只在execution leader取得advisory lock后启动。新leader SHALL 先收口旧leader已claim的running/retrying/HITL Run，再dispatch未被claim的queued Run；follower SHALL NOT 扫描收口或执行Run。scheduler、memory dream和messaging runtime SHALL 与leader角色使用同一生命周期边界，避免多进程重复执行。

#### Scenario: follower不执行后台任务
- **WHEN** backend未取得execution leader lock
- **THEN** recovery、producer、scheduler、memory dream和messaging runtime SHALL NOT 启动
- **AND** Web/API/SSE路由 SHALL 继续可用

### Requirement: 单 worker 容量 SHALL 可观测且通过负载验收

系统 SHALL 在一个execution leader和至少一个follower下观测active Run/subscription、leader dispatch、event-loop lag、本地/跨进程event-to-client latency、Redis publish/subscribe、handshake buffer、sequence gap、checkpoint latency/lag、command与terminal retention。发布前 SHALL 以100 active Run、每Run 2–3 Tab、每Run 10–30 events/s，混入follower路由、慢消费、Redis重连与leader切换，记录p50/p95/p99、RSS、Redis吞吐、queue bytes、checkpoint lag和回收结果。

#### Scenario: 多worker容量验收
- **WHEN** 发布候选执行规定负载且部分subscriber固定连接follower
- **THEN** 报告 SHALL 保留leader/follower延迟、event-loop lag、RSS、Redis吞吐/失败、queue bytes、checkpoint lag、gap恢复、command、terminal delivery和资源回收数据

## REMOVED Requirements

### Requirement: P0 backend SHALL 使用 PostgreSQL advisory lock 保证单 active 实例

**Reason**: advisory lock继续保证单execution leader，但未持lock的backend不再fail-fast，而是作为Web/API/SSE follower服务。

**Migration**: 将lifespan启动门禁改为leader角色选举；只有leader启动producer和singleton runtime，followers继续进入ready。
