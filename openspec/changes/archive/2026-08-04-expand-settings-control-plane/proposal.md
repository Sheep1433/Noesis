## Why

Noesis 已具备个人画像、记忆、定时任务、Telegram、Skills、MCP 与知识库等配置入口，但设置体验仍以分散入口和基础 CRUD 为主，缺少统一的发现、验证、运行反馈、诊断与恢复闭环。当前相关运行时与配置边界已经稳定，适合把设置页升级为面向用户的 Agent 控制面，并为后续能力扩展建立一致契约。

本 change 的目标是让用户能够在统一界面中回答四个问题：当前配置是什么、是否有效、运行结果如何、出现问题时如何定位与恢复。非目标包括多成员团队编排、语音服务、插件市场、多项目配置同步，以及改变现有聊天 SSE 协议。

## What Changes

- 扩展 `/settings` 信息架构：支持 section 搜索、稳定深链、配置健康概览和统一的加载、空态、保存、错误、危险操作交互。
- 新增模型与 Provider 设置：管理 Provider 连接、脱敏凭据、模型发现与能力元数据，并选择默认聊天、视觉、Embedding 与 Rerank 模型。
- 将 MCP 从 JSON-only 管理升级为表单与高级 JSON 双模式，支持单服务启停、连接探测、工具目录和可定位诊断；保留现有 `/api/mcp` 兼容路径。
- 扩展自动化设置：完整编辑任务、解释日程与下次执行时间、选择时区/Agent/会话/投递目标，并查看运行历史、结果摘要、错误和手动重试。
- 扩展通讯通道设置：启停、连接测试、发送测试消息、健康摘要、默认路由与最近收发结果；首期沿用 Telegram adapter，不在本 change 新增其它通道实现。
- 新增 Agent 上下文可见性：结构化画像、L2 日记浏览/搜索、记忆体积与修改信息、规则/提示词来源，以及最终注入内容预览；继续保留 Markdown 原文编辑。
- 新增通知与系统诊断：配置任务、审批、通道等用户通知偏好；汇总模型、MCP、Scheduler、通道、数据库、Qdrant 与 Sandbox 的健康状态。
- 新增用户设置导出、导入预览、恢复默认与敏感配置变更审计；导出物默认排除密钥、Token 与其它 secret。
- 不改变 `/api/chat` SSE 事件契约；现有设置、模型目录、MCP、定时任务、通道 API 在迁移期保持兼容，新增能力优先扩展既有 `/api/user`、`/api/models`、`/api/mcp` 路径。

## Capabilities

### New Capabilities

- `settings-control-plane`: 设置壳、搜索/深链、健康概览、统一交互、设置迁移与敏感变更审计。
- `model-provider-settings`: Provider 凭据、连接测试、模型发现、模型能力元数据与默认用途选择。
- `automation-operations`: 定时任务完整配置、调度预览、运行记录、结果查看与重试。
- `channel-operations`: 用户通道连接测试、投递测试、健康状态、路由配置与最近活动。
- `agent-context-settings`: 用户画像、分层记忆、规则/提示词来源与最终上下文注入预览。
- `platform-settings-observability`: 用户通知偏好与平台依赖健康诊断的设置界面和 API 契约。

### Modified Capabilities

- `user-platform`: 扩展用户作用域配置、脱敏 secret、配置导入导出和敏感变更审计的认证与隔离要求。
- `agent-runtime`: 扩展用户记忆 L2 浏览/搜索元数据及 Agent 最终上下文预览契约，不改变 `/memory/` 权威路径。
- `agent-delivery`: 暴露通道健康、测试投递与最近投递结果供设置控制面消费，不在 Delivery 内复制通道配置。
- `platform-chat`: 模型默认用途选择影响新会话/新 run 的模型解析，但不改变聊天 API 与 SSE 对外事件形状。

## Impact

- **前端**：`frontend/src/views/settings/`、模型选择器、Skills/MCP 管理页及共享设置组件；需要补充设置路由与关键 section 的组件测试和 smoke 测试。
- **后端 API**：扩展 `/api/user/settings`、`/api/user/scheduled-tasks`、`/api/user/channels`、`/api/models`、`/api/mcp`，新增诊断、运行记录、通知偏好、导入导出与审计端点；均使用 Cookie Session + CSRF，不引入 Bearer JWT。
- **服务与数据**：新增 Provider/默认模型配置、自动化运行记录、通知偏好和设置审计存储；复用既有 Scheduler、ChannelAdapter、MCP probe、健康检查及用户数据目录。
- **安全**：secret 仅服务端可读，列表/导出/日志/审计均不得返回明文；Agent 工具不得修改 Provider、通道、通知或自动化控制面配置。
- **兼容性**：无计划内 breaking change；现有配置文件与 API 继续可用，通过迁移读取和双模式编辑逐步收敛到新的控制面。
