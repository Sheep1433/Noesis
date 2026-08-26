# agent-runtime Specification

## Purpose

本能力规定 Agent **运行时**：文件系统与沙箱（宿主机 `.noesis/users/` 布局、Agent/Shell 共用的绝对路径坐标系 `/workspace`、`/skills/public|personal`、`/memory`、backend 工厂 docker / local_shell、Skills 只读挂载与用户 ZIP、用户记忆、web_search / web_fetch）、公共 Runtime 执行 Lifecycle（Context Lifecycle、Model Execution、Tool Execution、运行预算中间件）与 Token / 上下文可观测性。代码锚点：`packages/noesis-core/src/noesis/config/user_data_paths.py`、`packages/noesis-core/src/noesis/agents/backends/{paths,agent_path,memory,factory,docker_exec,local_shell}.py`、`packages/noesis-core/src/noesis/agents/middlewares/`。

## 路径命名

| 路径 | 含义 |
|------|------|
| `{REPO_ROOT}/.noesis/users/{user_id}/` | 用户数据根 |
| `.../sessions/{session_id}/workspace/` | 会话工作区（宿主机） |
| `.../sessions/{session_id}/uploads/` | 附件原文件 |
| `.../sessions/{session_id}/attachments/` | 附件 Markdown |
| `.../skills/` | 用户 Skills（跨会话） |
| `.../AGENTS.md` / `USER.md` | 用户记忆（跨会话） |
| 容器 `/workspace` | session workspace rw |
| 容器 `/skills/public` | 平台 Skills ro |
| 容器 `/skills/personal` | 用户 Skills ro |
| Agent `/memory/` | 记忆路由（**不**经沙箱默认挂载） |

**SHALL NOT** 再使用 filesystem 虚拟根 ``/notes.md`` 坐标系。
## Requirements
### Requirement: 用户数据根与会话子树

路径模块 SHALL 将用户根定为 `DATA_DIR / "users"`；`user_id` / `session_id` 拼入路径前 SHALL 校验段字符。用户根 MAY 随统一的 `DATA_DIR` 配置改变，但系统 **SHALL NOT** 为 `users` 单独提供另一套根目录配置。

会话子树 SHALL 含 `workspace/`、`uploads/`、`attachments/`。`delete_session_data` SHALL 删除整棵会话子树且幂等，**SHALL NOT** 删除用户级 `skills/`、`AGENTS.md`、`USER.md`。

#### Scenario: 工作区路径

- **WHEN** `get_workspace_dir("42", "sess-abc")`
- **THEN** 返回 `{REPO_ROOT}/.noesis/users/42/sessions/sess-abc/workspace`

#### Scenario: 非法 user_id

- **WHEN** `user_id` 含 `..` 或 `/`
- **THEN** SHALL 抛出 `ValueError`

### Requirement: Agent 路径唯一坐标系

Agent 文件工具与 Shell **SHALL** 使用同一绝对路径：`/workspace/...`、`/skills/public|personal/...`、`/memory/...`。

`paths.canonicalize_agent_path` SHALL：

- 将裸路径 / ``/notes.md`` 归一为 ``/workspace/notes.md``
- 将 UI ``sessions/{sid}/workspace/...`` 映射为 ``/workspace/...``
- 折叠多余 ``/workspace/workspace/...``
- 保持 ``/skills/...``、``/memory/...``

local 模式：`AgentPathBackend(strip_root=/workspace)` 对接宿主机 FilesystemBackend。docker 模式：default backend 为沙箱（skills 已在容器挂载），另挂 `/memory/` route。

**SHALL NOT** 对 `execute` 做 shlex 整命令路径 rewrite；**SHALL NOT** 改第三方 Skill 文案纠路径。

#### Scenario: 裸路径归一

- **WHEN** Agent `write_file("/notes.md", "x")`
- **THEN** 写入落在当前 session 宿主机 `workspace/notes.md`

#### Scenario: UI 路径注入映射

- **WHEN** mention 解析得到 `sessions/s1/workspace/a.md`
- **THEN** Agent 可见路径 SHALL 为 `/workspace/a.md`

### Requirement: 沙箱 backend 与挂载面

`sandbox.backend` SHALL 仅支持 `docker`（生产）与 `local_shell`（开发/测试）。**SHALL NOT** 支持已移除的 `aio`。

docker：每 `(user_id, session_id)` 容器；挂载仅为：

- session workspace → `/workspace`（rw）
- 公共 skills → `/skills/public`（ro）
- 个人 skills → `/skills/personal`（ro）

**SHALL NOT** 将整个 `users/{uid}/` rw 挂入容器。删 session **SHALL** 清理会话磁盘，容器生命周期由 runner idle / 显式回收管理；**SHALL NOT** 因删 session 必须立刻销毁无关用户资源以外的全局单例（无 user 级长驻 AIO）。

沙箱 env **SHALL NOT** 含业务 API 密钥（MAY 含 scoped `GH_TOKEN`）。

#### Scenario: Skills 只读

- **WHEN** `execute("echo x > /skills/personal/foo/SKILL.md")`
- **THEN** SHALL 失败；宿主机 personal Skills **SHALL NOT** 被改

#### Scenario: aio 配置拒绝

- **WHEN** `SANDBOX_BACKEND=aio`
- **THEN** 工厂 SHALL 抛出明确错误

### Requirement: execute 保留 Shell 语义

对 `execute` **SHALL NOT** 使用会破坏 `>`、`|`、`&&` 等的 shlex split/join 回写。

#### Scenario: 重定向

- **WHEN** `execute("printf done > /workspace/out.txt")`
- **THEN** 宿主机当前 session workspace 出现 `out.txt` 内容 `done`

### Requirement: handle 缓存失效重建

SandboxService 缓存仅为优化；runner 返回容器不存在时 backend SHALL 清缓存、ensure 并重试至少一次。

#### Scenario: idle 后恢复

- **WHEN** 容器已被 TTL 回收且仍有旧 handle，随后 `execute`
- **THEN** SHALL 重建沙箱并执行（或返回明确不可恢复错误）

### Requirement: Skills 文件系统 API

平台 Skills 根与用户 Skills ZIP 上传/树 API SHALL 由 skills 服务提供；Agent 侧权威路由为 `/skills/public/`、`/skills/personal/`（同名时 personal 优先）。**SHALL NOT** 再提供 `/skills/extensions`、`/skills/custom` 作为权威别名。

#### Scenario: 列表个人 skill

- **WHEN** 用户 ZIP 安装 skill 包后调用个人 skills 树 API
- **THEN** 响应 SHALL 含该包且磁盘位于 `users/{uid}/skills/`

### Requirement: 用户记忆 `/memory/`

`ensure_user_memory_files` SHALL seed `AGENTS.md` 与 `USER.md`。Agent 经 `/memory/` route（`memory.UserMemoryBackend`）读写；**SHALL NOT** 假设记忆文件出现在沙箱 `/workspace` 挂载中。

SuperAgent 装配 SHALL 注入记忆相关中间件/提示（见 `agent-profiles`）。

#### Scenario: 记忆不在 workspace ls

- **WHEN** docker 模式下 `execute("ls /workspace")`
- **THEN** **SHALL NOT** 默认列出用户根 `AGENTS.md` 作为 workspace 条目

#### Scenario: 经工具写 AGENTS.md

- **WHEN** Agent 写入 `/memory/AGENTS.md`（经 HITL 策略允许或审批后）
- **THEN** 宿主机 `users/{uid}/AGENTS.md` SHALL 更新

### Requirement: Web 工具

系统 SHALL 提供 `web_search` / `web_fetch`（Provider 可配置，如 Tavily / 本地 fetch）；密钥仅经配置注入应用侧，**SHALL NOT** 注入沙箱 env。

#### Scenario: 无 Key 时明确失败

- **WHEN** 未配置搜索 Provider 密钥且调用 web_search
- **THEN** SHALL 返回可理解的工具错误，而非空成功

### Requirement: Agent 上下文预览 SHALL 与真实装配共享解析器
运行时 SHALL 暴露不执行模型的上下文解析能力，供设置服务生成指定用户与 Agent profile 的来源清单和最终编译预览；预览与真实 run SHALL 共享记忆、规则和提示词解析器，SHALL NOT 在 API 层复制拼装规则。

#### Scenario: 预览不产生运行副作用
- **WHEN** 设置服务请求上下文预览
- **THEN** 运行时 SHALL NOT 调用模型、创建 checkpoint、写入 `/memory/` 或创建聊天消息

### Requirement: SuperAgent SHALL 提供用户记忆检索工具

SuperAgent SHALL 获得 `search_memory` 工具，支持查询词、可选时间范围、memory type、来源类型、状态、project scope、top_k 和是否展开证据。工具 SHALL 在运行时绑定当前 user_id 和授权 scope，模型不得指定或覆盖用户标识。结果 SHALL 只返回新的结构化机器经验，并显式标注 `source`、type、status、score、memory id、evidence status 和来源标识；默认 SHALL NOT 返回 candidate、needs_review、disabled、invalidated 或 superseded，只有用户明确要求查看未验证/历史记录时才可返回并标注状态。工具 SHALL NOT 搜索旧按日文件，也 SHALL NOT 返回整篇文件、整条 Run、reasoning、大工具输出、内部 provider provenance 或服务端路径。

当简单检索无法满足明确的历史、多跳、时间、workflow 或错误前提问题时，工具 MAY 调用受限 MemoryQueryService。该服务 SHALL 受当前用户/scope、只读工具、timeout、step、token、并发和 source-span 预算约束；SHALL 返回带 memory/source 引用的 Bulletin 或明确 abstain。

#### Scenario: Agent 跨会话检索机器经验
- **WHEN** 用户问题需要回忆其它会话、项目决策或历史任务经验且 Agent 调用 `search_memory`
- **THEN** 工具 SHALL 只返回当前用户和授权 scope 的精简结果、状态、分数和来源标识

#### Scenario: 默认排除未验证与历史状态
- **WHEN** Agent 未显式请求非 active 状态
- **THEN** 结果 SHALL 排除 candidate、needs_review、superseded、disabled 和 invalidated

#### Scenario: 深度检索返回证据包
- **WHEN** 问题需要多条 Run evidence 且 MemoryQueryService 成功
- **THEN** 结果 SHALL 包含短 Bulletin、memory ids、source spans 和 evidence status
- **AND** SHALL NOT 返回无界原始 trajectory

#### Scenario: 用户隔离
- **WHEN** 模型尝试通过查询参数访问其它用户或未授权 project scope
- **THEN** 工具 SHALL 忽略或拒绝该标识
- **AND** SHALL 只使用 Runtime 绑定的当前用户和 scope

### Requirement: SuperAgent SHALL 提供记忆来源读取工具

SuperAgent SHALL 获得 `get_memory_source` 工具，并在数据库/文件服务层校验来源、snapshot、session、message、tool、artifact 和 memory item 均属于当前用户与授权 scope。工具 SHALL 只返回被 item 引用的安全 Run spans、角色、结构化 outcome、产物/验证摘要和 source digest。内部 provider provenance、敏感参数、服务端路径、其它用户内容和未脱敏错误 SHALL NOT 返回；旧按日文件 SHALL NOT 作为来源类型继续兼容。

#### Scenario: Agent 追溯机器经验来源
- **WHEN** Agent 使用 memory/evidence id 请求来源
- **THEN** 工具 SHALL 返回支持该结论的有限 Run spans
- **AND** SHALL 明确区分用户输入、assistant 结论、工具 outcome、产物和验证

#### Scenario: 来源已删除或到期
- **WHEN** evidence 指向的消息/snapshot 已删除或大 payload 已按保留期清理
- **THEN** 工具 SHALL 返回来源不可用或仅剩 digest/metadata
- **AND** SHALL NOT 转换为未预期 500

#### Scenario: 越权来源请求
- **WHEN** 请求的来源不属于当前用户或授权 scope
- **THEN** 工具 SHALL 返回不存在或无权限且不得泄露来源内容

### Requirement: 公共 Runtime SHALL 按五类职责组织

系统 SHALL 将 ReAct Agent 的公共运行时职责限定为 Context Lifecycle、Model Execution、Tool Execution、运行预算中间件与 Runtime Telemetry，并 SHALL 以 LangChain `AgentMiddleware` lifecycle hook 作为 `create_agent` 的权威接入点。Middleware MAY 将纯计算、存储或 policy 委托给 runtime service，但同一状态与决策 SHALL 只有一个权威 owner；系统 SHALL NOT 绕开 LangChain Agent loop 再建第二套执行循环，也 SHALL NOT 为 length stop、empty terminal、tool output budget 等单点问题继续叠加相互独立且依赖顺序的补丁中间件。

#### Scenario: 工厂装配公共 Runtime

- **WHEN** `create_noesis_agent` 装配任一 ReAct Agent Profile
- **THEN** Agent SHALL 获得同一套公共 runtime middleware lifecycle
- **AND** Profile capability SHALL NOT 复制其中任一 owner 的状态机

#### Scenario: Middleware 委托内部 Service

- **WHEN** Context Lifecycle 或 Tool Execution 需要 artifact 存储、token 估算或 policy 计算
- **THEN** 对应 middleware MAY 调用无 Agent loop 控制权的内部 service
- **AND** continue/retry/stop 与 state update SHALL 仍由该 middleware hook 返回给 LangChain

### Requirement: Context Lifecycle SHALL 规范化并压缩模型上下文

Context Lifecycle SHALL 在模型请求前使用同一份最终 context snapshot 完成 tool call/output 配对修复、上下文预算判断与 compaction。Compaction SHALL 区分持久 history 与可从权威来源重新构造的 context source；压缩完成后 Skills、Memory、任务信息等长期上下文 SHALL 从来源重建，瞬时时间提示与调试信息 SHALL NOT 被固化进 summary。

#### Scenario: dangling tool call 后继续

- **WHEN** 恢复的 history 含没有对应 ToolMessage 的 tool call
- **THEN** Context Lifecycle SHALL 在请求 Provider 前补齐或剥离该不完整配对
- **AND** Provider SHALL NOT 因协议配对错误拒绝请求

#### Scenario: 压缩后重建长期上下文

- **WHEN** Agent 触发 compaction 后继续模型调用
- **THEN** 当前启用的 Skills、Memory 与任务上下文 SHALL 从权威来源重新加入最终请求
- **AND** 旧的动态提示 SHALL NOT 因 summary 被重复注入

#### Scenario: 压缩后仍超过窗口

- **WHEN** tool output 有界化和 compaction 后最终 ModelRequest 仍超过模型输入上限
- **THEN** Context Lifecycle SHALL 返回结构化 `context_exhausted` outcome
- **AND** SHALL NOT 将超限请求发送给 Provider

### Requirement: Model Execution SHALL 产生统一 Outcome

每次模型调用 SHALL 产生统一 model execution outcome，至少区分 `completed`、`retryable_error`、`length_stop`、`safety_stop`、`context_exhausted`、`partial_output` 与 `empty_after_tools`。模型重试 SHALL 仅发生在确认可重试且尚未产生用户可见输出、工具调用或 HITL 副作用时；达到边界后 SHALL 保留已有输出并终止，SHALL NOT 重放整个 step。

#### Scenario: 流开始前连接中断

- **WHEN** Provider 在产生可见 token 或 tool call 前返回可重试连接错误
- **THEN** Model Execution MAY 按配置 backoff 重试
- **AND** SHALL 发出可观测的 retry attempt 状态

#### Scenario: 已有文本后连接中断

- **WHEN** Provider 已产生用户可见文本后连接中断
- **THEN** Model Execution SHALL 返回 `partial_output`
- **AND** SHALL NOT 重试并产生重复文本

#### Scenario: 工具后模型空终态

- **WHEN** 当前 model step 的 request 末尾包含至少一个已结束工具结果，而后续模型调用没有正文、tool call 或 HITL 请求
- **THEN** Model Execution SHALL 通过只作用于本次 request 的瞬时收敛提示最多再次调用 model handler 一次
- **AND** SHALL NOT 将该提示写入 conversation state 或重放工具
- **AND** 若再次为空，SHALL 返回固定可见 fallback 并记录 `empty_after_tools`
- **AND** runtime SHALL NOT 静默完成或无限重试

### Requirement: Tool Execution SHALL 使用统一结果 Envelope

每次 in-scope 工具调用写入 Agent history 前 SHALL 归一为包含 `status`、`content` 和可选 `category`、`outcome` 的内部结果 envelope。超出配置预算的正文 SHALL 在工具返回边界被有界化。已挂载 DeepAgents `FilesystemMiddleware` 时 SHALL 优先采用其通用 tool-result offload；`ToolExecutionMiddleware` SHALL 将 offload 后的 ToolMessage 作为权威 content，SHALL NOT 解析第三方提示文本来伪造结构化 artifact metadata。未挂载 filesystem capability、工具被第三方 offload 排除或返回结果仍未有界时，Noesis SHALL 执行一次 fallback head/tail 截断；SHALL NOT 对已处理结果二次转存或等待整体 context 接近上限后才处理。

#### Scenario: 大工具结果写入 artifact

- **WHEN** 工具正文超过单结果预算且 Agent 具有当前 session filesystem backend
- **THEN** DeepAgents `FilesystemMiddleware` SHALL 优先将完整正文写入该 session 的 artifact 路径
- **AND** ToolMessage SHALL 使用 DeepAgents 生成的文件引用、省略说明与有界 preview

#### Scenario: 无 filesystem 的大结果

- **WHEN** 工具正文超过预算且当前 Agent 没有可写 artifact backend
- **THEN** ToolMessage SHALL 保留配置允许的头尾和明确省略标记
- **AND** SHALL NOT 将未限制的完整正文写入 history

#### Scenario: 已有第三方 offload 标记

- **WHEN** Tool Execution 收到已经包含 DeepAgents large-tool-result 路径和 preview 的 ToolMessage
- **THEN** SHALL 原样保留该有界 content
- **AND** SHALL NOT 从提示文本解析路径、再次截断 preview 或创建 Noesis artifact

### Requirement: 运行预算 SHALL 由独立 AgentMiddleware 实现

系统 SHALL 以独立 `AgentMiddleware`（如 `ToolLoopGuardMiddleware`、`SubagentLimitMiddleware`）实现运行预算，各中间件通过 `wrap_tool_call` / `after_model` 等 lifecycle hook 拦截，而非依赖 `run_id` 作用域的集中式预算控制器。所有限制 SHALL 产生稳定 stop reason；主 Agent 与子 Agent SHALL 使用同一预算模型，子 Agent 的本地限制 MAY 更严格但 SHALL NOT 绕过父 run 的总预算。

#### Scenario: 子 Agent 并发槽位耗尽

- **WHEN** 活跃子 Agent 数已达到配置上限且模型再次委派
- **THEN** SubagentLimitMiddleware SHALL 拒绝新委派并返回稳定的 `subagent_concurrency_limit` reason
- **AND** 已运行子 Agent SHALL 不受影响

#### Scenario: 重复工具循环

- **WHEN** 同一 run 的工具调用在配置窗口内达到循环硬限制
- **THEN** ToolLoopGuardMiddleware SHALL 停止继续调用工具并返回 `tool_loop_limit`
- **AND** 最终响应 SHALL 保留停止前已有结果

**Token 与上下文可观测性**

### Requirement: Runtime Telemetry SHALL 观察而不改变执行决策

Runtime Telemetry SHALL 消费 model、context、tool、subagent 与 compaction 事件，并按 model run id 去重；它 SHALL NOT 单独维护会影响控制流的第二套预算或循环计数。

#### Scenario: telemetry 关闭

- **WHEN** context display 或外部 tracing 关闭
- **THEN** Agent 的重试、压缩、工具治理和终止行为 SHALL 与开启时一致

### Requirement: Agent Runtime SHALL 按 Run 注入稳定的 Memory Bulletin

启用经验记忆的主 Agent profile SHALL 在新 `run_id` 的 `before_agent` 边界执行 fast memory retrieval，并将有界 Bulletin、memory ids、source snapshot 和 `run_id` 写入 LangGraph private state。RunService SHALL 将 `run_id`、user id、agent profile 和 project key 显式传递至 agent factory/middleware；middleware SHALL NOT 从 session id 或模型文本推断。相同 Run 的模型调用和 HITL resume SHALL 复用逐字节相同的自动块，新 Run SHALL 刷新；该 private state SHALL NOT 自动复制给 subagent。

Bulletin SHALL 只由当前用户/scope 的 active、有效、有合格 provenance/source evidence 且达到冻结相关性阈值的 item 构成。fast path SHALL 不额外调用生成模型；任一依赖失败 SHALL 零注入并继续 Run，不得用 raw、candidate、needs_review、过期或跨 scope 内容兜底。

#### Scenario: 同一 Run 自动块稳定
- **WHEN** 同一 Run 内发生多次模型调用
- **THEN** 每次请求中的自动 Memory Bulletin SHALL 逐字节相同

#### Scenario: HITL 跨进程恢复
- **WHEN** Run 进入 HITL pending 并从 checkpoint 在另一进程恢复
- **THEN** middleware SHALL 复用 checkpoint private state 中的原 Bulletin

#### Scenario: subagent 不继承自动块
- **WHEN** 主 Agent 创建 subagent
- **THEN** Memory Bulletin private state SHALL NOT 因通用状态复制而自动传入 subagent

#### Scenario: 记忆依赖降级
- **WHEN** embedding、workspace、Qdrant 或 PostgreSQL memory 查询不可用
- **THEN** Runtime SHALL 零注入并继续 Agent Run
- **AND** SHALL NOT 放宽状态、scope 或 provenance 门槛

#### Scenario: 用户关闭开关
- **WHEN** 用户关闭经验记忆并开始新 Run
- **THEN** Runtime SHALL 不执行自动检索或注入
- **AND** `search_memory`、`get_memory_source` 和治理操作 SHALL 保持可用

### Requirement: MemoryQueryService SHALL 使用独立只读运行边界

MemoryQueryService SHALL 与主 Agent 的业务工具分离，只装配 manifest/item/Run span/artifact summary 只读工具，并显式禁止外部网络、远程 MCP、业务写工具、shell 写入、跨用户路径和递归 memory capture。Service SHALL 对每次查询记录 duration、step count、token usage、returned spans、evidence status 和失败分类；trace SHALL 脱敏并按保留期清理。

#### Scenario: 查询 Controller 尝试调用业务工具
- **WHEN** MemoryQueryService 模型请求未注册的业务写工具或外部工具
- **THEN** Runtime SHALL 拒绝该工具调用
- **AND** SHALL NOT扩大当前授权或继续以该输出作为证据

#### Scenario: 查询无证据
- **WHEN** 只读检索在预算内未找到支持当前问题的来源
- **THEN** Service SHALL 返回 `insufficient`/abstain
- **AND** SHALL NOT 根据相似摘要编造结论

### Requirement: Runtime SHALL 保护稳定 Prompt 前缀的上下文缓存

PromptAssembler SHALL 保持稳定 system/developer instructions、工具 schema 和可缓存历史前缀位于自动 Memory Bulletin 之前，并将 Bulletin 作为单一 late context segment 放在稳定前缀之后、当前用户输入/本轮新增内容之前。Bulletin SHALL 使用 canonical serialization：稳定排序、固定字段顺序/空白/转义，且模型可见文本 SHALL NOT 包含当前时间、当前 `run_id`、source run/span、evidence count、last verified time 或随机值。相同 `bulletin_hash` SHALL 生成逐字节相同文本；真实 memory 内容变化时 SHALL 生成新 hash，不得为了 cache 保留 stale 内容。

`run_id`、source snapshot/span 和动态治理字段 SHALL 只保存在 private metadata，通过来源工具展开。显式 Deep Query SHALL 作为后续 tool result 追加，不改写稳定 system prompt 或冻结 Bulletin。Runtime SHALL 保持上述稳定前缀以支持 provider 的自动 prefix cache；系统 SHALL NOT 注入 provider 专用 cache breakpoint 标记（当前无显式缓存 provider 诉求），也不得因此增加用户开关。Runtime SHALL 记录 provider 可得的 cache-read、cache-write、uncached input 和 TTFT；provider 不返回时 SHALL 记录 unknown 而非 0。

#### Scenario: 同 Run 后续调用复用 Bulletin
- **WHEN** 同一 Run 的后续模型调用使用相同 `bulletin_hash`
- **THEN** 模型可见 Bulletin SHALL 逐字节相同
- **AND** Runtime SHALL 保留稳定 prefix 以允许 provider cache reuse

#### Scenario: 新 Run 内容未变化
- **WHEN** 新 Run 检索出与上一 Run 相同的 memory items 和可见字段
- **THEN** canonical serializer SHALL 产生相同 `bulletin_hash` 和文本
- **AND** SHALL NOT 因新 `run_id` 或当前时间制造 cache miss

#### Scenario: Memory 内容真实变化
- **WHEN** active statement、applicability 或 verification label 发生变化
- **THEN** 系统 SHALL 生成新的 Bulletin/hash
- **AND** SHALL 优先保证内容正确而非复用旧 cache

#### Scenario: Provider 不返回 cache 指标
- **WHEN** 模型响应没有 cache token details
- **THEN** telemetry SHALL 标记 cache metrics unavailable
- **AND** SHALL NOT 把缺失值计为零缓存命中

#### Scenario: Provider 要求显式 cache breakpoint
- **WHEN** 当前模型适配器声明支持且要求显式 prompt cache breakpoint
- **THEN** Runtime SHALL 在稳定前缀末端设置 provider-native breakpoint
- **AND** 动态 Bulletin、当前用户输入和后续 tool result SHALL 位于 breakpoint 之后

