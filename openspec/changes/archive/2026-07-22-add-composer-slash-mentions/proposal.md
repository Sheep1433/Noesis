## Why

Composer 已有 `+` 菜单可勾选 Models / Skills / MCP，但缺少 Cursor 式输入内触发的 `/`、`@` 快速引用：用户无法在打字时秒选 skill、挂 workspace 文件，或点名 subagent。Web 端没有本机索引，需要明确「预取 catalog + 本地过滤 + 结构化 mentions」契约，才能既对齐桌面体验又不在按键时打满接口。

## What Changes

- 在 chat Composer 输入框支持 **`/`**（Skills 等 slash 命令目录）与 **`@`**（文件 / 文件夹 / subagent 等 mention）弹出 picker。
- 前端对 catalog **预取 + TTL 缓存 + 本地 fuzzy**；**禁止**按键 debounce 全量请求。
- 发送消息时携带结构化 **`mentions`**（路径 / skill_id / subagent_type），由后端解析并注入本轮 prompt / 工具约束；大文件默认只传引用，不强制前端塞全文。
- 复用现有 `GET /api/skills/fs/tree` 与 `GET /api/chat/sessions/{session_id}/context`（及工作区只读文件 API）；subagent 列表按 `qa_type` 静态或轻量配置下发，不新增重索引服务。
- 与既有 `+` 菜单并存：`/` 选 skill 可同时反映到会话 `enabled_skills`（可选同步）；`@` 文件不替代附件上传。
- **非目标**：本机语义 `@Codebase` 向量检索、自定义 `.cursor/commands` 任意脚本、跨会话全库文件搜索、MCP 连通探测。

## Capabilities

### New Capabilities

- `composer-slash-mentions`：Composer `/`、`@` picker、catalog 缓存策略、mentions 载荷与后端注入契约

### Modified Capabilities

- `platform-chat`：流式问答请求与 user 消息 `extra` 增加 `mentions`；服务端注入规则
- `chat-session-context-panel`：context 树可作为 `@` 文件候选来源（只读复用，面板行为不变）
- `agent-super-agent`：消费 skill / file / subagent mentions 的运行时语义
- `agent-fault-operation`：消费 file / subagent mentions（有 subagent 时）的运行时语义

## Impact

- 前端：`chat.vue` 输入区、新 mention picker 组件、catalog store/composables；`sendMessage` / SSE 请求体
- 后端：`QaQueryRequest`（或等价 stream extra）、`qa_service` 注入、可选 prompt 片段；**无新重型 API**（优先复用 skills + session context）
- 规格：`platform-chat`、超级/故障 Agent；与 `composer-session-tools`（`+` 菜单）互补，不替代
- 兼容：旧客户端不传 `mentions` 时行为不变（非 BREAKING）
