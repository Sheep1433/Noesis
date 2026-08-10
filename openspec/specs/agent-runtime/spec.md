# agent-runtime Specification

## Purpose

本能力规定 Agent **运行时**：文件系统与沙箱（宿主机 `.noesis/users/` 布局、Agent/Shell 共用的绝对路径坐标系 `/workspace`、`/skills/public|personal`、`/memory`、backend 工厂 docker / local_shell、Skills 只读挂载与用户 ZIP、用户记忆、web_search / web_fetch）、公共 Runtime 执行 Lifecycle（Context Lifecycle、Model Execution、Tool Execution、Run Governor）与 Token / 上下文可观测性。代码锚点：`packages/noesis-core/src/noesis/config/user_data_paths.py`、`packages/noesis-core/src/noesis/agents/backends/{paths,agent_path,memory,factory,docker_exec,local_shell}.py`、`packages/noesis-core/src/noesis/agents/middlewares/`。

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

### Requirement: L2 记忆查询 SHALL 保持用户路径隔离
运行时或记忆服务 SHALL 只在当前用户权威记忆目录内列出和搜索 L2 日记，规范化并校验日期/相对路径；查询结果 SHALL NOT 改变 L0/L1 默认注入规则。

#### Scenario: 路径穿越查询
- **WHEN** L2 查询参数试图越出当前用户记忆根
- **THEN** 系统 SHALL 拒绝请求且 SHALL NOT 读取其它用户或宿主文件

### Requirement: SuperAgent SHALL 提供用户记忆检索工具

SuperAgent SHALL 获得 `search_memory` 工具，支持查询词、可选日期范围、分类和 top_k。工具 SHALL 在运行时绑定当前 user_id，模型不得指定或覆盖用户标识，返回内容 SHALL 为精简 L2 条目而非整篇文件。

#### Scenario: Agent 跨会话检索
- **WHEN** 用户问题需要回忆其他会话的信息且 Agent 调用 search_memory
- **THEN** 工具 SHALL 只返回当前用户匹配的记忆摘要、日期、分数和来源标识

### Requirement: SuperAgent SHALL 提供记忆来源读取工具

SuperAgent SHALL 获得 `get_memory_source` 工具，并在数据库层校验 session 和 message 均属于当前用户。工具 SHALL 限制相邻消息数量并排除 reasoning 与工具原始输出。

#### Scenario: Agent 追溯来源
- **WHEN** Agent 使用搜索结果的 session_id/message_id 请求来源
- **THEN** 工具 SHALL 返回有限的可见文本上下文

#### Scenario: 越权来源请求
- **WHEN** 请求的来源不属于当前用户
- **THEN** 工具 SHALL 返回不存在或无权限且不得泄露来源内容

## 运行时执行 Lifecycle

### Requirement: 公共 Runtime SHALL 按五类职责组织

系统 SHALL 将 ReAct Agent 的公共运行时职责限定为 Context Lifecycle、Model Execution、Tool Execution、Run Governor 与 Runtime Telemetry，并 SHALL 以 LangChain `AgentMiddleware` lifecycle hook 作为 `create_agent` 的权威接入点。Middleware MAY 将纯计算、存储或 policy 委托给 runtime service，但同一状态与决策 SHALL 只有一个权威 owner；系统 SHALL NOT 绕开 LangChain Agent loop 再建第二套执行循环，也 SHALL NOT 为 length stop、empty terminal、tool output budget 等单点问题继续叠加相互独立且依赖顺序的补丁中间件。

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

### Requirement: Run Governor SHALL 统一运行预算

Run Governor SHALL 以 `run_id` 为作用域维护模型调用、工具调用、重复调用窗口、子 Agent 活跃数/总数/深度及可选累计 token 预算。所有限制 SHALL 产生稳定 stop reason；主 Agent 与子 Agent SHALL 使用同一预算模型，子 Agent 的本地限制 MAY 更严格但 SHALL NOT 绕过父 run 的总预算。

#### Scenario: 子 Agent 并发槽位耗尽

- **WHEN** 活跃子 Agent 数已达到配置上限且模型再次委派
- **THEN** Run Governor SHALL 拒绝新委派并返回稳定的 `subagent_concurrency_limit` reason
- **AND** 已运行子 Agent SHALL 不受影响

#### Scenario: 重复工具循环

- **WHEN** 同一 run 的工具调用在配置窗口内达到循环硬限制
- **THEN** Run Governor SHALL 停止继续调用工具并返回 `tool_loop_limit`
- **AND** 最终响应 SHALL 保留停止前已有结果

#### Scenario: token attribution 尚不可用

- **WHEN** runtime 无法获得去重后的实际 Provider usage
- **THEN** Run Governor SHALL 不启用实际累计 token 硬限制
- **AND** MAY 继续记录估算 context occupancy，但 SHALL NOT 将其标记为实际 run cost

## Token 与上下文可观测性

### Requirement: Runtime Telemetry SHALL 观察而不改变执行决策

Runtime Telemetry SHALL 消费 model、context、tool、subagent、compaction 与 governor outcome，并按 model run id 去重；它 SHALL NOT 单独维护会影响控制流的第二套预算或循环计数。

#### Scenario: telemetry 关闭

- **WHEN** context display 或外部 tracing 关闭
- **THEN** Agent 的重试、压缩、工具治理和终止行为 SHALL 与开启时一致

### Requirement: 当前上下文快照 SHALL 基于最终模型请求

系统 SHALL 在每次模型调用前基于所有 Agent middleware 处理完成后的最终 `ModelRequest` 生成当前上下文快照。快照 SHALL 至少包含当前估算 token、模型上下文上限、占用比例，以及 system、conversation、tool results、tool definitions、other 顶层分类；快照 SHALL 表示单次即将发送的请求，SHALL NOT 累加为 run usage。

#### Scenario: 工具定义计入当前上下文

- **WHEN** 最终模型请求包含对话消息和已绑定工具定义
- **THEN** `current_tokens` SHALL 覆盖消息与工具定义
- **AND** breakdown SHALL 单独提供 `tool_definitions`

#### Scenario: 多次模型调用只更新当前快照

- **WHEN** 同一 run 依次进行两次模型调用
- **THEN** 当前 context 展示 SHALL 使用最后一次调用的快照
- **AND** SHALL NOT 将两次 `current_tokens` 相加

### Requirement: 上下文来源细分 SHALL 依赖可靠 provenance

系统 SHALL 支持 Skills、memory、RAG、attachments 等来源细分。来源注入方 SHALL 使用不进入 Provider 输入的内部 provenance 标记实际注入内容；统计器 SHALL 仅根据显式标记或已解析的权威工具路径归属来源，SHALL NOT 通过任意正文正则猜测。缺少可靠标记的内容 SHALL 保留在顶层分类或 `other`。

#### Scenario: Skills 列表被标记

- **WHEN** Skills middleware 将可用 Skills 列表注入最终 system message 并提供 provenance
- **THEN** 快照 SHALL 在 system 总量内报告 `sources.skills`
- **AND** 分类总量 SHALL NOT 因同一内容同时属于 system 与 Skills 而重复计入 `current_tokens`

#### Scenario: 未标记工具结果安全降级

- **WHEN** ToolMessage 没有可验证的来源标记
- **THEN** 其 token SHALL 计入 `tool_results`
- **AND** 系统 SHALL NOT 猜测其属于 RAG、Skills 或 attachments

### Requirement: 上下文分类 SHALL 明确估算语义

系统 SHALL 将本地上下文分类标记为估算，并记录可用的计数方法。分类 SHALL 使用一致的计数路径；本地序列化或 framing 差值 SHALL 进入 `other` 或等价未归属字段。系统 SHALL NOT 按比例改写分类以冒充 Provider 实际 input usage。

#### Scenario: Provider input 与本地估算不同

- **WHEN** Provider 返回的 `input_tokens` 与本地 `current_tokens` 不一致
- **THEN** 系统 SHALL 保留两个原始值及其不同语义
- **AND** SHALL NOT 强制修改各分类使二者相等

### Requirement: Provider usage SHALL 保留可用明细

系统 SHALL 规范化每次模型响应的 input、output、total token，并在 Provider 提供时保留 cache read、cache write、reasoning 等 detail。缺失的 detail SHALL 表示为不可用，SHALL NOT 默认伪造为零。detail SHALL 作为后端规范化与按需调试展示字段保留，SHALL NOT 作为 chat 页默认 token 摘要展示项。

#### Scenario: Responses API 返回 cache 与 reasoning

- **WHEN** Provider usage 含 cached input tokens 与 reasoning output tokens
- **THEN** 规范 usage SHALL 保留对应 `input_token_details` 与 `output_token_details`
- **AND** total token SHALL NOT 因 detail 再次重复相加
- **AND** chat 页默认摘要 SHALL NOT 展示 cache/reasoning，仅按需调试视图可读取

#### Scenario: Provider 只返回基础 usage

- **WHEN** Provider 只返回 input、output 和 total
- **THEN** 基础 usage SHALL 正常展示
- **AND** cache/reasoning SHALL 显示不可用或省略

### Requirement: Run usage SHALL 按 caller 和模型调用归属

系统 SHALL 将一轮 Agent run 的实际 Provider usage 按唯一 model run id 去重累计，并至少支持 `lead_agent`、`subagent`、`middleware` caller。系统 SHALL 支持按模型汇总，并为调试保留有界 step attribution；子 Agent usage SHALL 只计入 run 总量一次。

#### Scenario: 主 Agent 与子 Agent 分别调用模型

- **WHEN** 一轮 run 中主 Agent 和子 Agent 各完成一次模型调用
- **THEN** run cumulative SHALL 等于两次实际 usage 之和
- **AND** `by_caller` SHALL 分别报告 `lead_agent` 与 `subagent`

#### Scenario: 重复模型完成事件

- **WHEN** 同一 model run id 因流式与终态事件被观察两次
- **THEN** usage SHALL 只累计一次

#### Scenario: 调试步骤数量受限

- **WHEN** 长时间 Agent run 产生大量模型与工具事件
- **THEN** step attribution SHALL 按配置上限或语义完成事件有界保存
- **AND** SHALL NOT 按每个 token delta 生成 attribution 记录

### Requirement: 内部 attribution 元数据 SHALL NOT 进入模型输入

用于 token 来源和 caller 归属的 Noesis 内部元数据 SHALL 保持 request/run scoped，并在 Provider wire payload 生成前被剥离或位于不可序列化的内部上下文。该元数据 SHALL NOT 改变 prompt 文本、tool schema 或 prompt cache key。

#### Scenario: 检查 Provider 请求载荷

- **WHEN** 带 Skills、memory 和 RAG provenance 的请求被序列化给 Provider
- **THEN** wire payload SHALL 只包含原有模型输入
- **AND** SHALL NOT 出现 Noesis attribution 调试字段
