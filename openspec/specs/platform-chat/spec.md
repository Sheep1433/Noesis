# platform-chat Specification

## Purpose

本能力是 Noesis **网页聊天平台**的权威规格：会话与消息 API、`qa_type` 路由、SSE 对外契约、流式 assistant 落库状态机、停止与失败分流、以及 chat 页与流式强相关的 UI（reasoning / tool / todo / 子 Agent）。Composer 面（上传、mentions、上下文面板）见 `chat-composer`；Run Fan-out / 通道见 `agent-delivery`；HITL 策略见 `agent-hitl`。

## Requirements

### Requirement: 会话生命周期管理

系统 SHALL 提供会话创建、列表、更新标题、软删除等 API；软删除时 SHALL 清理该会话磁盘子树（见 `agent-runtime`），**SHALL NOT** 删除用户级记忆与 Skills。

#### Scenario: 软删清理会话磁盘

- **WHEN** 用户软删除会话 `sid`
- **THEN** `.data/users/{uid}/sessions/{sid}/` SHALL 被删除；`AGENTS.md` / `USER.md` / `skills/` SHALL 保留

### Requirement: 消息列表与详情

系统 SHALL 提供按会话拉取消息历史的 API；返回结构 SHALL 支持前端按 parts 渲染（含 tool / reasoning / HITL 部件）。

#### Scenario: 历史含通道来源消息

- **WHEN** 同会话存在经 Telegram 入站写入的 user 消息
- **THEN** 网页历史 API SHALL 可见该消息（来源元数据 MAY 暴露）

### Requirement: qa_type 路由

`POST` 流式问答 SHALL 按 `qa_type` 路由到对应 Agent profile（详见 `agent-profiles`）：

| `qa_type` | Agent |
|-----------|--------|
| `COMMON_QA` | GeneralQAAgent |
| `FAULT_OPERATION_QA` | FaultOperationAgent |
| `TEST_CASE_QA` | CaseCoordinator |
| `SUPER_AGENT_QA` | SuperAgent |

未知 `qa_type` SHALL 拒绝。历史仅展示的废弃类型（如旧 DeepResearch）MAY 只读映射，**SHALL NOT** 作为新发送入口。

#### Scenario: SUPER_AGENT 路由

- **WHEN** 请求 `qa_type=SUPER_AGENT_QA`
- **THEN** 系统 SHALL 使用 SuperAgent 装配（Skills / 工作区 / 可选 HITL）

### Requirement: 流式问答与 SSE 核心契约

浏览器主通道 SHALL 为 `POST /api/chat/sessions/stream`（或项目现行等价路径）的 SSE。事件类型至少覆盖：`reasoning-*`、`text-*`、`tool-call-*` / `tool-input-*`、`tool-output-available`、`usage-update`、`context-update`、`hitl-required`、`error`、`finish`、`[DONE]`。

内部事件权威表示见 `agent-delivery`（RunEvent）；SseDelivery SHALL 将 RunEvent 编码为上述对外契约。keepalive 注释帧若存在，SHALL 仅由传输层注入。

#### Scenario: 文本增量兼容

- **WHEN** run 产生文本增量且客户端订阅 SSE
- **THEN** 客户端 SHALL 收到兼容的 `text-delta`（或现行等价名）帧

### Requirement: SSE 传输稳定性

流式路径 SHALL 配置合理的代理/应用超时；服务端 MAY 按可配置间隔发送 SSE 注释保活帧。连接类写入失败 SHALL 可观测，**SHALL NOT** 笼统降级为未分类业务错误。

#### Scenario: 保活不污染总线

- **WHEN** SseDelivery 注入 keepalive
- **THEN** PersistSink / ChannelDelivery **SHALL NOT** 将其当作 RunEvent 业务事件

### Requirement: 流式 assistant 消息 SHALL 按骨架—检查点—终态单次落库

同一轮 SSE / run 对应 DB **一行** assistant（`message_id` = `assistant_message_id`）：骨架（`streaming`）→ 可选检查点（会话 context）→ 终态 UPDATE。终态互斥：`completed` / `error` / `partial`。

流式过程中 **SHALL NOT** 按 token 增量 UPDATE assistant 正文。落库 **SHALL NOT** 依赖浏览器 SSE 仍存活（权威在 PersistSink，见 `agent-delivery`）。

HITL 暂停（`hitl_pending`）时 assistant **SHALL** 保持 `streaming`，**SHALL NOT** 写入终态；resume 续写同一 `assistant_message_id`。

#### Scenario: 无浏览器仍终态

- **WHEN** run 仅有 PersistSink（通道/定时任务）并完成
- **THEN** assistant SHALL 更新为 completed（或等价成功态）

#### Scenario: 用户停止 → partial

- **WHEN** 用户触发 stop 且生命周期原因为用户停止
- **THEN** assistant SHALL 为 `partial`，并带与现网一致的停止语义

### Requirement: tool-output-available 语义

`tool-output-available` SHALL 携带单次工具耗时；错误帧 MAY 含 `errorCategory`；成功帧 MAY 含 outcome 元数据。assistant 落库 tool part SHALL 与 SSE 错误语义一致。细则见 `agent-tool-failure-handling`。

#### Scenario: 耗时字段

- **WHEN** 工具调用结束并发出 tool-output-available
- **THEN** 帧 SHALL 含可解析的耗时（毫秒或约定单位）

### Requirement: usage-update 与上下文指示

流式路径 SHALL 发出消息级累计 LLM token 的 `usage-update` 与 `finish.usage`；MAY 发出会话上下文占用的 `context-update`。系统 SHALL 提供可配置的上下文窗口上限；会话 MAY 持久化最近上下文快照。

#### Scenario: finish 含 usage

- **WHEN** 一轮正常完成
- **THEN** `finish`（或等价）SHALL 含累计 usage，供 chat 页展示

### Requirement: 停止生成

系统 SHALL 提供停止当前流的 API；用户停止、网络中断与生成失败 SHALL 分流（不同 `finish_reason` / 文案）。chat 页停止 UI SHALL 等待服务端 SSE 结束，避免本地假完成。

#### Scenario: stop → partial

- **WHEN** 客户端调用 stop 且流仍在进行
- **THEN** 服务端 SHALL 中止 Agent 并将 assistant 标为 partial

### Requirement: Langfuse 可选追踪

当配置启用时，流式问答 SHALL 关联 Langfuse 会话/trace；关闭时 **SHALL NOT** 阻断主路径。

#### Scenario: 关闭无影响

- **WHEN** Langfuse 未配置或关闭
- **THEN** 流式问答 SHALL 正常完成

### Requirement: LLM 工厂

系统 SHALL 按配置的 `MODEL_TYPE`（或等价）选用厂商 LangChain 集成创建聊天模型；**SHALL NOT** 在业务代码硬编码密钥。

#### Scenario: 缺密钥失败可定位

- **WHEN** 所需 API Key 缺失
- **THEN** 创建模型 SHALL 失败并给出可定位错误，而非静默空响应

### Requirement: reasoning SSE 与 UI

`LangGraphSseBridge`（或经 Delivery 映射的等价路径）SHALL 从模型 chunk 提取思考并发出 `reasoning-*`；chat 流式页 SHALL 原生 reasoning 优先于 redacted 兜底。

#### Scenario: reasoning-delta

- **WHEN** 模型产出可提取的思考增量
- **THEN** 客户端 SHALL 收到 `reasoning-delta`（或等价）

### Requirement: TodoList 与 write_todos

chat 页 SHALL 从 `write_todos` 的 tool-input-available 更新 TodoList；生命周期 SHALL 仅绑定当前流式回合。

#### Scenario: 新回合清空

- **WHEN** 用户发起新一轮流式问答
- **THEN** 上一回合 TodoList 展示状态 SHALL 重置或不串到新回合

### Requirement: 子 Agent（task）展示

chat 页 SHALL 对 `task` 工具 parts 渲染折叠 UI；子 Agent 内部 tool/text/reasoning parts SHALL 嵌套展示。流式帧与 parts MAY 含 `parentTaskCallId`。非法 input/output SHALL 防御性处理。

#### Scenario: 嵌套 tool

- **WHEN** 子 Agent 产生工具调用
- **THEN** UI SHALL 在父 task 折叠块内展示，而非与顶层工具平铺混淆

### Requirement: Agent runtime 防护（摘要）

Agent runtime SHALL 支持独立摘要模型的 summarization offload；SHALL 在工具循环早期检测并收敛；SHALL 修复 dangling tool calls 后再继续模型调用。细则可落在实现与回归测试，本 spec 保留验收意图。

#### Scenario: dangling tool call

- **WHEN** 历史中存在未配对的 tool_call
- **THEN** 继续调用模型前 SHALL 补齐或剥离，避免提供商协议错误

### Requirement: 聊天关系数据 PostgreSQL

会话、消息、附件元数据等聊天关系数据 SHALL 持久化在 PostgreSQL（见 `user-platform`）；语义与既有 API 一致。

#### Scenario: 重启后历史仍在

- **WHEN** 后端重启后拉取同一 session 消息
- **THEN** 已终态消息 SHALL 仍可查询

### Requirement: HITL 传输面（指针）

`hitl-required` SSE、`hitl/resume` API、assistant HITL 部件状态的**传输与落库** SHALL 满足 `agent-hitl` 与 `agent-delivery`；本能力保证网页 SSE/API 入口可用。

#### Scenario: 网页可 resume

- **WHEN** 流发出 `hitl-required` 且用户提交 approve
- **THEN** 同一 `assistant_message_id` 上 run SHALL 继续并最终终态落库
