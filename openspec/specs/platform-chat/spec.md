# platform-chat Specification

## Purpose

本能力是 Noesis **网页聊天平台**的权威规格：会话与消息 API、`qa_type` 路由、SSE 对外契约、流式 assistant 落库状态机、停止与失败分流、以及 chat 页与流式强相关的 UI（reasoning / tool / todo / 子 Agent / 引用与来源展示）。Composer 面（上传、mentions、上下文面板）见 `chat-composer`；Run Fan-out / 通道见 `agent-delivery`；HITL 策略见 `agent-hitl`。
## Requirements
### Requirement: 会话生命周期管理

系统 SHALL 提供会话创建、列表、更新标题、软删除等 API；软删除时 SHALL 清理该会话磁盘子树（见 `agent-runtime`），**SHALL NOT** 删除用户级记忆与 Skills。

#### Scenario: 软删清理会话磁盘

- **WHEN** 用户软删除会话 `sid`
- **THEN** `.noesis/users/{uid}/sessions/{sid}/` SHALL 被删除；`AGENTS.md` / `USER.md` / `skills/` SHALL 保留

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

浏览器实时响应 SHALL 使用 `/api/chat` 下的 run 创建与 SSE 订阅端点。系统 SHALL 提供独立的 run 创建、状态查询、SSE 订阅和停止能力，并 SHALL 删除 `POST /api/chat/sessions/stream`。浏览器主实时通道仍为 SSE，不要求 WebSocket。

事件类型至少覆盖：`run-snapshot`、`run-status`、`reasoning-*`、`text-*`、`tool-call-*` / `tool-input-*`、`tool-output-available`、`context-update`、`hitl-required`、`error`、`finish`、`[DONE]`。业务事件 SHALL 携带 `run_id` 与 sequence；keepalive 注释帧 SHALL 仅由传输层注入。

#### Scenario: 创建后独立订阅
- **WHEN** 已认证用户成功创建 run
- **THEN** 创建响应 SHALL 返回 `run_id` 与 `assistant_message_id`
- **AND** 客户端 SHALL 能使用独立 SSE 端点订阅该 run

#### Scenario: 文本增量兼容
- **WHEN** run 产生文本增量且客户端订阅 SSE
- **THEN** 客户端 SHALL 收到兼容的 `text-delta` 帧

### Requirement: SSE 传输稳定性

流式路径 SHALL 配置合理的代理/应用超时；服务端 MAY 按可配置间隔发送 SSE 注释保活帧。连接类写入失败 SHALL 可观测，并 SHALL 只关闭对应 subscription，不得笼统降级为 run 业务错误或取消 producer。

客户端 SHALL 检查业务事件 sequence。发现 sequence gap、网络异常或未收到终态的 EOF 时，客户端 SHALL 查询权威 run 状态并重新订阅，SHALL NOT 把该 EOF 当作成功完成。

#### Scenario: 保活不污染总线
- **WHEN** SseDelivery 注入 keepalive
- **THEN** PersistSink / ChannelDelivery SHALL NOT 将其当作 RunEvent 业务事件

#### Scenario: 无终态 EOF 触发恢复
- **WHEN** 浏览器流在未收到终态事件时结束
- **THEN** chat 页 SHALL 保持 run 未完成语义并查询/重订阅
- **AND** SHALL NOT 调用成功收尾回调

### Requirement: 流式 assistant 消息 SHALL 按骨架—检查点—终态单次落库

系统 SHALL 保证同一 run 对应 DB 一行 assistant（`message_id = assistant_message_id`）：骨架（`streaming`）→ 可选节流 parts/context 检查点 → 终态 UPDATE。终态互斥：`completed` / `error` / `partial`。

系统 SHALL NOT 按 token 增量 UPDATE assistant 正文；完整工具结束、阶段结束、HITL pending 或可配置节流条件满足时 MAY 更新同一行 parts 检查点。落库 SHALL NOT 依赖浏览器 SSE 仍存活。

该原则 SHALL 覆盖所有流式内容类型：终态内容与生命周期事件（assistant text/reasoning 终态、tool part 终态、message/run 生命周期）入库（durable）；流式增量（token delta、reasoning raw delta、tool output chunk、progress、typing、heartbeat）走 SSE 实时投递，SHALL NOT 落 DB。新增写入点 SHALL 遵循同一原则。

HITL 暂停时 assistant SHALL 保持 `streaming`；resume 续写同一 `run_id` 与 `assistant_message_id`。服务重启导致无法继续时，run SHALL 为 `interrupted`，assistant SHALL 为 `partial`，`finish_reason=server_restart`。

#### Scenario: 无浏览器仍终态
- **WHEN** run 所有浏览器订阅均断开后正常完成
- **THEN** assistant SHALL 更新为 completed

#### Scenario: 服务重启保留检查点
- **WHEN** 后端启动恢复发现悬空 run 且 assistant 已有检查点
- **THEN** assistant SHALL 保留已有 parts 并更新为 partial
- **AND** SHALL 标记 `finish_reason=server_restart`

#### Scenario: 用户停止 → partial
- **WHEN** 用户明确触发 stop 且 run 尚未终态
- **THEN** assistant SHALL 为 partial
- **AND** SHALL 带 `finish_reason=stopped`

#### Scenario: tool output 不增量入库
- **WHEN** 工具执行过程中产生多个 stdout/stderr chunk
- **THEN** DB SHALL NOT 存在按 chunk 增量写入的行
- **AND** 工具终态时 SHALL 一次性写入完整 tool part 到 assistant message content

### Requirement: tool-output-available 语义

`tool-output-available` SHALL 携带单次工具耗时；错误帧 MAY 含 `errorCategory`；成功帧 MAY 含 outcome 元数据。assistant 落库 tool part SHALL 与 SSE 错误语义一致。细则见 `agent-tool-failure-handling`。

`tool-input-start` / `tool-input-available` / `tool-output-available` MAY 携带可选 `step_id` 标识所属 model step；同一 model step 内并行调用的工具共享同一 `step_id`。`step_id` 为增量字段，client MAY 忽略；assistant 落库 tool part SHALL 保留 `step_id` 以支持重载后重建并行分组。

#### Scenario: 耗时字段

- **WHEN** 工具调用结束并发出 tool-output-available
- **THEN** 帧 SHALL 含可解析的耗时（毫秒或约定单位）

### Requirement: 上下文占用指示

流式路径 SHALL 在 Provider 返回 usage 后发出当前上下文占用的 `context-update`。系统 SHALL 提供可配置的上下文窗口上限；会话 MAY 持久化最近上下文快照。

`context-update` SHALL 保留 `current_tokens`、`max_tokens`、`used_percentage`，其 `current_tokens` 取自主对话最近一次 model call 返回的 `input_tokens`（非累计）。子 Agent 的 model call SHALL NOT 写入 context 指示器，避免子 Agent 的输入占用覆盖主对话的上下文占用。

chat 页 SHALL 展示"当前上下文窗口占用"，取自主对话最近一次 model call 的 `input_tokens`。Provider 不返回 usage 或部分字段缺失时 SHALL 降级到上一轮真实值，SHALL NOT 阻断流式回答。

#### Scenario: context 用主对话最近一次真实 input_tokens
- **WHEN** 一轮 Agent run 中主对话与子 Agent 各进行模型调用
- **THEN** `context-update` 的 `current_tokens` SHALL 取自主对话最近一次 model call 的 `input_tokens`（非累计）
- **AND** 子 Agent 的 input_tokens SHALL NOT 覆盖主对话的 context 指示器
- **AND** SHALL NOT 用累计 input token 作为当前 context window 占用

#### Scenario: Provider 不返回 usage
- **WHEN** Provider 未返回 `input_tokens`
- **THEN** context 指示器 SHALL 沿用上一轮真实 `input_tokens`；首轮无数据时 SHALL NOT 展示指示器

### Requirement: 停止生成

系统 SHALL 提供按 `run_id` 停止当前执行的 API；用户停止、浏览器断开、生成失败与服务重启 SHALL 分流。刷新、关闭页面和普通网络断开 SHALL NOT 自动调用 stop。chat 页停止 UI SHALL 等待服务端 run 进入终态，避免本地假完成。

#### Scenario: stop → partial
- **WHEN** run 所有者明确调用 stop 且 run 仍在进行
- **THEN** 服务端 SHALL 中止 Agent 并将 assistant 标为 partial

#### Scenario: beforeunload 不停止
- **WHEN** 浏览器在 run 进行中刷新或关闭页面
- **THEN** 客户端 SHALL NOT 因 `beforeunload` 调用 stop
- **AND** 后端 SHALL 允许 run 继续

### Requirement: Langfuse 可选追踪

当配置启用时，流式问答 SHALL 关联 Langfuse 会话/trace；关闭时 **SHALL NOT** 阻断主路径。

#### Scenario: 关闭无影响

- **WHEN** Langfuse 未配置或关闭
- **THEN** 流式问答 SHALL 正常完成

### Requirement: LLM 工厂

系统 SHALL 按部署端配置的模型目录与 `MODEL_TYPE`（或等价配置）选用厂商 LangChain 集成创建聊天模型；用户选择的 `model_id` SHALL 只能引用平台公开目录。系统 SHALL NOT 从用户设置加载 Provider 地址、API Key 或运行时模型快照，且 SHALL NOT 在业务代码硬编码密钥。

#### Scenario: 缺密钥失败可定位
- **WHEN** 平台模型所需 API Key 缺失
- **THEN** 创建模型 SHALL 失败并给出可定位错误，而非静默空响应

#### Scenario: 用户选择平台模型
- **WHEN** 用户在聊天页选择 `/api/models` 中的模型
- **THEN** 后续 run SHALL 使用该平台目录项且不读取用户 Provider 配置

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

### Requirement: 流式问答入口 SHALL 经 Run Fan-out 投递

`POST /api/chat/runs` SHALL 创建由 RunManager 持有的 producer，并为该 Run 配置独立 PersistWriter；独立 SSE 订阅端点 SHALL 从 RunHandle subscription 消费带 sequence 的 RunEvent，并仅在 SSE delivery 边界编码。问答编排 SHALL NOT 在单一 HTTP generator 内同时拥有 producer、落库和客户端生命周期。

旧 `POST /api/chat/sessions/stream` SHALL 被删除，问答编排 SHALL NOT 保留第二条发送路径。

#### Scenario: 新入口快速返回 run 身份
- **WHEN** 已认证用户对 `/api/chat/runs` 发起合法创建请求
- **THEN** 服务端 SHALL 在 run 注册和消息骨架落库后返回 run 身份
- **AND** SHALL NOT 等待 Agent 完成才响应

#### Scenario: 旧入口不可用
- **WHEN** 客户端请求 `/api/chat/sessions/stream`
- **THEN** 系统 SHALL 返回 404 或路由不存在的等价结果
- **AND** SHALL NOT 通过隐藏包装创建 run

### Requirement: 停止生成 SHALL 走统一 Run 生命周期

停止生成接口 SHALL 通过统一 RunManager/cancel 入口通知目标 `run_id`，使 PersistSink 与仍订阅的 Delivery 观察到一致的中止语义。停止 SHALL 鉴权且幂等；系统 SHALL NOT 使用与 run 身份无关的 session 全局布尔量误停其它执行。

#### Scenario: 停止后 partial 落库
- **WHEN** 用户对所属 active run 调用停止接口
- **THEN** assistant SHALL 进入 partial
- **AND** 仍在线 Delivery SHALL 收到一致终态

### Requirement: HITL 分段流 SHALL 经同一 Fan-out

`hitl-required` / `finish_reason=hitl_pending` 与 `POST .../hitl/resume` 启动的新 producer segment **SHALL** 经同一 RuntimeEventMapper → RunHandle → SseDelivery / PersistWriter 路径，语义与主规格 `platform-chat` HITL 要求一致：pending 不 completed；resume 续写同一 `assistant_message_id`；**SHALL NOT** 在 RunHandle 外另起一套仅 generator 内可见的 HITL 落库分支。

#### Scenario: resume 仍走 Fan-out

- **WHEN** 用户对 pending HITL 调用 `hitl/resume`
- **THEN** resume 响应 SHALL 返回同一 Run 的权威 running snapshot
- **AND** 客户端 SHALL 重新订阅同一 `run_id`，PersistWriter SHALL 继续更新同一 assistant 行直至真正终态

### Requirement: chat 页 SHALL 从权威 run snapshot 恢复

chat 页 SHALL 保存当前 run_id、assistant_message_id 与 last_sequence。页面重新加载或连接恢复时，客户端 SHALL 通过服务端 active-run API 查询权威 Run 并重新订阅，不得依赖 sessionStorage 或消息历史推测；收到 `run-snapshot` 时 SHALL 按 replace 语义重建该 assistant parts，而不是重复 append。

#### Scenario: 刷新后继续显示增量
- **WHEN** 用户在 run 进行中刷新并重新进入同一 session
- **THEN** chat 页 SHALL 加载当前 snapshot 并订阅后续事件
- **AND** 用户 SHALL 继续看到同一 assistant_message_id 的生成过程

#### Scenario: 重复事件按 sequence 忽略
- **WHEN** 重订阅补发了 sequence 小于等于客户端 last_sequence 的事件
- **THEN** 客户端 SHALL 忽略该重复事件

#### Scenario: HITL 续跑更新原工具块
- **WHEN** 刷新恢复或批准续跑再次产生相同 `tool_call_id` 的工具输入与结果事件
- **THEN** 服务端 snapshot 与 chat 页 SHALL 更新原工具块的输入、HITL 和执行状态
- **AND** SHALL NOT 追加第二个工具块

#### Scenario: 多会话审批状态隔离
- **WHEN** 一个或多个 session 分别处于 `hitl_pending` 且用户切换当前会话
- **THEN** chat 页 SHALL 只展示当前 session 对应的审批面板
- **AND** 切走 SHALL 仅隐藏该面板而不丢弃其 pending 状态
- **AND** 切回后 SHALL 从 session 本地状态或权威 run snapshot 恢复该面板
- **AND** 提交 SHALL 使用该审批自身绑定的 `session_id`、`run_id` 与 `interrupt_id`

#### Scenario: 审批后等待继续输出
- **WHEN** 用户提交审批且权威 run snapshot 从 `hitl_pending` 变为 `queued`、`running` 或 `retrying`
- **THEN** chat 页 SHALL 立即显示“正在继续生成”或等价状态
- **AND** SHALL NOT 等到下一段正文 token 到达后才恢复生成提示

#### Scenario: 切换会话隔离旧 Run 订阅
- **WHEN** 用户从存在 active Run 的会话切换到另一会话或新对话
- **THEN** 客户端 SHALL 释放旧会话的本地 subscription 但 SHALL NOT 停止服务端 Run
- **AND** 旧 subscription 的迟到 snapshot 或 delta SHALL NOT 修改新会话界面
- **AND** HITL 审批 SHALL 严格使用当前 session 对应的 `run_id`

### Requirement: chat 页 SHALL 区分临时重试与终态失败

客户端收到 `run-status` 且 `will_retry=true` 时 SHALL 保持 loading 并显示受控重试状态；恢复到 running 后 SHALL 清除临时提示。只有 run `error` 或终态 error 事件 SHALL 触发最终失败 UI。

#### Scenario: LLM 重试期间用户可感知
- **WHEN** 模型流断开且服务端正在自动重试
- **THEN** 用户 SHALL 看到“正在重试”或等价状态
- **AND** 当前已生成内容 SHALL 保留

#### Scenario: 重试耗尽显示最终错误
- **WHEN** 所有自动重试均失败
- **THEN** chat 页 SHALL 显示脱敏终态错误
- **AND** SHALL 结束该 run 的 loading

### Requirement: chat 页 SHALL 对创建和重连实施幂等与限速

chat 页 SHALL 为一次用户发送生成稳定 `client_request_id`，在创建响应未知时使用同一身份重试，不得因网络错误生成新的幂等键。SSE 重连 SHALL 使用指数退避与随机抖动，并限制连续自动重连频率；页面重新可见或网络恢复时 MAY 再次查询权威 run 状态。

达到自动重连上限时，客户端 SHALL 保留 run 的非终态语义并提供手动重连，不得伪装 completed。多个标签页 MAY 分别订阅同一 run，单个标签页的通知或失败状态 SHALL NOT 修改服务端 run。

#### Scenario: 创建响应未知后重试
- **WHEN** 创建请求可能已经成功但客户端因网络错误未收到响应
- **THEN** 客户端 SHALL 使用原 client_request_id 重试
- **AND** 服务端返回的仍是原 run

#### Scenario: 服务恢复时避免重连风暴
- **WHEN** SSE 连续失败
- **THEN** 客户端 SHALL 使用带随机抖动的指数退避
- **AND** SHALL NOT 立即无限循环请求 run 与 stream 接口

#### Scenario: 达到自动重连上限
- **WHEN** 客户端达到连续自动重连次数上限且服务端终态未知
- **THEN** 页面 SHALL 显示连接恢复操作并保留已有内容
- **AND** SHALL NOT 将 run 标记为成功或最终失败

### Requirement: 新 run SHALL 按用途解析用户默认模型
聊天服务创建新 run 时 SHALL 通过模型用途解析器确定模型，解析顺序为当前用户用途绑定、平台用途默认、现有环境配置；一次 run SHALL 固定启动时解析结果。用户修改默认模型 SHALL NOT 改变正在执行的 run。

#### Scenario: 对话期间修改默认模型
- **WHEN** run 已启动后用户修改 `chat` 默认模型
- **THEN** 当前 run SHALL 继续使用启动时模型，下一次新 run SHALL 使用新的有效绑定

### Requirement: 设置控制面扩展 SHALL 保持聊天协议兼容
新增模型设置、运行记录、通知与诊断 SHALL NOT 改变 `/api/chat` 现有请求必填字段、SSE 事件集合和 assistant 骨架—检查点—终态单行落库状态机。

#### Scenario: 未配置用户模型绑定
- **WHEN** 用户没有任何用途绑定并发起现有聊天请求
- **THEN** 系统 SHALL 按平台/环境默认正常运行且前端无需新增 SSE 分支

### Requirement: Citation SHALL 由 Prompt 生成普通 Markdown 文本交付

系统 SHALL 通过共享 system prompt 要求 Agent 在普通 Markdown 回答中为 Web 和 KB 统一生成 `[n]` 引用及 `### 参考资料`，并作为普通 Markdown text part 经现有 `text-start`、`text-delta`、`text-end` 和 `finish` 交付。系统 SHALL NOT 使用 typed answer segment、structured `response_format`、虚拟 Tool、citation structured response、citation 专用终态文本或第二份 citation annotation。平台 MAY 解析已生成的 Markdown 编号并将其与本轮 retrieval 做确定性匹配，但 SHALL NOT 改写模型正文。

#### Scenario: 流式输出带 Markdown 引用的回答

- **WHEN** 模型逐 token 生成正文编号和参考资料列表
- **THEN** 客户端 SHALL 按原有 text delta 顺序展示
- **AND** 终态消息 SHALL 与流式正文完全一致

#### Scenario: 模型引用网页

- **WHEN** 回答使用 `web_search` 或 `web_fetch` 返回的事实
- **THEN** 模型 SHALL 在事实附近输出 `[n]`
- **AND** 参考资料的对应条目 SHALL 包含工具返回的原始 URL
- **AND** SHALL NOT 输出内部 evidence ID

#### Scenario: 工具没有提供来源

- **WHEN** 工具结果不包含可识别来源
- **THEN** 模型 SHALL NOT 编造引用
- **AND** MAY 明确说明依据不足

### Requirement: 平台 MAY 独立持久化 retrieval results

平台 MAY 使用独立 retrieval part 和 `retrieval-results-available` 交付工具来源，供恢复及来源抽屉展示。retrieval part SHALL NOT 声称其中每条结果都被最终答案引用。

#### Scenario: 刷新恢复研究回答

- **WHEN** 带 Markdown 引用的回答在生成中刷新
- **THEN** 普通 text snapshot SHALL 恢复已经生成的引用文本
- **AND** retrieval part SHALL 独立恢复

### Requirement: Retrieval results SHALL NOT 冒充 cited sources

平台 MAY 持久化工具返回的 retrieval results，并在回答末尾通过紧凑来源入口和来源抽屉展示，但 SHALL NOT 将 Top-K、score 或全部检索结果自动称为“引用”或“答案依据”。平台 SHALL NOT 解析模型 Markdown 反推 claim-to-source binding。

#### Scenario: 检索后模型没有引用

- **WHEN** 工具返回检索结果但最终正文没有引用
- **THEN** 正文 SHALL 保持模型原始输出
- **AND** retrieval results MAY 继续通过来源入口和来源抽屉展示

### Requirement: Retrieval results SHALL 使用统一来源抽屉

正文实际出现对应 `[n]`、全部参考资料条目均唯一匹配成功、流式正文已完成且该段之后没有其他正文时，客户端 SHALL 隐藏仅供绑定使用的 `### 参考资料` 段；否则 SHALL 保留原始 Markdown，并将连续参考资料条目分行展示。存在本轮 retrieval results 时，客户端 SHALL 在回答底部工具栏与 token 用量同行展示紧凑来源图标和去重后的来源文档数量，而不是独立的检索结果折叠块。点击入口 SHALL 打开“来源”抽屉，按“引用来源”和“其他检索结果”展示 Web 与 KB 来源；抽屉 SHALL 使用紧凑单行编号条目，只展示单行省略标题及域名或 Collection，不展示 excerpt 正文，并保留完整标题供 hover 查看；原始 Markdown SHALL 保持完整。

#### Scenario: 查看本轮全部来源

- **WHEN** 用户点击回答末尾的来源入口
- **THEN** 客户端 SHALL 打开来源抽屉
- **AND** Web 来源 SHALL 可安全打开原始 URL
- **AND** KB 来源 SHALL 在新标签页进入对应 Collection 文档并打开该文件的分片抽屉
- **AND** 未被正文引用的结果 SHALL 归入“其他检索结果”

### Requirement: 可点击引用 SHALL 来自本轮 retrieval

客户端 SHALL 解析正文 `[n]` 与参考资料条目，并使用 canonical URL（Web）或文件名与 Collection（KB）与已持久化的本轮 retrieval results 匹配。Web 展示标题 MAY 与 retrieval title 不同，不参与来源身份判断。只有唯一匹配成功的条目 SHALL 渲染为可点击上标。

#### Scenario: Web 编号匹配成功

- **WHEN** 参考资料的 URL canonicalize 后唯一匹配本轮 Web retrieval
- **THEN** 对应 `[n]` SHALL 渲染为可点击上标
- **AND** 点击 SHALL 打开当前回答的来源抽屉并滚动、高亮对应编号
- **AND** 点击抽屉条目 SHALL 使用安全外链策略打开原始 URL

#### Scenario: KB 编号匹配成功

- **WHEN** 参考资料的文件名、Collection 和可用 locator 唯一匹配本轮 KB retrieval
- **THEN** 对应 `[n]` SHALL 渲染为可点击上标
- **AND** 点击 SHALL 打开当前回答的来源抽屉并滚动、高亮对应编号
- **AND** 点击抽屉条目 SHALL 在新标签页进入受认证保护的对应 Collection 并打开该文件的分片抽屉

#### Scenario: 条目无匹配或多义

- **WHEN** 参考资料无法唯一匹配本轮 retrieval
- **THEN** 平台 SHALL NOT 生成可点击 citation
- **AND** 正文 SHALL 保持模型原始 Markdown

### Requirement: Citation 上标 SHALL 可确定性恢复

平台 SHALL 在同一 assistant message 中保存原始 Markdown text 和 retrieval parts。客户端 SHALL 使用这两类权威数据确定性重建同一编号、source type 和跳转目标；不持久化第二份答案或依赖流式时内存 annotation。

#### Scenario: 刷新带引用的已完成回答

- **WHEN** 客户端重新加载已完成的 assistant message
- **THEN** `[n]` SHALL 继续显示为一个可点击上标
- **AND** 点击目标 SHALL 与首次流式生成完成时一致

### Requirement: SSE SHALL 表达统一 Agent Stop Reason

RunEvent 到 `/api/chat` SSE 与 assistant 终态映射 SHALL 支持稳定的 Agent stop reason，至少覆盖 `context_exhausted`、`length_stop`、`safety_stop`、`partial_output`、`empty_after_tools`、`tool_loop_limit`、`tool_call_limit`、`subagent_concurrency_limit`、`subagent_total_limit` 与 `subagent_depth_limit`。新增 reason SHALL 作为兼容字段出现在 `finish`、`error` 或现行终态事件中；旧客户端忽略该字段时 SHALL 仍能完成消息收尾。

#### Scenario: length stop 保留正文

- **WHEN** 模型因长度限制结束且已经产生正文
- **THEN** assistant SHALL 保存已有正文并进入 partial 或现行等价非 completed 终态
- **AND** SSE 终态 SHALL 携带 `length_stop`

#### Scenario: 运行预算中间件停止仍可展示

- **WHEN** ToolLoopGuardMiddleware 因工具循环达到硬限制而停止
- **THEN** assistant SHALL 保留停止前的 reasoning、tool parts 与正文
- **AND** 终态 SHALL 携带 `tool_loop_limit`

### Requirement: 新 Run SHALL 一次性设置默认会话标题

`POST /api/chat` 下的 Run 创建入口 SHALL 在创建首条 user 消息、assistant 骨架与 run 的同一事务中，仅当会话标题仍精确等于默认值“新对话”时，根据首条非空用户可见文本设置规范化标题。创建响应 SHALL 返回服务端最终 `session_title`；前端 SHALL 立即更新当前页和会话列表，并在刷新后以会话 API 的标题为准。

#### Scenario: 首条消息设置标题

- **WHEN** 标题为“新对话”的会话成功创建首个 Run，用户文本为“你好”
- **THEN** 会话标题 SHALL 在同一事务中更新为“你好”
- **AND** 创建 Run 响应 SHALL 返回该标题

#### Scenario: 已改名会话不被覆盖

- **WHEN** 会话已有非默认标题且用户开始新一轮 Run
- **THEN** 服务端 SHALL 保留原标题
- **AND** 前端 SHALL 使用响应中的原标题，不得根据本轮问题覆盖

#### Scenario: Run 创建回滚

- **WHEN** user、assistant 或 run 任一写入失败导致事务回滚
- **THEN** 自动标题更新 SHALL 一并回滚
- **AND** 不得出现有标题但没有首轮消息的半成品会话

### Requirement: 会话消息 SHALL 使用服务端确定性序号

每条 `t_chat_message` SHALL 具有会话内唯一、严格递增的 `message_sequence`。所有消息写入口 SHALL 在锁定会话序号分配器的短事务中分配序号；同一 Run 的 user 与 assistant SHALL 连续分配，且 user 序号小于 assistant。`created_at` SHALL 仅表达时间，不再作为历史顺序权威。

#### Scenario: 同毫秒创建 user 与 assistant

- **WHEN** RunService 在同一毫秒预创建 user 和 assistant
- **THEN** user.message_sequence SHALL 等于 N
- **AND** assistant.message_sequence SHALL 等于 N+1

#### Scenario: 同会话并发写入

- **WHEN** 两个合法写入入口并发向同一会话保存消息
- **THEN** 系统 SHALL 分配不同且连续的 sequence
- **AND** 唯一约束 SHALL NOT 因竞态产生随机失败

### Requirement: 历史 API SHALL 按消息序号分页与返回

`GET /api/chat/sessions/{id}/messages` SHALL 按 `message_sequence ASC` 返回消息，并在每条消息中包含该字段。`before_id` cursor SHALL 解析目标消息的 sequence 并查询更小序号，不能继续使用 `created_at` 比较。当前 Web 前后端 SHALL 同版本升级；新响应缺少 sequence SHALL 作为协议错误处理，不保留第二套时间排序。

#### Scenario: 刷新后保持 user → assistant

- **WHEN** 用户完成一轮问答后刷新并重新加载历史
- **THEN** API SHALL 先返回该轮 user，再返回对应 assistant
- **AND** 前端 SHALL 按 sequence 稳定展示该顺序

#### Scenario: cursor 遇到相同 created_at

- **WHEN** cursor 消息与更早消息具有相同 created_at
- **THEN** API SHALL 仍依据 message_sequence 正确返回全部更早消息
- **AND** 不得漏掉同毫秒消息

### Requirement: 历史迁移 SHALL 建立可审计的稳定顺序

数据库迁移 SHALL 为已有消息一次性回填 `message_sequence`，保证会话内唯一，并保证具有 parent user 的 assistant 排在 parent 之后。迁移 SHALL 校验每会话数量、唯一性、parent 顺序与 `next_message_sequence=max+1`，不满足时 SHALL 中止而不是带缺陷上线。

#### Scenario: 旧 user 与 assistant 时间相同

- **WHEN** 旧数据中 assistant.parent_id 指向 user 且二者 created_at 相同
- **THEN** 回填后 assistant.message_sequence SHALL 大于 user.message_sequence

#### Scenario: 迁移校验失败

- **WHEN** 回填产生重复 sequence 或 parent 顺序倒置
- **THEN** 迁移 SHALL 失败并输出可定位的会话/消息标识
- **AND** NOT NULL 与唯一约束 SHALL NOT 在错误数据上提交

### Requirement: 工具卡片 SHALL 按权威 state 展示和恢复

chat 页 SHALL 按服务端 tool `state` 显示“正在执行、等待确认、已完成、执行失败、执行超时、已拒绝、已停止”等互斥状态。HITL 等待时流订阅保持 active 不得禁用授权控件；Run snapshot 或历史加载 SHALL replace 同一 assistant 的客户端 parts，纠正刷新前的旧状态。

#### Scenario: 日志已失败而旧界面仍运行中

- **WHEN** 服务端权威历史中某工具为 `state=failed`，客户端刷新前缓存为 `running`
- **THEN** 刷新恢复后工具卡片 SHALL 显示失败
- **AND** SHALL NOT 保留旧的“运行中”标签

#### Scenario: HITL 等待授权

- **WHEN** execute part 为 `approval_pending` 且 SSE subscription 仍 active
- **THEN** UI SHALL 显示“等待确认”
- **AND** 允许一次/本会话允许/拒绝控件 SHALL 可点击，除非授权结果正在提交

### Requirement: 工具失败且无回答时 SHALL 显示阻断态

前端 SHALL 从 assistant 结构化 tool states 派生无回答阻断态，不依赖模型自行判断。存在任意未成功工具，但在最后一个工具块之后仍有回答正文时 SHALL NOT 额外提示结果可能不完整；只有工具调用前的过程文本时仍 SHALL 视为没有最终回答，并显示阻断态与重试入口。单个工具详情 SHALL 保持可展开。

#### Scenario: 回答包含多个失败工具

- **WHEN** assistant 中有多个 failed/timed_out 工具且仍有正文
- **THEN** 回答级完整性提示 SHALL NOT 显示
- **AND** 每个失败工具卡片 SHALL 保留自己的状态和详情

#### Scenario: 工具失败且无回答

- **WHEN** 关键工具失败并且 assistant 没有可见正文
- **THEN** UI SHALL 告知本轮未完成
- **AND** SHALL 提供重新执行本轮的操作

### Requirement: 可靠 Web Agent Run SHALL 明确适用范围

typed RuntimeEventMapper、可靠 Run、多 Tab、snapshot 恢复和统一 Delivery 的演进与验收范围 SHALL 为 `COMMON_QA`、`FAULT_OPERATION_QA` 与 `SUPER_AGENT_QA`。

`TEST_CASE_QA`、CaseCoordinator、`phase-*`、test-case resume/export 不再纳入本能力的演进与验收范围。现有 Web 入口 MAY 继续使用相同 `run_id`、assistant identity 与订阅 API 承载该旧流程，并在 producer 边界把 CaseCoordinator 的旧 SSE 帧适配为 RunEvent；该兼容适配 SHALL 保持隔离，SHALL NOT 进入上述三种目标 Agent 共用的 RuntimeEventMapper，也 SHALL NOT 成为新增可靠性设计的约束。

#### Scenario: 测试用例生成不参与主路径验收

- **WHEN** 执行本能力的实现或验收
- **THEN** SHALL 只验收 `COMMON_QA`、`FAULT_OPERATION_QA` 与 `SUPER_AGENT_QA` 的 typed 主路径
- **AND** SHALL NOT 因 `TEST_CASE_QA` 的旧 SSE 形状向目标 Agent 的 RuntimeEventMapper 增加兼容 parser

### Requirement: 服务端 SHALL 提供权威 active Run 发现

系统 SHALL 提供 `GET /api/chat/sessions/{session_id}/active-run`，对已鉴权 owner 返回完整 RunSnapshot 或 `data=null`。未知、已删除或跨用户 session SHALL 返回 404 且不泄露 Run 身份。

#### Scenario: 新 Tab 发现正在执行的 Run

- **WHEN** Tab A 已启动 Run，Tab B 打开同一 session
- **THEN** Tab B SHALL 从 active-run 获取相同 `run_id`、`assistant_message_id`、status、snapshot_sequence 与 content
- **AND** SHALL NOT 依赖 Tab A 的 sessionStorage

### Requirement: 多 Tab SHALL 独立订阅同一 Run

同一用户的多个 Tab SHALL 使用独立 SSE subscription。断开、刷新或溢出任意一个 subscription SHALL 只移除自身，不取消 producer、Persistence 或其它 Delivery。

#### Scenario: 关闭创建 Run 的 Tab

- **WHEN** Tab A 与 Tab B 均订阅后关闭 Tab A
- **THEN** producer SHALL 继续，Tab B SHALL 收到权威终态

### Requirement: 客户端 SHALL 以 snapshot replace 和 sequence 连续性恢复

客户端收到 run-snapshot SHALL replace 相同 assistant 的 parts，并设置 last_sequence。业务 sequence 小于等于 last_sequence SHALL 忽略；等于 last_sequence+1 SHALL apply；大于 last_sequence+1 SHALL 停止 reader并进行 snapshot recovery。无终态 EOF SHALL NOT 触发成功或失败终态回调。

#### Scenario: sequence gap 不继续渲染

- **WHEN** last_sequence=20 而下一事件 sequence=23
- **THEN** 客户端 SHALL 丢弃该事件并进入 snapshot recovery

### Requirement: 同 session 创建冲突 SHALL 加入已有 Run

同一 session 已有 active Run 时，`POST /api/chat/runs` SHALL 返回 HTTP 409 和当前用户可访问的 `run_id`、`assistant_message_id`、`session_id`、status。客户端 SHALL 加入已有 Run，不启动第二 producer。

#### Scenario: 两个 Tab 同时发送

- **WHEN** 两个 Tab 对同一 session 并发创建 Run
- **THEN** 最多一个 producer SHALL 启动，另一个请求 SHALL 能加入已有 Run

### Requirement: stop 与 HITL resume SHALL 按 Run 鉴权且幂等

stop 与 HITL resume SHALL 按 `(run_id,current_user_id)` 鉴权并验证 session/assistant 关联。重复 stop SHALL 最多取消一次 producer并产生一个 terminal transaction；重复或过期 HITL 命令 SHALL NOT 启动第二 producer。旧 Run 命令 SHALL NOT 作用于同 session 的后续 Run。

#### Scenario: 旧 Tab 不能停止新 Run

- **WHEN** 旧 Tab 对已终态 R1 发 stop，而同 session 已有新 Run R2
- **THEN** R2 SHALL 不受影响
