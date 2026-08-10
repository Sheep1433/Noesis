## 1. 共享设置地基（串行前置）

- [x] 1.1 建立 section 注册表及类型，迁移现有七个 section，并为标题、关键词、URL 深链和非法 id 回退补前端测试
- [x] 1.2 建立 SettingsSection、SettingsRow、SettingsField、SettingsStatus、SettingsEmptyState、SettingsDangerAction 等共享 primitives
- [x] 1.3 实现设置搜索、未保存离开保护和统一加载/保存/错误反馈，不改变现有 section 功能
- [x] 1.4 定义前后端共享的 secret keep/replace/clear 语义、脱敏 read model 和错误行动码
- [x] 1.5 建立 settings API/service/schema 模块边界与 capability flags，确认 API → Service → Domain/Harness 依赖方向
- [x] 1.6 为设置壳增加路由、键盘操作、移动端和 smoke 测试，并运行前端 lint/build

## 2. 数据模型与安全基础（串行前置）

- [x] 2.1 设计并添加 Provider、模型用途绑定、自动化运行记录、通知偏好和设置审计数据库 migration
- [x] 2.2 实现用户作用域 repository/service，覆盖跨用户 404、并发更新和事务回滚测试
- [x] 2.3 确认用户级 secret 静态加密或安全引用方案；若不可用则阻止 Provider secret 上线，不得使用明文临时字段
- [x] 2.4 实现统一 secret redact，覆盖 API、日志、审计、诊断和导出负向泄漏测试
- [x] 2.5 实现设置审计 append-only Service 与分页 API，确保审计内容不保存 secret 旧值或新值
- [x] 2.6 验证所有新增非安全方法使用 Cookie Session + CSRF，拒绝 Bearer JWT 与跨用户资源访问

## 3. Provider 与模型设置（并行工作流 A）

- [x] 3.1 实现 `/api/user/providers` CRUD、启停及脱敏凭据写入命令
- [x] 3.2 实现 Provider 连接测试、超时、错误分类和模型发现 API
- [x] 3.3 实现模型用途绑定 API，校验 chat/vision/embedding/rerank 能力兼容性
- [x] 3.4 扩展模型解析器，按用户绑定 → 平台默认 → 环境配置解析并在 run 启动时固定快照
- [x] 3.5 新增“模型与 Provider”设置 section，支持连接管理、测试、模型目录和默认用途选择
- [x] 3.6 补 Provider 用户隔离、secret 脱敏、连接失败、用途不兼容和 run 稳定性回归测试

## 4. MCP 管理闭环（并行工作流 B）

- [x] 4.1 在保留现有 `/api/mcp/config` 的前提下扩展单 Server 表单 CRUD 与启停 API
- [x] 4.2 统一 MCP probe read model，返回连接、工具数、检查时间和脱敏错误分类
- [x] 4.3 实现 MCP 工具目录查看及单 Server 刷新，不向前端暴露敏感 headers
- [x] 4.4 将 MCP 页面升级为表单模式与高级 JSON 双模式，并定义两种模式的冲突处理
- [x] 4.5 补 MCP 非法配置、远端超时、认证失败、用户隔离和 JSON 兼容回归测试

## 5. 自动化运行与历史（并行工作流 C）

- [x] 5.1 扩展 scheduled task API，支持完整编辑、cron 校验、时区、会话策略和投递目标
- [x] 5.2 实现 cron 人类可读摘要与下一次执行时间预览 API，并覆盖 DST/非法表达式测试
- [x] 5.3 在调度触发与手动触发时创建 queued/running/终态运行记录，关联 session/run 和投递结果
- [x] 5.4 实现运行历史分页、详情和保留清理策略，不破坏任务最新状态摘要
- [x] 5.5 实现可重试失败运行的新记录语义、`retry_of` 关联和幂等请求保护
- [x] 5.6 重构“自动化”设置 section，提供编辑器、下一次执行、历史详情、错误摘要和重试
- [x] 5.7 补多 worker 抢跑、状态互斥、失败记录、重试幂等和用户隔离回归测试

## 6. 通道诊断与路由（并行工作流 D）

- [x] 6.1 在 Delivery 中实现通道健康与最近活动 read model，复用 adapter 权威状态
- [x] 6.2 实现不产生聊天消息的连接测试，以及发送固定内容的测试投递命令
- [x] 6.3 扩展通道设置 API，支持默认 qa_type、会话策略和投递偏好并校验当前用户作用域
- [x] 6.4 重构“通讯”设置 section，展示启停、配对、健康、最近活动、连接测试和测试投递
- [x] 6.5 补禁用通道不收发、未配对拒绝、测试投递不落聊天消息、速率限制和 secret 脱敏测试

## 7. Agent 上下文与记忆（并行工作流 E）

- [x] 7.1 定义 USER.md 可无损维护的结构化字段格式，并实现结构化/原文模式冲突保护
- [x] 7.2 实现当前用户 L2 日记列表、元数据和限量搜索 API，覆盖路径穿越与跨用户隔离测试
- [x] 7.3 从 Agent runtime 提取可复用的上下文 resolver/compiler，只读返回来源、优先级、注入状态和体积估算
- [x] 7.4 实现指定 Agent profile 的最终上下文预览 API，验证不调用模型、不建 checkpoint、不写记忆
- [x] 7.5 扩展画像与记忆设置 section，并新增规则/提示词来源与最终预览界面
- [x] 7.6 补设置、会话面板和 Agent `/memory/` 同盘一致性，以及预览与真实 resolver 一致性测试

## 8. 通知、健康与配置迁移（并行工作流 F）

- [x] 8.1 实现通知偏好 Service/API，覆盖自动化结果、HITL 和通道异常事件类型
- [x] 8.2 把通知偏好接入现有 Delivery 表面，验证关闭通知不影响业务 run 与持久化
- [x] 8.3 实现模型、MCP、Scheduler、通道、数据库、checkpoint、Qdrant、Sandbox 并发健康聚合与单项超时
- [x] 8.4 新增设置概览健康摘要和系统诊断 section，仅展示用户可行动信息与关联 id
- [x] 8.5 实现版本化非敏感设置导出，覆盖 Provider、任务、通道、记忆和通知的 secret 排除测试
- [x] 8.6 实现导入 preview/apply、冲突检测、按域事务回滚和恢复默认危险操作
- [x] 8.7 补诊断单项失败不导致整体 500、Qdrant/MCP 外部 404 分类和产品文案不泄露内部实现测试

## 9. 功能分支集成与验收

- [x] 9.1 在 `feat/expand-settings-control-plane` 建立 foundation，并按 A-F 工作流集中实现；共享 primitives、API/schema 边界和 section 注册表由该分支统一维护
- [x] 9.2 完成跨工作流文件边界与依赖方向审查，确保能力保持独立 section、Service 和测试边界，共享注册由集成分支统一完成
- [x] 9.3 功能分支同步最新 `dev` 并完成自测；验证通过后按 `feat/expand-settings-control-plane → dev` 合并，禁止直接进入 `main`
- [x] 9.4 运行后端 `uv run pytest tests/ -q`，重点验证认证、MCP、Qdrant、Scheduler、Delivery 与消息持久化无回归
- [x] 9.5 运行前端 `pnpm lint`、相关组件测试、settings smoke 与 `pnpm build`
- [x] 9.6 执行设置关键路径人工验收：深链、搜索、Provider、MCP、任务历史、通道测试、上下文预览、诊断、导入导出
- [x] 9.7 验证现有 `/api/chat` SSE 事件与 assistant 单行终态落库契约未改变
- [x] 9.8 更新 README/运维说明与相关 PRD，记录 migration、capability flags、回滚和数据保留策略
