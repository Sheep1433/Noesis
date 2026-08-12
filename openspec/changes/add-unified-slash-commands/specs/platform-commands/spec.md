# 规格变更：platform-commands

对 `openspec/specs/platform-chat/` 的增量（若该 spec 不存在则新建 `platform-commands` 能力区）。

## SPEC.1 命令解析统一性

### 要求

- 所有通道（web / telegram / feishu / wechat / cli）的斜杠命令 **SHALL** 在 `InboundMessage.command_name()` 唯一解析点完成解析，任何 adapter 或通道入口 **SHALL NOT** 自行解析斜杠命令。
- `dispatch(InboundMessage) → CommandResult` **SHALL** 是所有通道「消息进 Agent 前」的共同拦截点。

### 场景

- 用户在 Telegram 发 `/help`、在 Web 发 `/help`、在 CLI 交互模式输入 `/help` → 三端 **SHALL** 命中同一 handler，输出内容一致（仅出站投影差异）。
- 用户发 `/foo`（未注册）→ `dispatch` **SHALL** 返回 `handled=True` + 提示文本（建议 `/help`），**SHALL NOT** 启动 Agent。
- 用户发非斜杠文本 → `dispatch` **SHALL** 返回 `handled=False`，原路径放行。

## SPEC.2 命令注册

### 要求

- 命令 **SHALL** 通过 `@command(name)` 装饰器注册到单一 registry。
- 加一个新命令 **SHALL** 只需新增一个 handler + 装饰器，**SHALL NOT** 改动任何 adapter 或通道入口。

## SPEC.3 控制命令保留字

### 要求

- `{"help","skills","agents","model","status","reset","approve","reject","stop"}` **SHALL** 为控制命令保留字。
- skill 目录 **SHALL NOT** 与保留字重名。
- dispatch 匹配 **SHALL** 控制命令先于 skill 命令。

## SPEC.4 结构化命令结果

### 要求

- `dispatch` **SHALL** 返回 `CommandResult`，而非裸 `str`。
- `CommandResult(handled=True, text=...)` → 通道投影后回复，**SHALL NOT** 启动 Agent，**SHALL NOT** 落库（不产生 assistant 消息记录）。命令回复为 ephemeral 系统提示，不进消息历史。
- `CommandResult(handled=True, rewrite_request=...)`（D 类）→ 通道 **SHALL** 以改写后的 query + `enabled_skills` 走 `SuperAgent`，**SHALL NOT** 另起执行路径。
- `CommandResult(handled=False)` → 原路径放行。

## SPEC.5 首批内置命令

### 要求

首批 **SHALL** 实现以下只读命令，三端一致：

| 命令 | 行为 |
|---|---|
| `/help` | 列出 registry 中所有命令名 |
| `/skills` | 列出 `skills_root()` 下已安装 skill 包（命令名 + 描述） |
| `/agents` | 列出 `QA_TYPE_MAP` 中可用 qa_type |
| `/model` | 输出当前模型 |
| `/status` | 输出当前 run 状态 / 是否 `hitl_pending` |

## SPEC.6 CLI 子命令复用

### 要求

- `noesis help` / `noesis skills` 等 Typer 子命令 **SHALL** 调用同一 `registry` 的 handler，**SHALL** 复用端内 `/help` 的实现。
- CLI 交互模式内 **SHALL** 支持 `/help` 等斜杠命令，与 Web/Telegram 行为一致。

## SPEC.7 RunOrigin

### 要求

- `RunOrigin` **SHALL** 包含 `"cli"`，使 CLI 通道纳入 delivery 来源枚举。
