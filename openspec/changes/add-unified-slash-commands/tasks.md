# 任务清单：add-unified-slash-commands

> 状态标记：[ ] 未开始 / [~] 进行中 / [x] 完成。按顺序执行，每步可独立回归。

## 阶段 1 · 命令层骨架（零通道接入）

- [x] **1.1** 扩展 `InboundMessage`（`chat/delivery/channels.py`）：新增 `user_id` 字段，新增 `command_name()` / `command_args()` 方法
- [x] **1.2** `RunOrigin`（`chat/delivery/events.py`）补 `"cli"`
- [x] **1.3** 新建 `chat/commands/result.py`：`CommandResult` + `RequestRewrite`（D 类数据结构先定义，首批不使用）
- [x] **1.4** 新建 `chat/commands/registry.py`：`@command` 装饰器、`list_commands()`、`dispatch()`、`CONTROL_COMMANDS` 保留字
- [x] **1.5** 新建 `tests/test_chat_command_registry.py`：覆盖 dispatch 命中 / 未命中 / 未知命令 / 非斜杠放行

## 阶段 2 · 首批 A 类 handler

- [x] **2.1** `chat/commands/handlers/help.py` —— 列出 `list_commands()`
- [x] **2.2** `chat/commands/handlers/skills.py` —— 扫描 `skills_root()` 列出 skill 包；复用 `config/extensions_paths.py:skills_root()`
- [x] **2.3** `chat/commands/handlers/agents.py` —— 遍历 `IntentEnum`
- [x] **2.4** `chat/commands/handlers/model.py` —— 读取当前模型
- [x] **2.5** `chat/commands/handlers/status.py` —— 查询 run 状态 / `hitl_pending`（经 `runtime.set_run_manager_provider` 注入）
- [x] **2.6** handler 单测：`tests/test_chat_command_handlers.py`

## 阶段 3 · 三通道接入同一 dispatch

- [x] **3.1** Telegram/飞书：`telegram_runtime._handle_message`（`route_inbound` 后、`run_channel_agent` 前）插 `dispatch`；命中→ `client.send_message` 回复，不启动 Agent
- [x] **3.2** Web：`chat_api.create_run` POST 在 `RunService.create` 前插 `dispatch`；命中→ephemeral 回复（不建 run、不落库），返回 `command_reply`；未命中→原 `RunService.create` 路径
- [x] **3.3** CLI：`noesis_cli.main._run_turn` 在 `session.run_turn` 前插 `dispatch`；命中→ `console.print`；未命中→原 Agent 调用
- [x] **3.4** `noesis help` / `noesis skills` Typer 子命令复用同一 `registry`（`_invoke_slash_command`）
- [x] **3.5** 接入回归测试：`tests/test_chat_command_channel_integration.py`（Telegram + CLI）、`tests/test_chat_command_web_integration.py`（Web）

## 阶段 4 · 验证与文档

- [x] **4.1** `backend` 全量回归：`uv run pytest tests/ -q`（926 passed；`test_usage_normalization.py` 为 dev 既有 collection 错误，与本次无关，跳过）
- [x] **4.2** `frontend` `pnpm lint`（0 error；新增 `command_reply` 字段 + `useSSEStream` 早返回）
- [x] **4.3** 文档：`docs/architecture/platform/unified-commands.md`（单文件演进）

## 阶段 5 · 命令发现（三端补全）

- [x] **5.1** registry `@command` 加 `description` 参数；`list_command_descriptions()` 暴露 (name, desc)；5 个 handler 补描述
- [x] **5.2** Telegram：`TelegramBotClient.set_my_commands` 封装；`_poll_loop` 启动时注册命令菜单；`_maybe_refresh_bot_commands` 按 skills 目录 mtime 热加载重注册
- [x] **5.3** Web：`GET /api/chat/commands` 端点；`useMentionCatalog` slash 模式并入控制命令（与 skill 合并成一个 `/` 补全列表）；`command` mention 类型 ephemeral 不进 Agent payload
- [x] **5.4** CLI：交互模式 readline Tab 补全（`_install_command_completer`，数据源同 `list_command_descriptions` + `scan_installed_skills`）
- [x] **5.5** 验证：931 passed，前端 lint 0 error；文档更新命令发现与热加载章节

## 非首批（D 类预留，不在本提案实现）

- [x] 技能快捷命令：所有 skill 自动暴露为 `/命令名`，dispatch fallback 返回 `RequestRewrite` 改写为 Agent run。已实现：dispatch fallback 用 `scan_all_skill_names` 判定，三通道 rewrite 接通（Web 改写 content+enabled_skills，CLI 传 enabled_skills，Telegram force_enabled_skills）。无参数时返回用法提示。
