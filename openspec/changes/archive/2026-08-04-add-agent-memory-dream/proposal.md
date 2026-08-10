## Why

现有 L2 只有文件列表与关键词搜索，没有稳定写入来源，也无法由 Agent 跨会话检索；同时 USER.md 的“常用字段”与原文编辑重复。需要把历史消息整理成可追溯、按需读取的长期记忆，并简化画像编辑入口。

## What Changes

- 移除 USER.md“常用字段”表单，仅保留 Markdown 原文编辑。
- 新增按用户、按自然日运行的“做梦”任务，从已完成且未删除的会话消息生成 `memory/YYYY-MM-DD.md`。
- L2 条目保存分类、摘要和来源 `session_id` / `message_id`；重复运行同一天时更新同一文件，不重复追加。
- 新增认证 API，用于手动触发、查看运行状态、搜索 L2 记忆和读取受权限约束的来源消息。
- 为 SuperAgent 新增跨会话记忆搜索与来源读取工具；L2 仍不默认注入会话上下文。
- 在设置页展示 L2 记忆、触发整理和检索结果。

## Capabilities

### New Capabilities

- `agent-memory-dream`: 每日记忆整理、可追溯存储、检索和 Agent 工具行为。

### Modified Capabilities

- `agent-user-memory`: 移除结构化画像字段，并明确 L2 由做梦任务写入、按需检索。
- `agent-user-settings`: 设置页画像仅编辑原文，并提供记忆整理与检索入口。
- `agent-runtime`: SuperAgent 获得按用户隔离的记忆搜索与来源读取工具。

## Impact

- 后端：`/api/user/memory` 增加做梦、检索和来源读取接口；新增记忆整理 Service、Agent tools 与运行状态存储。
- 前端：记忆设置页移除常用字段，增加整理按钮、日期状态和跨会话记忆检索。
- 数据：继续使用用户目录下的 Markdown L2 文件；不改变聊天消息表，不破坏现有 API。
- 依赖：首版不新增外部检索依赖，采用结构化 Markdown 条目和关键词检索。
