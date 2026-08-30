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

### Requirement: L2 记忆查询 SHALL 保持用户路径隔离
运行时或记忆服务 SHALL 只在当前用户权威记忆目录内列出和搜索 L2 日记，规范化并校验日期/相对路径；查询结果 SHALL NOT 改变 L0/L1 默认注入规则。

#### Scenario: 路径穿越查询
- **WHEN** L2 查询参数试图越出当前用户记忆根
- **THEN** 系统 SHALL 拒绝请求且 SHALL NOT 读取其它用户或宿主文件

### Requirement: Model Execution SHALL 产生统一 Outcome

模型重试、Provider finish reason 与 delivery stop reason SHALL 保持统一语义。瞬时 Provider attempt 重试 SHALL 沿用同一份已经完成 canonicalization、compaction 与预算校验的 request；每个 attempt SHALL 单独计数且可观测。重试 SHALL 由 Provider SDK 的 HTTP 层负责（在流式 body 开始前根据状态码决定），middleware 层不重复实现可见输出检测。需要改变 messages 的收敛继续 SHALL 重新经过完整 Agent lifecycle，不得直接调用内部 handler。

#### Scenario: 工具后模型空终态

- **WHEN** 工具结束后的模型响应没有正文、tool call 或 HITL 请求
- **THEN** 系统 SHALL 结束为可诊断状态，或通过可计数的完整 Agent lifecycle继续一次
- **AND** SHALL NOT 在内部 handler 中发起不可观测的额外模型调用

### Requirement: Tool Execution SHALL 使用统一结果 Envelope

每次工具调用写入 history 前 SHALL 保持 `status`、`content` 和可选 `category`、`outcome` 的稳定语义。调用异常翻译、command outcome 与输出有界化 SHALL 按顺序处理且不得互相改写语义。大结果 SHALL 优先在工具返回源或已配置的 artifact backend 有界化；已经处理的结果 SHALL NOT 被再次转存或截断。

#### Scenario: 已有 Artifact 引用的大结果

- **WHEN** 工具结果已经包含可读取的 artifact 引用与有界 preview
- **THEN** 后续处理 SHALL 原样保留该 content
- **AND** SHALL NOT 再次转存、截断或从提示正文猜测 metadata

#### Scenario: Typed Tool Failure

- **WHEN** 工具抛出可分类调用异常
- **THEN** 系统 SHALL 生成同 tool call id 的 error ToolMessage
- **AND** 输出预算处理 SHALL NOT 把 error 改写为 success

#### Scenario: 取消后恢复

- **WHEN** 工具因整轮取消而未产生 ToolMessage，随后同一会话恢复并准备再次调用模型
- **THEN** canonicalization SHALL 在 Provider 请求前补齐或移除该不完整配对
- **AND** 终止中的取消异常 SHALL NOT 被普通工具错误处理吞掉

### Requirement: 运行预算 SHALL 由独立 AgentMiddleware 实现

系统 SHALL 以独立 `AgentMiddleware`（如 `ToolLoopGuardMiddleware`、`SubagentLimitMiddleware`）实现运行预算，各中间件通过 lifecycle hook 拦截，而非依赖集中式预算控制器。所有限制 SHALL 产生稳定 stop reason；主 Agent 与子 Agent SHALL 使用同一预算模型。

系统 SHALL 只启用具有真实配置、正确 platform run identity、生产记账入口和确定性测试的运行限制。未配置或没有真实数据源的限制 SHALL 不得被描述为已生效。父子 Agent 共享预算只有在子任务 admission、释放、取消和恢复均可追踪时才 SHALL 启用。

#### Scenario: 未配置的预算轴

- **WHEN** 子 Agent 并发、深度或 token budget 没有生产配置或记账入口
- **THEN** runtime SHALL 不宣称该限制已生效
- **AND** inventory 与用户事件 SHALL 不产生对应 stop reason

#### Scenario: Run 级限制隔离

- **WHEN** 同一 session 发起两个不同 platform run
- **THEN** run-level counter SHALL 使用各自 run identity
- **AND** SHALL NOT 从上一 run 的 checkpoint 继承本轮计数

### Requirement: Runtime Telemetry SHALL 观察而不改变执行决策

模型 usage、上下文占用、finish reason、tool/subagent 归属与 compaction event SHALL 由唯一观测链消费。没有消费者的 observer SHALL 不参与运行；观测开关关闭时 retry、compaction、tool handling 与终止语义 SHALL 保持不变。

#### Scenario: 无外部观测消费者

- **WHEN** runtime 没有配置 tracing 或 telemetry sink
- **THEN** Agent SHALL 不执行无输出的重复观测路径
- **AND** `/api/chat` 的 usage、context 与完成收尾 SHALL 正常工作

### Requirement: 公共 Runtime SHALL 按 Lifecycle 边界归属职责

公共 Runtime SHALL 保持单一 Agent loop。跨场景策略只有在必须拦截明确 lifecycle seam、能在该 seam 内完整表达且不会与其它组件维护同一控制状态时，才 SHALL 进入 middleware。上下文来源、场景装配、持久化、运行观测和资源生命周期 SHALL 各有唯一 owner；系统 SHALL NOT 因统一命名而要求所有 ReAct Agent 装配固定数量的自定义 middleware。

#### Scenario: 同一决策只有一个 Owner

- **WHEN** Agent 执行 retry、compaction、tool failure 转换或调用限制
- **THEN** 每项决策 SHALL 只有一个组件维护控制状态
- **AND** SHALL NOT 由两个可独立开关的组件重复计数或重复处理

#### Scenario: Context 来源不依赖隐藏状态

- **WHEN** 场景准备 system prompt、用户输入、附件或 Skills/Memory 路径
- **THEN** SHALL 通过 `create_noesis_agent()` 的直接参数传入
- **AND** SHALL NOT 依赖另一个 middleware 的隐藏 `ContextVar` 才能得到结果

### Requirement: Context Management SHALL 实现 Claude Code 式分层策略

场景 prompt、用户输入和附件 SHALL 在调用 Agent 前准备。稳定来源 SHALL 与 conversation 分离；压力处理 SHALL 按 tool-result replacement、snip、micro-compaction、conversation compaction 和 reactive overflow recovery 的顺序进行。LangChain/DeepAgents 能力只在行为契约符合时 SHALL 直接采用；缺失时 SHALL 由 Noesis 的窄 middleware 或 runtime adapter 补足。

每次模型调用 SHALL 只有一份 canonical request。可从当前权威源重建的稳定内容 SHALL NOT 被固化进 conversation summary。

Compaction SHALL 按最终 request 预算判断，预算至少覆盖 system instructions、conversation、tool results 与 tool definitions。淘汰的 history SHALL 在摘要替换前具有可恢复记录；摘要失败 SHALL NOT 以错误文本不可逆替换原 history。

#### Scenario: Preview 是配置预览

- **WHEN** 设置服务预览某用户与 Agent Profile 的上下文配置
- **THEN** preview SHALL 展示场景 prompt 及配置的 Skills/Memory 来源
- **AND** SHALL NOT声称等于最终 Provider request，也不得调用模型、创建 checkpoint或写入数据

#### Scenario: 最终 Request 触发压缩

- **WHEN** conversation history 单独未达到阈值，但稳定指令或 tool definitions 加入后达到 compaction threshold
- **THEN** 系统 SHALL 在发送模型前执行 compaction
- **AND** SHALL NOT 仅因 history 计数较小而直接进入超限终态

#### Scenario: 压缩后重建稳定内容

- **WHEN** history 被 summary 与 preserved tail 替代
- **THEN** 当前场景指令、Skills、Memory、工具定义与动态时间 SHALL 由各自 owner 保持可用
- **AND** preserved tail 已包含的 conversation 内容 SHALL NOT 重复注入

#### Scenario: Tool Pair 不可切断

- **WHEN** canonicalization 或 compaction 处理 tool call、invalid tool call、tool result 或 thinking block
- **THEN** 下一次模型请求 SHALL 保持 Provider 接受的配对与顺序
- **AND** SHALL NOT 从关联 call/result 中间切断 preserved tail

### Requirement: 当前上下文快照 SHALL 基于最终模型请求

系统 SHALL 在 compaction 决策与诊断时基于最终 canonical request 计算本地估算，覆盖 system、conversation、tool results、tool definitions 与未归属 framing。该估算 SHALL 与 Provider 实际 usage 明确区分；用户可见 `context-update.current_tokens` 继续使用 Provider 最近一次实际 `input_tokens`。

#### Scenario: Tool Definitions 计入预算

- **WHEN** 最终模型请求包含动态绑定的工具定义
- **THEN** pre-call context estimate SHALL 覆盖这些定义
- **AND** compaction threshold SHALL NOT 只统计 conversation messages

#### Scenario: Provider Usage 与本地估算并存

- **WHEN** Provider 返回的实际 `input_tokens` 与本地估算不同
- **THEN** 系统 SHALL 保留两者的不同语义
- **AND** SHALL NOT修改本地 breakdown 冒充 Provider 实际值

### Requirement: Compaction SHALL 预留空间并可恢复失败

系统 SHALL 根据模型输入上限、摘要输出预留与瞬时 request buffer 计算自动压缩阈值；具体数值 SHALL 来自模型配置和运行数据。Summary 调用 SHALL 禁用业务工具并防止递归 compaction。Summary request 自身超限时 SHALL 按完整 API round 移除最旧前缀并执行有界重试。连续失败 SHALL 有有界计数和熔断，失败时原始 history 与 archive SHALL 保持可恢复。

#### Scenario: Summary 调用隔离

- **WHEN** runtime 生成 conversation summary
- **THEN** summary model request SHALL 不包含业务工具
- **AND** summary 调用 SHALL NOT 再触发自动 compaction

#### Scenario: Summary 返回错误文本

- **WHEN** summary model 返回空内容或框架错误标记文本
- **THEN** compaction SHALL 将本次摘要判定为失败且不得发布新的 summary state
- **AND** raw history 与已写 archive SHALL 保持可恢复

#### Scenario: 连续压缩失败

- **WHEN** 自动 compaction 连续达到配置失败上限
- **THEN** 系统 SHALL 停止本轮自动重试并返回稳定可诊断 reason
- **AND** SHALL NOT 删除 raw history 或进入无限模型调用

#### Scenario: Summary Request 自身超限

- **WHEN** summary model 返回 prompt-too-long
- **THEN** compaction SHALL 按完整 API round 移除最旧前缀并在配置上限内重试
- **AND** SHALL NOT 切断 tool call/result，也 SHALL NOT 无限重试

#### Scenario: 手动压缩不受自动熔断影响

- **WHEN** 自动 compaction 因连续失败进入熔断
- **THEN** 系统 SHALL 停止自动尝试
- **AND** 已启用的手动 compact 入口 SHALL 仍可触发一次显式压缩

#### Scenario: 压缩事务失败

- **WHEN** archive、summary、boundary、stable-source refs 或 checkpoint 任一步失败
- **THEN** 新 compact state SHALL NOT 发布
- **AND** history、file state 与 discovered tools SHALL 保持压缩前的可恢复状态

### Requirement: Stable Context Sources SHALL 支持 Revision 与压缩后重建

每个来源 SHALL 由自己的 owner 定义 freshness：Skills 使用用户 revision，Memory 在顶层 invocation 刷新，tool catalog 使用 catalog hash，attachments 每轮解析，场景动态信息由 DynamicContext 提供。同一 run 内来源 SHALL 保持稳定；变更时 SHALL 只失效对应来源。

#### Scenario: Skill 在两个 Turn 之间变更

- **WHEN** Skill 在上一个 turn 结束后被安装、删除或更新
- **THEN** 下一个 turn SHALL 刷新 Skills private cache 并生成新 revision
- **AND** 已在运行的当前 turn SHALL NOT 在中途突然改变 prompt

### Requirement: Effective History Reduction SHALL 保留 Raw Transcript

Tool-result replacement 与 Snip SHALL 产生可持久化、可重放的 effective-history projection，不得物理删除 raw transcript。每个 replacement SHALL 记录原内容 hash、原因、artifact/reference 和 token 变化。Tool Result micro-compaction SHALL 由 ToolResultBudget 承担，不得存在第二个同义 middleware。

#### Scenario: Checkpoint Resume 重放局部减重

- **WHEN** 同一 run 从 checkpoint 恢复
- **THEN** 有效 history SHALL 重放与恢复前相同的 replacement/snip 决策
- **AND** raw transcript SHALL 仍可用于诊断与重新压缩

### Requirement: Tool Registry 与 Deferred Filter SHALL 对大型 Schema 延迟加载

基础工具与已发现工具 SHALL 在最终 request 中可用；超过预算阈值的 MCP/extension 工具 SHALL 默认 deferred，并通过 tool search 加入 discovered set。工具已发现 SHALL NOT 等于获得执行权限。

#### Scenario: MCP Catalog 超过预算

- **WHEN** 全部 MCP tool schemas 超过配置预算
- **THEN** 未发现工具的完整 schema SHALL NOT 进入最终 request
- **AND** tool search 结果 SHALL 只在当前 run 激活对应 schema

### Requirement: Read Before Write SHALL 防止基于过期内容写入

Filesystem Profile SHALL 在成功 Read 的 ToolMessage metadata 中记录 path 与当前内容 hash。Edit/Write 前 SHALL 重新读取并验证现有文件具有当前版本的 read mark；成功写入 SHALL 使旧 mark 失效。同一路径的检查与执行 SHALL 串行化。

#### Scenario: 已读文件被外部修改

- **WHEN** Edit/Write 发现当前 mtime/hash 与最近 Read 记录不同
- **THEN** tool adapter SHALL 拒绝基于过期内容的写入
- **AND** 工具 SHALL 返回要求重新读取的有界 error ToolMessage

### Requirement: Provider Adapter SHALL 生成唯一 Canonical Request

LangChain Provider adapter SHALL 在发送前完成 system 合并、message role、tool call/result id、thinking/media block、schema 与 cache marker 的 Provider 适配。Noesis SHALL NOT 建立第二个 canonical request adapter。`PatchToolCallsMiddleware` SHALL 只修复中断导致的不完整 tool pair，不得被当成 Provider encoder。

#### Scenario: 预算基于最终 Schema

- **WHEN** DeferredToolFilter 与 Provider adapter 完成 tool schema 过滤和编码
- **THEN** compaction 预算 SHALL 使用该最终 request
- **AND** SHALL NOT 使用过滤前的全量 catalog 或早期 history 快照

### Requirement: Subagent Context SHALL 默认隔离

普通子 Agent SHALL 使用独立 message history 与私有运行状态，只接收任务输入和 Profile 明确允许的稳定上下文。父 Agent 只 SHALL 接收子任务的有界结果；子任务取消、失败或超时 SHALL 形成可识别结果，且不得把子 Agent 私有状态合并回父 Agent。

#### Scenario: 普通 Task Worker

- **WHEN** 主 Agent 创建普通 task worker
- **THEN** worker SHALL 不自动获得父 Agent 全部 conversation 或私有 counters
- **AND** SHALL 只获得任务描述、允许的稳定上下文与自身工具

#### Scenario: 并行子任务独立完成

- **WHEN** 主 Agent 同时运行多个子任务且其中一个失败或取消
- **THEN** 其它已运行子任务 SHALL 不被错误合并或重复执行
- **AND** 父 Agent SHALL 按 tool call id 接收每个子任务的独立有界结果

#### Scenario: 显式 Fork 与 Resume

- **WHEN** 调用方显式选择 fork
- **THEN** 子 Agent SHALL 获得父 conversation snapshot 与白名单 durable state 的深拷贝
- **AND** 子 Agent resume SHALL 从自己的 checkpoint 恢复，不重读父 Agent 当前 state

### Requirement: 记忆注入 SHALL 区分稳定前缀与 Run 级选条通道

Runtime SHALL 经上下文文件通道注入记忆：`USER.md` 与 `MEMORY.md` 索引 SHALL 位于稳定前缀（会话内不变），每 Run 选中的条目正文 SHALL 经 Run 级 late-context 快照通道注入；每 Run 变化的内容 SHALL NOT 进入稳定前缀区（防全历史缓存失效）。注入 SHALL 在 Run 级冻结，subagent 不重复注入。`search_memory` 工具 SHALL 以 grep 与文件读取实现并遵循既有预算限制。旧 Bulletin 中间件注入路径 SHALL 移除。

#### Scenario: 注入位置稳定
- **WHEN** 同一会话多轮对话
- **THEN** 稳定前缀内容 SHALL 不变，记忆内容 SHALL 位于相同注入位置

#### Scenario: 检索预算
- **WHEN** 模型调用 search_memory
- **THEN** 工具 SHALL 遵循预算并只读 memory 目录文件

### Requirement: Run 管道 SHALL 为主/子 Agent 唯一执行内核

系统 SHALL 以单一 RunPipeline 承载 run 执行映射：LangGraph 流事件 → 领域事件（RunStarted / ReasoningDelta / TextDelta / ToolCallStarted / ToolOutputAvailable / UsageDelta / ContextSnapshot / ApprovalRequired / RunFinished）、usage 累计、终态 payload 构造（content + usage + finish_reason）与上下文快照提取。主 Agent（HTTP/SSE 请求作用域）与后台子 Agent（executor 生命周期包装）SHALL 消费同一管道实现；同一 run 级能力 SHALL NOT 在主/子链路存在两份实现。推理档位 ContextVar 的设置点 SHALL 位于管道入口（turn 参数），主链路现状行为不变、子 Agent followup turn 随参数生效。

#### Scenario: 子 Agent run 的 usage 落库

- **WHEN** 后台子 Agent 完成一个 turn
- **THEN** 该 turn 的 usage（steps / llm_ms / input_tokens / output_tokens / cache_read_tokens / cache_write_tokens）SHALL 写入子会话 assistant 消息 `extra.usage`，字段结构与主链路一致
- **AND** 父会话当轮 assistant 消息 SHALL 继续按「主+子合并」口径累计 usage

#### Scenario: 上下文快照单一来源

- **WHEN** 子 Agent turn 内模型调用返回 usage
- **THEN** 上下文快照 SHALL 由管道写入 ContextMetricsRegistry（按 session_id 分键，主子隔离）
- **AND** 代码库中 SHALL NOT 存在第二份针对子 Agent 的快照提取实现

#### Scenario: 主链路 SSE 帧兼容

- **WHEN** 主 Agent run 经统一管道执行并序列化为 SSE
- **THEN** 帧集合、语义与顺序 SHALL 与统一前逐帧一致（reasoning-* / text-* / tool-* / token-details / context-update / finish / [DONE]）

#### Scenario: 推理档位随 turn 生效

- **WHEN** 子 Agent followup turn 携带 reasoning_effort 参数
- **THEN** 该 turn 的模型调用 SHALL 使用指定档位
- **AND** 未指定时 SHALL 继承任务创建时的档位

#### Scenario: 双轨不可表达

- **WHEN** 开发者向 run 管道新增能力（新事件类型、新 usage 字段、新终态属性）
- **THEN** 该能力 SHALL 经由 RunPipeline 单点生效于主/子两条链路，无需分别实现
