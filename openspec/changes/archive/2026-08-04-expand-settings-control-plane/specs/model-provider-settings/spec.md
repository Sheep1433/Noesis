## ADDED Requirements

### Requirement: 用户 SHALL 管理隔离且脱敏的 Provider 连接
系统 SHALL 允许已认证用户创建、编辑、启停和删除自己的 Provider 连接，并配置类型、显示名、Base URL 与凭据。读取接口 SHALL 仅返回凭据是否已配置、可选后缀和更新时间；写入 SHALL 明确支持 keep、replace、clear 三态。

#### Scenario: 读取 Provider
- **WHEN** 用户读取已配置 API Key 的 Provider
- **THEN** 响应 SHALL 表示密钥已配置但 SHALL NOT 返回明文或可复原值

#### Scenario: 用户隔离
- **WHEN** 用户 A 请求修改用户 B 的 Provider id
- **THEN** 系统 SHALL 返回 404 且用户 B 的配置保持不变

### Requirement: Provider SHALL 支持连接测试与模型发现
系统 SHALL 提供受超时约束的连接测试，并在 Provider 支持时发现模型目录。结果 SHALL 包含成功状态、检查时间、可行动错误分类和脱敏摘要；认证请求详情、响应 header 与原始 secret SHALL NOT 返回前端。

#### Scenario: 认证失败
- **WHEN** Provider 使用无效凭据执行连接测试
- **THEN** 系统 SHALL 返回 authentication 类错误和用户可理解提示且不泄漏凭据

### Requirement: 用户 SHALL 为模型用途选择默认模型
系统 SHALL 支持 `chat`、`vision`、`embedding`、`rerank` 用途绑定到当前用户可用的 Provider 模型，并验证模型声明能力与用途兼容。解析顺序 SHALL 为用户用途绑定、平台用途默认、现有环境配置；修改只影响后续新 run。

#### Scenario: 设置默认视觉模型
- **WHEN** 用户把具备视觉能力的模型设置为 `vision` 默认
- **THEN** 后续新 run 的视觉模型解析 SHALL 使用该绑定

#### Scenario: 不兼容用途
- **WHEN** 用户把仅文本模型绑定到 `embedding`
- **THEN** 系统 SHALL 拒绝保存并指出能力不兼容
