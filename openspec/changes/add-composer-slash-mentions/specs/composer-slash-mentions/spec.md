## ADDED Requirements

### Requirement: Composer SHALL 支持 `/` 与 `@` 触发 mention picker

系统 SHALL 在 chat Composer 文本输入中，于行首或空白字符之后输入 `/` 或 `@` 时打开 mention picker；Esc 或点击外侧 SHALL 关闭；方向键 + Enter（或点击）SHALL 选中一项并插入对应 mention chip / token。

`/` 触发时候选 SHALL 至少包含当前用户可见的 Skills 包（平台 + 用户）；`@` 触发时候选 SHALL 至少包含当前会话可引用的文件/文件夹，并在支持 subagent 的 `qa_type` 下包含可用 `subagent` 类型。

#### Scenario: 斜杠打开 Skills 列表

- **WHEN** 用户在 `SUPER_AGENT_QA` 会话 Composer 输入 `/`
- **THEN** 系统 SHALL 展示 Skills 候选列表，且列表数据来自已缓存或预取的 skills 目录（非按键远程全量搜索）

#### Scenario: At 打开文件与 subagent

- **WHEN** 用户在 `SUPER_AGENT_QA` 或 `FAULT_OPERATION_QA` 会话 Composer 输入 `@`
- **THEN** 系统 SHALL 展示文件/文件夹候选，并 SHALL 展示该 `qa_type` 可用的 subagent 类型

#### Scenario: 不支持的 qa_type 隐藏 slash Skills

- **WHEN** 当前会话 `qa_type` 不挂载 SkillsMiddleware（如 `COMMON_QA`）
- **THEN** 系统 SHALL NOT 将 `/` 展示为 Skills 命令菜单（MAY 无响应或提示不可用）

### Requirement: Catalog 预取与本地过滤 SHALL 保证交互延迟

前端 SHALL 在进入适用会话或首次打开 picker 时预取 catalog，并在客户端以 TTL 缓存；用户在 picker 内继续输入时 SHALL 仅对本地缓存做模糊过滤，**SHALL NOT** 为每次按键发起全量 tree 请求。

Skills catalog SHALL 复用 `GET /api/skills/fs/tree`；文件 catalog SHALL 复用 `GET /api/chat/sessions/{session_id}/context`（或其等价只读树）。Subagent 列表 MAY 为前端按 `qa_type` 维护的静态表。

#### Scenario: 二次打开使用缓存

- **WHEN** 用户在 TTL 内第二次输入 `/`
- **THEN** 系统 SHALL 立即展示候选且 **SHALL NOT** 强制重新请求 skills tree（除非缓存已失效或用户触发刷新）

#### Scenario: 过滤不打远程

- **WHEN** 用户在已打开的 `@` picker 中输入文件名片段
- **THEN** 系统 SHALL 在本地过滤候选列表，**SHALL NOT** 将过滤关键字作为远程搜索参数

### Requirement: 发送消息 SHALL 携带结构化 mentions

用户确认的 mention SHALL 以结构化数组随本轮问答请求提交（字段名 `mentions`），并 SHALL 写入对应用户消息的 `extra.mentions` 以便历史回显。每项 SHALL 含 `type`（`skill` | `file` | `folder` | `subagent`）及类型相关标识（如 `id`、`path`、`source`、`virtual_path`）。

未选择任何 mention 时，客户端 **SHALL NOT** 要求该字段；省略时服务端行为与变更前一致。

#### Scenario: 选中 skill 后发送

- **WHEN** 用户经 `/` 选中 skill `deep-research-v2` 并发送消息
- **THEN** 请求体 SHALL 含 `mentions` 中一项 `type=skill` 且 `id=deep-research-v2`，且用户消息 `extra.mentions` SHALL 持久化该项

#### Scenario: 旧客户端兼容

- **WHEN** 客户端发送问答请求且不包含 `mentions` 字段
- **THEN** 系统 SHALL 按既有逻辑处理，**SHALL NOT** 返回仅因缺少 `mentions` 导致的错误

### Requirement: 文件 mention SHALL 默认引用优先

对 `file` / `folder` 类型 mention，系统默认 SHALL 向 Agent 注入路径引用提示（要求 Agent 通过工具读取），**SHALL NOT** 默认将完整大文件内容塞入用户可见 query 字符串。仅当服务端判定为小文本且未超配置阈值时，MAY 内联有限正文。

#### Scenario: 大文件只引用

- **WHEN** 用户 `@` 引用超过配置阈值的文本文件并发送
- **THEN** 注入内容 SHALL 包含可解析的虚拟路径或会话相对路径，且 **SHALL NOT** 将全文作为必选内联上下文

### Requirement: Picker 与 `+` 菜单并存

系统 SHALL 保留既有 Composer `+` 菜单（Models / Skills 勾选 / MCP）行为；`/`、`@` picker **SHALL NOT** 替换该菜单。本轮 skill mention MAY 与会话 `enabled_skills` 合并用于当轮执行，但 **SHALL NOT** 强制改写用户未意图修改的持久勾选（实现细节见 design）。

#### Scenario: 同时存在两种入口

- **WHEN** 用户打开 `+` 菜单勾选 Skills，并另用 `/` 插入 skill mention
- **THEN** 两套 UI 均可用，且本轮请求可同时携带 `enabled_skills` 与 `mentions`
