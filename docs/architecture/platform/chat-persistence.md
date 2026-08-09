# 聊天记录与持久化架构

> 状态：Current
> OpenSpec：`platform-chat`、`user-platform`、`agent-run-delivery`

## 1. 数据职责

PostgreSQL 保存用户、会话、消息、附件元数据和设置。Qdrant 只保存知识库向量与分片数据。LangGraph checkpoint 使用独立逻辑库，不修改业务表。

核心关系：

```text
User 1 ── N ChatSession 1 ── N ChatMessage
                         └── N ChatAttachment
```

Service 层负责事务和权限；API 不直接访问 ORM。认证使用 Cookie Session + CSRF，前端不以 `sessionStorage` token 作为权威认证方案。

## 2. 会话生命周期

空白页面不应因组件挂载自动创建会话。首次发送、上传或需要持久化会话级设置时，平台才物化 session。刷新、路由恢复和侧栏列表使用同一 session id。

删除会话时，平台先完成数据库状态变更，再安排沙箱、附件和工作区清理。外部资源清理失败必须可观测，不应回滚已确认的业务删除。

## 3. 消息模型

assistant 使用 multipart `content.parts`。一轮流式回答只写一条 assistant 行，经 skeleton、可选 checkpoint、终态 UPDATE 完成。详细状态机见 [SSE 流式数据设计](chat-streaming.md)。

历史读取必须兼容旧纯文本 content；新 part 必须 versioned 或提供 tolerant parser，不能导致整条历史消息丢失。

## 4. 权限与隔离

- 所有 session/message 查询必须带当前 user 条件。
- message 详情、附件和未来来源详情必须以 session ownership 为授权根。
- 用户工作区与会话工作区分离，路径由 `noesis.config` 统一生成。
- API 参数中的 user id、collection 或文件路径不能替代服务端权限校验。

## 5. 代码入口

- API：`backend/server/api/chat_api.py`
- Service：`backend/packages/noesis-core/src/noesis/services/chat_service.py`、`services/qa/`
- ORM：`backend/packages/noesis-core/src/noesis/storage/postgres/models/chat.py`
- 消息构建：`backend/packages/noesis-core/src/noesis/domain/chat/message_builder.py`
- 前端历史恢复：`frontend/src/store/business/initChatHistory.ts`
