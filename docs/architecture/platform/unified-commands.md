# 统一跨端斜杠命令层

> 状态：Current
> OpenSpec：`add-unified-slash-commands`

## 范围

所有通道（Web / Telegram / 飞书 / CLI）共享同一套斜杠命令。加一个命令只需 `@command` 装饰器，无需碰任何 adapter 或通道入口。设计参考 Hermes gateway 的「信道无关命令层」。

## 核心约束

1. **命令只解析一次**：所有平台的输入折叠成 `InboundMessage`（`chat/delivery/channels.py`），命令在 `InboundMessage.command_name()` 唯一解析。任何 adapter SHALL NOT 自行解析斜杠。
2. **命令逻辑只写一次**：`@command` 装饰器注册到单一 registry（`chat/commands/registry.py`），`dispatch()` 分发。

## 结构

```
chat/commands/
├── registry.py            @command 装饰器 + dispatch() + CONTROL_COMMANDS 保留字
├── result.py              CommandResult + RequestRewrite（D 类预留）
├── runtime.py             run_manager 注入点（避免 chat 反向依赖 services）
└── handlers/              内置命令（help / skills / agents / model / status）
chat/config_skills_scan.py /skills 扫描（与 D 类 skill 命令同源）
```

`dispatch` 返回结构化 `CommandResult` 而非裸 `str`，支持两种形态：

- **直接回复**：`handled=True, text=...` → 通道投影后回复，**不启动 Agent、不落库**（ephemeral）。
- **放行改写**（D 类预留）：`handled=True, rewrite_request=...` → 以改写后的 query + `enabled_skills` 走 `SuperAgent`，不另起执行路径。
- **未命中**：`handled=False` → 原路径放行。

## 通道接入

各通道在「消息进 Agent 前」插同一 `dispatch`，命中则 ephemeral 回复、不启动 Agent：

| 通道 | 接入点 | ephemeral 实现 |
|---|---|---|
| Telegram/飞书 | `telegram_runtime._handle_message`（route 后、`run_channel_agent` 前） | `client.send_message` 直接回，不写 SSOT、不建 run |
| Web | `chat_api.create_run` POST（`RunService.create` 前） | 命中则不建 run、不持久化 user/assistant 消息，返回 `command_reply`；前端 `useSSEStream` 直接渲染文本后结束流 |
| CLI | `noesis_cli.main._run_turn`（`session.run_turn` 前） | `console.print` 直接回，不调 Agent |

CLI 额外：`noesis help` / `noesis skills` 等 Typer 子命令复用同一 registry（`_invoke_slash_command`），与端内 `/help` 同源。

## 不落库

命令回复是 ephemeral 系统提示，**不进消息历史**（类比 Slack 的 `/help` 不进频道历史）。Web 路径在 `create_run` 阶段拦截，`RunService.create` 不被调用，故 user 消息与 assistant 骨架行均不持久化。

## 保留字与 skill 命令

`CONTROL_COMMANDS = {help, skills, agents, model, status, reset, approve, reject, stop}` 为控制命令保留字，skill 目录不得与之重名。dispatch 匹配时控制命令先于 skill 命令。

D 类（skill 快捷命令，所有 skill 自动暴露为 `/命令名`）**已实现**：dispatch 在未命中控制命令时，用 `scan_all_skill_names(user_id)` 检查 name 是否为已安装 skill（platform + user），是则返回 `RequestRewrite(query=用户参数, enabled_skills=[name])`，由通道改写为一次 Agent run。无参数时返回用法提示。`CONTROL_COMMANDS` 保留字优先级保证控制命令不被 skill 名覆盖。

## 包边界

命令层位于 `noesis.chat` 包，受边界约束 SHALL NOT 直接 import `noesis.services` / `noesis.agents`。`/status` 需要 run_manager 时，由 `run_service.py` 在启动时通过 `set_run_manager_provider` 注入（`chat/commands/runtime.py`），未注入则 graceful 降级（CLI 等无 DB 通道）。

## 内置命令

| 命令 | 行为 | 复用 |
|---|---|---|
| `/help` | 列出所有命令（含描述） | registry 自身 |
| `/skills` | 列出已安装 skill 包 | `skills_root()` + `scan_installed_skills` |
| `/agents` | 列出可用 qa_type | `config.code_enum.IntentEnum` |
| `/model` | 当前模型与 catalog | `llm.catalog` |
| `/status` | 用户活跃 run / HITL 挂起 | `RunManager.list_active_for_user`（注入） |

## 命令发现（三端补全）

命令可被发现是「各端能力一致」的前提。三端共用同一数据源（`list_command_descriptions` + `scan_installed_skills`），各端只做 UI 投影：

| 端 | 发现方式 |
|---|---|
| Web | `useMentionCatalog` slash 模式并入控制命令；`GET /api/chat/commands` 取控制命令，与 skills fs-tree 合并成一个 `/` 补全列表 |
| Telegram | `TelegramBotClient.set_my_commands` 注册 Bot 命令菜单，输入 `/` 时原生弹出 |
| CLI | `noesis help` 子命令 + 交互模式 readline Tab 补全（`_install_command_completer`） |

`@command(name, description=...)` 是控制命令描述的单一来源；skill 命令描述取自 SKILL.md frontmatter。

## 热加载

skills 运行时由 `VersionedSkillsMiddleware` 按 revision 惰性热加载（每次 agent run 检查 revision 文件，变了才重扫）。命令发现层的热加载：

- **Web**：`scan_installed_skills` 无缓存，每次 `/skills` 或补全触发都实时扫描；命令列表经 `getSlashCommands` 60s TTL 后刷新。
- **CLI**：`noesis skills` / 补全每次实时扫描。
- **Telegram**：`setMyCommands` 仅在 poll 启动时调一次；之后每条入站消息检查 `skills_root()` 目录 mtime，变了才重新 `setMyCommands`。无变更时只是一次 stat，开销可忽略。
