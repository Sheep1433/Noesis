# Delta: local-project-mode — 新增能力

## ADDED Requirements

### Requirement: 本地项目会话的目录边界

`noesis --project <dir>` 启动的本地会话 SHALL 以该目录为工作区根：Agent 看到的文件路径 SHALL 与宿主机真实路径一致（无 `/workspace` 平移）；会话运行时状态（checkpoint、消息、授权记录、待办）SHALL 落在项目根 `.noesis/` 内，多项目之间 SHALL NOT 串扰。

#### Scenario: 裸仓库零配置启动

- **WHEN** 在一个无任何 Noesis 配置的 git 仓库目录执行 `uv run noesis chat --project .`
- **THEN** 会话 SHALL 正常启动并完成对话、`read_file` / `write_file` / `edit_file` / `execute`
- **AND** 全程 SHALL NOT 依赖 Postgres / Redis / Qdrant / Docker

#### Scenario: 路径直通

- **WHEN** Agent 读写 `<project>/src/a.py`
- **THEN** 工具调用的路径参数 SHALL 原样映射到宿主机文件系统同一路径
- **AND** Agent 回复中给出的路径 SHALL 与用户终端里的路径一致（可直接复制）

### Requirement: 写操作的项目边界拦截

本地模式 SHALL 对文件写/删操作做项目边界拦截：目标路径解析后落在项目根之外（含 `..` 逃逸、符号链接逃逸、绝对路径直写）时，该操作 SHALL 被拒绝并返回可解释错误（含目标路径与当前项目根）；读操作 SHALL 不受边界拦截。

#### Scenario: 项目外写拒绝

- **WHEN** Agent 尝试写入项目根之外的路径
- **THEN** 工具 SHALL 返回拒绝原因（项目外路径 + 当前工作区根）
- **AND** 会话 SHALL NOT 中断，Agent 可基于拒绝信息继续工作

#### Scenario: 路径逃逸形态拦截

- **WHEN** 写目标经 `..` 回溯、符号链接或其组合解析后落在项目根外
- **THEN** 拦截 SHALL 按解析后的真实路径判定并拒绝

### Requirement: 操作分级确认与授权记忆

本地模式 SHALL 按操作类别分级确认：读自动放行；写项目内路径可配置（放行或询问）；执行命令、网络访问、项目外写 SHALL 默认询问。确认 SHALL 复用 HITL 中断机制（参数级谓词），SHALL NOT 新造中断通道。用户选择 SHALL 支持「本次允许 / 永久允许 / 拒绝」三种，其中「永久允许」按「工具 + 参数模式」持久化到项目内授权记录并在后续命中时免确认。

#### Scenario: 高危操作询问

- **WHEN** Agent 发起执行命令或网络访问
- **THEN** 会话 SHALL 中断等待用户确认（本次允许 / 永久允许 / 拒绝）
- **AND** 拒绝后 Agent SHALL 收到可解释的拒绝信息并继续工作，会话不卡死

#### Scenario: 永久允许免确认

- **WHEN** 用户曾对「工具 X + 参数模式 P」选择永久允许，后续命中同一模式
- **THEN** 该调用 SHALL 免确认直接执行

### Requirement: 本地持久化与恢复

本地会话 SHALL 以 SQLite 持久化：LangGraph checkpoint（`.noesis/checkpoint.db`）与消息/会话/待办/授权（`.noesis/local.db`）；助手消息终态 SHALL 保持「先比较再写入」的单行原子更新语义。进程重启后 SHALL 能从持久化状态续聊（同 thread 的消息历史与执行状态一致恢复）。

#### Scenario: 重启续聊

- **WHEN** 本地会话结束后于同一项目重新启动并指定既有会话
- **THEN** 消息历史 SHALL 完整恢复
- **AND** Agent 上下文（checkpoint）SHALL 与恢复前一致，可继续未完成的任务指令

#### Scenario: 并行项目隔离

- **WHEN** 两个不同项目目录各自启动本地会话并行使用
- **THEN** 两会话的 `.noesis/` 状态 SHALL 互不可见、互不影响
