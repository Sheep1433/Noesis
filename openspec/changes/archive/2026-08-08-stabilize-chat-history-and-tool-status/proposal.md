## Why

当前聊天页存在三类直接破坏可信度的问题：工具已经失败却仍显示“运行中”，新会话标题长期停留为“新对话”，以及刷新后 user/assistant 消息可能倒序。它们分别暴露出工具生命周期没有统一终态、Run 新入口遗漏会话标题写入、消息仅靠毫秒时间排序且同毫秒无稳定次序，需要作为一个独立变更统一修正并验收。

## What Changes

- 建立工具调用从开始、等待授权、执行到终态的完整状态机；所有已开始的工具必须收敛到成功、失败、超时、拒绝或取消之一，不允许终态 Run 留下无解释的“运行中”。
- 统一实时 SSE、Run snapshot、assistant 消息落库与历史恢复的工具状态；刷新后服务端权威快照/历史必须纠正客户端旧状态。
- 为用户提供分层失败展示：折叠态说明结果与影响，展开态提供可操作详情；隐藏堆栈、内部路径和基础设施细节。
- 以真实结构化执行结果判定命令成功、非零退出和超时；不从输出文本或 shell 包装是否抛异常猜测结果。
- 新 Run 创建时，在同一事务内仅用首条非空用户消息设置仍为默认值的会话标题，并保证列表与当前页面最终读取服务端标题。
- 为会话消息增加服务端分配的确定性顺序，历史 API 与前端严格按该顺序恢复 user → assistant；不再仅依赖 `created_at` 或角色猜测。
- **BREAKING**：不保留无确定性消息顺序的旧写入路径；所有新消息必须携带服务端顺序。现有 Web 客户端同步升级，不为历史接口保留第二套排序/标题逻辑。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `agent-tool-failure-handling`：扩展工具生命周期终态、HITL/取消/超时语义、命令真实结果判定及回答完整性影响。
- `platform-chat`：扩展工具状态 UI 与刷新恢复契约、默认标题一次性生成、消息确定性排序及相关 `/api/chat` 响应字段。

## Impact

- 后端：`RunProjection`、`AssistantMessageBuilder`、LangGraph SSE bridge、PersistSink、工具失败/执行结果解析、`RunService.create`、`ChatService`、聊天 ORM/Schema/API 与 Alembic 迁移。
- 前端：`useSSEStream.ts`、`messageParts.ts`、`ToolCallCollapse`、HITL/子 Agent 展示、历史初始化和会话列表标题更新。
- 数据：消息新增确定性顺序字段；现有数据迁移按会话创建时间与稳定 tie-breaker 一次性回填。
- API/SSE：`/api/chat/sessions/{id}/messages` 增加消息顺序字段；现有 SSE 事件名保留，但工具状态载荷收紧为完整枚举。当前 Web 前后端同时升级，不承诺旧客户端兼容。
- 测试：新增工具状态矩阵、Run 终态收敛、刷新恢复、标题幂等、同毫秒消息排序及真实 PostgreSQL 回归测试。
