## REMOVED Requirements

### Requirement: 用户 SHALL 管理隔离且脱敏的 Provider 连接

**Reason**: 普通用户不应向平台托管 Provider API Key 或指定任意远程 Base URL。

**Migration**: Provider 与凭据改由部署者通过服务端配置维护；数据库迁移清除已有用户 Provider 数据。

### Requirement: Provider SHALL 支持连接测试与模型发现

**Reason**: 用户级连接已移除，模型目录由部署端统一发布。

**Migration**: 用户通过 `/api/models` 查看平台可用模型，部署者负责验证 Provider。

### Requirement: 用户 SHALL 为模型用途选择默认模型

**Reason**: 用户用途绑定依赖用户 Provider，且与聊天页模型选择职责重叠。

**Migration**: 对话模型由用户在聊天页选择，其余用途使用平台默认配置。

## ADDED Requirements

### Requirement: 用户 SHALL 只读查看平台模型目录

设置页 SHALL 展示平台已配置的可用模型、默认模型及用户可理解的能力信息，不得提供 Provider、Base URL 或 API Key 输入。

#### Scenario: 查看模型设置
- **WHEN** 用户打开模型设置
- **THEN** 页面 SHALL 展示 `/api/models` 返回的目录且不出现凭据管理操作
