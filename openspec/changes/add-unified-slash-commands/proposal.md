# 变更提案：统一跨端斜杠命令层

- **变更 ID**: `add-unified-slash-commands`
- **状态**: 🆕 提案
- **分支建议**: `feat/unified-slash-commands`（从最新 `dev` 拉）
- **类型**: 功能 / 架构
- **影响范围**: `backend/packages/noesis-core/src/noesis/chat/`、`backend/packages/noesis-core/src/noesis/services/qa/`、`backend/packages/noesis-core/src/noesis/services/channel_run_service.py`、`backend/packages/noesis-cli/src/noesis_cli/`

## 背景

Noesis 当前有三条通道（Web、Telegram/Feishu、CLI），各自走独立路径：

- Web → `QaService` → `MentionResolveService` → `SuperAgent`
- Telegram/Feishu → `channel_run_service.run_channel_agent` → `SuperAgent`
- CLI → `ChatSession` 直接调 `SuperAgent.run_agent()`（不经 HTTP、不经 QaService）

三者之间**没有共享的命令层**。`/help`、`/skills`、`/baoyu-url-to-markdown` 这类斜杠命令在后端无任何 handler；前端 `/skill-id` 仅是 mention 自动补全（`useMentionCatalog.ts`），选中后转成 `MentionItem(type:"skill")` 注入 prompt，**没有命令执行语义**。CLI 仅靠 Typer 内置 `no_args_is_help` 显示帮助，与 Web 无关。`RunOrigin`（`events.py:7`）甚至不含 `"cli"`。

结果：同一能力在不同端表现不一致，加一个新命令要改三个入口，且 CLI 不支持 skills 选择。

## 动机

让「加一个命令只写一次、三端自动生效」，参考 Hermes gateway 的「信道无关命令层」思路（见 `Interview/highlights/SSE/multichannel_demo.py`）：所有平台的输入折叠成统一消息结构 → 汇入同一 `dispatch()` 命令分发中心 → 同一套命令注册表。

## 设计概要

1. **统一消息解析点**：复用现有 `InboundMessage`（`channels.py:18`，已含 channel_type/text/thread_id/raw）作为跨端唯一消息结构，补可选 `user_id`，并增加 `command_name()` / `command_args()` 方法。命令**只在此解析一次**。
2. **命令注册表 + 分发器**：新增 `noesis/chat/commands/registry.py`，`@command` 装饰器 + `dispatch()`。`dispatch` 返回结构化 `CommandResult`（非裸 `str`），支持「直接回复」与「放行并改写请求为 Agent run」两种形态。
3. **三通道接入同一 dispatch**：各通道在「消息进 Agent 前」统一先 `dispatch`：命中且 `handled=True` → 直接回复、不启动 Agent；未命中 → 继续原有路径。
4. **区分控制命令与技能命令**：控制命令（`/help` 等）拦截不进 Agent；技能快捷命令（`/baoyu-url-to-markdown`）由 dispatch 改写为一次 `enabled_skills=[该skill]` 的 Agent run，复用 `SuperAgent`。
5. **CLI 复用 registry**：`noesis help` / `noesis skills` 等 Typer 子命令直接调用同一 `registry`，经 `render.py` 投影到终端。CLI 交互模式内也支持 `/help`。

详见 `design.md`。

## 首批范围

仅实现 **A 类只读查询命令**：

| 命令 | 作用 | 复用 |
|---|---|---|
| `/help` | 列出所有可用斜杠命令 | `registry` 自身 |
| `/skills` | 列出已安装 skill 包 | `skills_root()` 扫描 SKILL.md |
| `/agents` | 列出可用 qa_type | CLI `agents` + `QA_TYPE_MAP` |
| `/model` | 查看当前模型 | model catalog |
| `/status` | 当前 run 状态 / 是否 HITL 挂起 | `RunPaused` / `hitl_pending` |

D 类（技能快捷命令：所有 skill 自动暴露为 `/命令名`）**不在首批实现**，但 registry 与 `CommandResult` 设计必须为其预留扩展点（见 `design.md` §扩展点）。

## 非目标

- 不替换现有 mention 机制（`/skill-id` 仍走 `MentionResolveService` 进 Agent 当工具用）。
- 不在首批实现 B/C/D 类命令（会话控制、远程交互、技能快捷调用）。
- 不改变各通道的出站投影逻辑（`ChannelCapabilities` + `project_for_capabilities` 不变）。

## 风险与回退

- **风险**：Web 入口插入 dispatch 可能影响现有 SSE 流式时序。缓解：dispatch 命中时走 ephemeral 回复（不落库、不经 Agent 流式），与正常对话落库路径隔离。
- **回退**：命令层是纯新增拦截，dispatch 未命中即原路径放行；任何通道出现回归可单独摘除该通道的 dispatch 接入点，不影响其余通道。

## 验证

- `backend`：`uv run pytest tests/ -q`，新增 `tests/chat/commands/` 覆盖 registry、dispatch、三通道接入。
- `frontend`：按影响范围 `pnpm lint`（首批不改前端，但需确认 mention 与 `/help` 共存不冲突）。
- 手动：三端分别触发 `/help`、`/skills`，确认输出一致（仅投影差异）。
