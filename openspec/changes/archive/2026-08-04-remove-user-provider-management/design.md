## Context

当前模型设置把普通用户 Provider 连接、API Key、远程模型发现、用途绑定和 Agent 运行时快照串成一条链。平台同时已有部署端模型目录 `/api/models` 和聊天页模型选择器，因此用户 Provider 并非运行 Agent 的必要条件。敏感值加密仍被 Telegram、MCP 等功能使用，不能整体删除。

## Goals / Non-Goals

**Goals:**

- 普通用户界面与 API 不再接收 Provider API Key 或任意 Base URL。
- Agent 只使用部署端模型目录和服务端凭据。
- 保留聊天模型选择、视觉能力判断和平台诊断。
- 删除已落库的用户 Provider 密文与无效模型绑定。

**Non-Goals:**

- 不新增管理员控制台或 RBAC。
- 不改变部署端 `config.yaml`、环境变量与模型目录格式。
- 不删除通道、MCP 等其它功能对敏感值加密的使用。
- 不改变聊天 SSE 协议。

## Decisions

### 1. 删除用户 Provider API，而不是隐藏 UI

`backend/noesis_server/api/provider_api.py` 及其 router 将移除，避免隐藏入口仍可写入密钥。`provider_service.py`、Provider repository 方法、相关 schemas 和 QA runtime snapshot 注入一并删除。相比 feature flag，这形成单一清晰产品模式。

### 2. 模型设置改为平台目录只读视图

前端 models section 复用 `frontend/src/api/models.ts` 的 `/api/models` 数据，展示当前默认模型和视觉能力。实际每个会话的模型选择继续由聊天页 `ModelSelector` 完成，不再维护 `chat/vision/embedding/rerank` 用户绑定。

### 3. 数据库迁移删除两张用户模型表

新增 Alembic revision 删除 `user_model_purpose_bindings` 后再删除 `user_provider_connections`。这是有意的数据清除，避免废弃 API Key 密文长期留存。downgrade 只恢复空表结构，不恢复数据。

### 4. 敏感值加密基础设施保留

`SecretCipher` 与 `SETTINGS_ENCRYPTION_KEY` 仍服务于 Telegram Token 和 MCP secret。只删除 Provider 专属 schema、service 和调用点，避免误伤其它设置。

### 5. 导入导出与诊断移除 providers 域

设置导出不再产生 providers，导入 preview/apply 不再接受 providers。诊断的 models 检查改为验证平台模型目录是否可用，不发起用户自定义远程请求。

## Risks / Trade-offs

- [迁移会永久删除已有用户 Provider] → 在 proposal 标记 breaking；downgrade 只恢复空结构。
- [用户不能覆盖平台模型地址] → 由部署者配置，这是本次明确的产品边界。
- [删除 ProviderService 影响 chat] → `_resolve_model_for_query` 移除快照步骤后运行平台目录回归测试。
- [设置能力声明过期] → 同步 capabilities、导入导出域和用户文案。

## Migration Plan

1. 先部署不再引用用户 Provider 表的应用代码。
2. Alembic 删除模型绑定和 Provider 连接表。
3. 前端模型 section 切换为只读平台目录。
4. 回滚时 downgrade 重建空表；用户必须重新配置，旧密钥不可恢复。

## Open Questions

无。管理员模型管理留待出现真实需求后另立变更。
