## ADDED Requirements

### Requirement: 新 run SHALL 按用途解析用户默认模型
聊天服务创建新 run 时 SHALL 通过模型用途解析器确定模型，解析顺序为当前用户用途绑定、平台用途默认、现有环境配置；一次 run SHALL 固定启动时解析结果。用户修改默认模型 SHALL NOT 改变正在执行的 run。

#### Scenario: 对话期间修改默认模型
- **WHEN** run 已启动后用户修改 `chat` 默认模型
- **THEN** 当前 run SHALL 继续使用启动时模型，下一次新 run SHALL 使用新的有效绑定

### Requirement: 设置控制面扩展 SHALL 保持聊天协议兼容
新增模型设置、运行记录、通知与诊断 SHALL NOT 改变 `/api/chat` 现有请求必填字段、SSE 事件集合和 assistant 骨架—检查点—终态单行落库状态机。

#### Scenario: 未配置用户模型绑定
- **WHEN** 用户没有任何用途绑定并发起现有聊天请求
- **THEN** 系统 SHALL 按平台/环境默认正常运行且前端无需新增 SSE 分支
