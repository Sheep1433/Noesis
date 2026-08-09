## Why

Noesis 面向少量授权用户，模型凭据应由部署者统一管理。继续允许普通用户保存自定义 Provider API Key，会扩大凭据托管、误用和任意网络访问风险，也让产品边界与项目叙事变得模糊。

## What Changes

- **BREAKING**：删除 `/api/user/providers` 与 `/api/user/model-bindings` 用户级 Provider、凭据、连接测试、模型发现和用途绑定 API。
- 删除设置页的 Provider、Base URL、API Key、连接测试和模型绑定界面。
- 设置页“模型”改为只读展示平台已配置的对话模型及能力；实际选择继续使用聊天页现有模型选择器。
- Agent 运行不再解析用户 Provider 快照，统一使用部署端模型目录和服务端凭据。
- 设置导入、导出、诊断和 capabilities 不再包含用户 Provider 域。
- 通过数据库迁移删除用户 Provider 连接与用途绑定表，从持久化层清除已托管凭据。
- 保留平台 `/api/models` 模型目录、聊天请求 `model_id`、部署端模型配置和 MCP/Telegram 所需的敏感值加密能力。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `model-provider-settings`: 从用户自定义 Provider 调整为部署端管理、用户只读模型目录。
- `settings-control-plane`: 设置页与诊断、迁移能力移除用户 Provider 域。
- `platform-chat`: 新 run 只解析平台模型目录，不再加载用户 Provider 运行时快照。
- `user-platform`: 用户设置不再接收或持久化 Provider API Key。

## Impact

- 前端：重写模型设置 section，删除用户 Provider API client 和敏感输入文案。
- 后端：删除 `provider_api.py`、`ProviderService`、相关 repository/schema/runtime snapshot 连接点。
- 数据库：新增 Alembic 迁移，删除 `user_provider_connections` 与 `user_model_purpose_bindings`，现有用户 Provider 配置不可恢复。
- 兼容性：用户 Provider API 为 breaking removal；`GET /api/models` 与 `/api/chat` 的 `model_id` 保持兼容，SSE 无变化。
