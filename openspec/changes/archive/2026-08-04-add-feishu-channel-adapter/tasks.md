## 1. 配置与协议基础

- [x] 1.1 加入并锁定飞书官方 Python SDK，补齐 dev/prod/docker 配置示例与默认关闭的 runtime 开关
- [x] 1.2 扩展通道配置模型和 `/api/user/channels` schema，部署级保存飞书应用凭据，用户级保存 Open ID/Chat ID，并兼容旧 Telegram 数据
- [x] 1.3 为飞书配置校验、脱敏响应、越权和旧数据兼容补回归测试

## 2. 飞书 Adapter 与客户端

- [x] 2.1 新建 `domain/chat/delivery/feishu` 客户端，封装 token、应用校验、回复/发送/更新消息与脱敏错误
- [x] 2.2 实现飞书事件规范化、单聊/群 @文本提取、sender open_id 配对键和真实 ChannelAdapter 注册
- [x] 2.3 实现 event_id/message_id TTL 去重与不支持消息拒绝，并补 Adapter 单元测试

## 3. 运行时与 Agent 数据流

- [x] 3.1 实现飞书 WebSocket 客户端生命周期、线程到 asyncio loop 的快速入队和 supervisor 动态 reconcile
- [x] 3.2 将合法入站接入 Session、消息 SSOT、ChannelRunService 与 `origin=feishu`，支持 persistent/new_per_message
- [x] 3.3 实现飞书节流流式投影、终态分段回落和独立 delivery health，禁止镜像完整工具 output
- [x] 3.4 为快速确认、重复事件、未配对用户、群聊策略、无 SSE 落库与投递失败补集成测试

## 4. HITL

- [x] 4.1 实现飞书 approve/reject 交互卡片和短期单次 callback token 校验
- [x] 4.2 将卡片回调与下一条 clarification 文本映射到统一 resume decision，并补越权、过期和重复回调测试

## 5. 设置页与通道操作

- [x] 5.1 扩展连接测试与测试消息服务，使其按通道类型调用 Telegram 或飞书客户端并返回脱敏健康状态
- [x] 5.2 扩展通讯设置页的类型选择和飞书字段，保留 Telegram 编辑体验并更新用户可见说明
- [x] 5.3 补前端类型检查与组件测试，验证切换类型、Telegram secret 行为和飞书用户绑定 payload

## 6. 验证与文档

- [x] 6.1 运行飞书/通道/消息持久化后端测试，并执行后端启动检查
- [x] 6.2 运行前端 lint 与 build，修复本 change 引入的问题
- [x] 6.3 更新 README、长期架构文档和 OpenSpec README，写明飞书权限、配置、单实例边界与排障方法

## 7. 共享应用多用户绑定修正

- [x] 7.1 将飞书 App ID/App Secret 移到部署级配置，移除用户 API、存储和 UI 中的应用凭据
- [x] 7.2 将共享 WebSocket 入站按 Open ID 解析到用户级通道配置，隔离 session、Agent 路由与 HITL
- [x] 7.3 增加双用户绑定与共享凭据连接/投递回归测试
- [x] 7.4 更新长期文档并完成后端、前端与 OpenSpec 全量验证
