## ADDED Requirements

### Requirement: 用户 SHALL 配置通知偏好
系统 SHALL 允许当前用户按事件类型和可用投递表面配置通知偏好，事件至少覆盖自动化完成/失败、HITL 待处理和通道异常。关闭某类通知 SHALL NOT 停止对应业务执行，只停止该类用户通知。

#### Scenario: 关闭任务成功通知
- **WHEN** 用户关闭自动化成功通知但保留失败通知
- **THEN** 成功 run SHALL 不通知用户而失败 run 仍 SHALL 按可用表面通知

### Requirement: 设置概览 SHALL 聚合平台依赖健康
系统 SHALL 聚合模型 Provider、MCP、Scheduler、通道、业务数据库、checkpoint、Qdrant 与 Sandbox 的 `healthy|degraded|unavailable|unknown` 状态、检查时间、用户可理解摘要和可选行动码。各检查 SHALL 独立超时；单项失败 SHALL NOT 使整个端点失败。

#### Scenario: Qdrant 不可用
- **WHEN** Qdrant 检查超时而其它依赖正常
- **THEN** 诊断端点 SHALL 返回 Qdrant unavailable 和其它依赖各自状态，HTTP 响应保持可用

### Requirement: 诊断响应 SHALL 避免暴露实现与敏感信息
普通用户诊断响应 SHALL NOT 包含连接串、主机凭据、绝对文件路径、内部堆栈或 secret；服务端日志可记录关联 id，前端 SHALL 使用行动码呈现可理解恢复建议。

#### Scenario: 数据库认证错误
- **WHEN** 后端诊断捕获数据库认证异常
- **THEN** 用户响应 SHALL 仅包含数据库不可用摘要与关联 id，不包含 DSN 或口令
