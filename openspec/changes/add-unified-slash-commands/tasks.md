# 任务清单：add-unified-slash-commands

> 状态标记：[ ] 未开始 / [~] 进行中 / [x] 完成。按顺序执行，每步可独立回归。

## 阶段 1 · 命令层骨架（零通道接入）

- [ ] **1.1** 扩展 `InboundMessage`（`chat/delivery/channels.py:18`）：新增 `user_id` 字段，新增 `command_name()` / `command_args()` 方法
- [ ] **1.2** `RunOrigin`（`chat/delivery/events.py:7`）补 `"cli"`
- [ ] **1.3** 新建 `chat/commands/result.py`：`CommandResult` + `RequestRewrite`（D 类数据结构先定义，首批不使用）
- [ ] **1.4** 新建 `chat/commands/registry.py`：`@command` 装饰器、`list_commands()`、`dispatch()`、`CONTROL_COMMANDS` 保留字
- [ ] **1.5** 新建 `tests/chat/commands/test_registry.py`：覆盖 dispatch 命中 / 未命中 / 未知命令 / 非斜杠放行

## 阶段 2 · 首批 A 类 handler

- [ ] **2.1** `chat/commands/handlers/help.py` —— 列出 `list_commands()`
- [ ] **2.2** `chat/commands/handlers/skills.py` —— 扫描 `skills_root()` 列出 skill 包；复用 `config/extensions_paths.py:skills_root()`
- [ ] **2.3** `chat/commands/handlers/agents.py` —— 遍历 `QA_TYPE_MAP`
- [ ] **2.4** `chat/commands/handlers/model.py` —— 读取当前模型
- [ ] **2.5** `chat/commands/handlers/status.py` —— 查询 run 状态 / `hitl_pending`
- [ ] **2.6** 每个 handler 单测：构造 `InboundMessage` → 调 handler → 断言 `CommandResult.text`

## 阶段 3 · 三通道接入同一 dispatch

- [ ] **3.1** Telegram/Feishu：`channel_run_service.run_channel_agent`（`services/channel_run_service.py:232`）在 `route_inbound` 后、调 `SuperAgent` 前插 `dispatch`；命中→ `project_outbound` 回复，不启动 Agent
- [ ] **3.2** Web：`QaService` 入口构造 `InboundMessage(channel_type="web")`，先 `dispatch`；命中→ephemeral 回复（不落库、不产生 assistant 消息记录，不经 Agent 流式）；未命中→原 `MentionResolveService` 路径
- [ ] **3.3** CLI：`ChatSession._run_agent_turn`（`noesis_cli/client.py:78`）构造 `InboundMessage(channel_type="cli")`，先 `dispatch`；命中→ `StreamRenderer` 输出；未命中→原 Agent 调用
- [ ] **3.4** `noesis help` / `noesis skills` Typer 子命令（`noesis_cli/main.py`）调用同一 `registry` handler
- [ ] **3.5** 接入回归测试：各通道 mock `dispatch` 命中与未命中两条路径

## 阶段 4 · 验证与文档

- [ ] **4.1** `backend` 全量回归：`uv run pytest tests/ -q`
- [ ] **4.2** `frontend` 按影响范围 `pnpm lint`（确认 mention 与 `/help` 共存不冲突）
- [ ] **4.3** 手动三端验证：分别触发 `/help`、`/skills`，确认输出一致（仅投影差异）
- [ ] **4.4** 更新 `docs/architecture/platform/` 或 `docs/engineering/`：记录统一命令层设计（单文件演进，不做版本对比）

## 非首批（D 类预留，不在本提案实现）

- [ ] 技能快捷命令：所有 skill 自动暴露为 `/命令名`，dispatch fallback 返回 `RequestRewrite` 改写为 Agent run。实现前须校验本提案的 `CommandResult.rewrite_request` 扩展点与 `CONTROL_COMMANDS` 优先级是否成立。
