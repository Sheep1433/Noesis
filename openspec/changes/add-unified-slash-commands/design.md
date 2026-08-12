# 设计：统一跨端斜杠命令层

参考：`Interview/highlights/SSE/multichannel_demo.py`（Hermes 式信道无关命令层）。

## 1. 核心约束

来自 demo 的两条硬约束，贯穿整个设计：

1. **命令只解析一次**：所有平台的输入折叠成 `InboundMessage`，命令在 `InboundMessage.command_name()` 唯一解析，任何 adapter 都不自行解析斜杠。
2. **命令逻辑只写一次**：`@command` 装饰器注册，`dispatch()` 分发；加新命令只碰装饰器，不碰任何 adapter 或通道入口。

## 2. 统一消息结构：扩展 `InboundMessage`

复用 `backend/packages/noesis-core/src/noesis/chat/delivery/channels.py:18` 的 `InboundMessage`，不新造结构（demo 的 `MessageEvent` 与之等价）。

```python
@dataclass
class InboundMessage:
    channel_type: str          # "web" | "telegram" | "feishu" | "wechat" | "cli"
    external_chat_id: str
    text: str
    external_message_id: Optional[str] = None
    thread_id: Optional[str] = None
    user_id: Optional[str] = None              # 新增：控制命令需要按用户定位会话
    raw: Dict[str, Any] = field(default_factory=dict)

    def command_name(self) -> Optional[str]:
        """斜杠命令统一解析点。'/help x' → 'help'；非斜杠返回 None。"""
        t = self.text.strip()
        if not t.startswith("/"):
            return None
        return t.split(maxsplit=1)[0][1:]

    def command_args(self) -> str:
        t = self.text.strip().split(maxsplit=1)
        return t[1] if len(t) > 1 else ""
```

`RunOrigin`（`events.py:7`）补 `"cli"`：
```python
RunOrigin = Literal["web", "telegram", "wechat", "feishu", "cron", "eval", "cli"]
```

## 3. 命令注册表与分发器

新增 `backend/packages/noesis-core/src/noesis/chat/commands/`：

```
chat/commands/
├── __init__.py
├── registry.py      # @command 装饰器 + CommandHandler 协议 + dispatch()
├── result.py        # CommandResult（结构化，支持两种形态）
└── handlers/
    ├── __init__.py
    ├── help.py
    ├── skills.py
    ├── agents.py
    ├── model.py
    └── status.py
```

### 3.1 `result.py` —— 结构化命令结果

demo 的 handler 直接返回 `str`，对 Noesis 不够：各通道能力不同（Telegram 4000 字 + MarkdownV2 转义、Web 富文本 SSE、CLI 终端）。dispatch 产出**通道无关的结构化结果**，再由各通道 `project_outbound` / `render` 投影。

```python
@dataclass
class CommandResult:
    handled: bool                          # True=命中命令；False=放行进 Agent
    text: str = ""                         # 富文本（Markdown），handled=True 时用
    # 扩展点（D 类预留，首批不实现）：
    rewrite_request: Optional[RequestRewrite] = None

@dataclass
class RequestRewrite:
    """技能快捷命令：命中后改写为一次 Agent run，而非直接回复文本。"""
    query: str
    enabled_skills: list[str]
```

两种形态：
- **直接回复**：`handled=True, text="..."` → 通道投影后回复，**不启动 Agent**。
- **放行改写**（D 类）：`handled=True, rewrite_request=...` → 通道以改写后的 query + skills 走 Agent run，复用 `SuperAgent`。
- **未命中**：`handled=False` → 继续原有路径（Web 的 mention / Telegram 的直跑 / CLI 的直跑）。

### 3.2 `registry.py` —— 注册表与分发

```python
CommandHandler = Callable[[InboundMessage], Awaitable[CommandResult]]
_registry: dict[str, CommandHandler] = {}

def command(name: str):
    def decorator(fn: CommandHandler):
        _registry[name] = fn
        return fn
    return decorator

def list_commands() -> list[str]:
    return sorted(_registry)

async def dispatch(msg: InboundMessage) -> CommandResult:
    name = msg.command_name()
    if name is None:
        return CommandResult(handled=False)        # 非命令，放行
    handler = _registry.get(name)
    if handler is None:
        return CommandResult(handled=True, text=f"未知命令 /{name}（试试 /help）")
    return await handler(msg)
```

### 3.3 保留字与匹配优先级

控制命令是保留字，skill 目录不得与之重名。dispatch 顺序：**控制命令先于 skill 命令**。

```python
CONTROL_COMMANDS = {"help", "skills", "agents", "model", "status", "reset", "approve", "reject", "stop"}
```

D 类实现时，skill 命令的 fallback 匹配需先校验 `name not in CONTROL_COMMANDS`，且命中后走 `RequestRewrite` 形态。

## 4. 内置命令（首批 A 类）

每个 handler 只读、零副作用，封装现成能力：

| 命令 | 实现要点 | 复用 |
|---|---|---|
| `help` | `text = ", ".join(list_commands())` | `registry` 自身 |
| `skills` | 扫描 `skills_root()` 下列出含 `SKILL.md` 的子目录，输出 `命令名 + 描述` | `config/extensions_paths.py:skills_root()` |
| `agents` | 遍历 `QA_TYPE_MAP` 输出 qa_type + Agent 类名 | CLI `agents`（`main.py:67`）+ `QA_TYPE_MAP` |
| `model` | 读取当前 model catalog / 会话模型 | model catalog |
| `status` | 查询当前 run 状态、是否 `hitl_pending` | `RunPaused` / `channel_run_service` 状态 |

`/skills` 列表中的每个条目天然对应一个（D 类）可调用的 `/命令名`，二者走同一份 skill 扫描结果，避免「列表里有但调不通」。

## 5. 三通道接入

各通道在「消息进 Agent 前」插入同一 `dispatch`，逻辑等价：

```python
result = await dispatch(inbound)
if result.handled:
    if result.rewrite_request:
        # D 类：以改写后的请求走 Agent run（首批无此分支）
        ...
    else:
        await channel.reply(inbound, result.text)   # 通道投影后回复，不启动 Agent
        return
# 未命中 → 原有路径
await run_agent(...)
```

### 5.1 Telegram / Feishu

入口 `channel_run_service.run_channel_agent`（`services/channel_run_service.py:232`）：`route_inbound` 之后、调 `SuperAgent` 之前插 `dispatch`。已具备 `InboundMessage`，接入最顺，作为首个接入点。

### 5.2 Web

`QaService` 入口构造 `InboundMessage(channel_type="web", user_id=..., text=query, ...)`，先 `dispatch`。命中 → **ephemeral 回复**（临时系统提示，**不落库、不产生 assistant 消息记录**，不经 Agent 流式）；未命中 → 继续 `MentionResolveService` → `SuperAgent`。

命令交互与正常对话分离：历史记录只记对话，`/help` 等命令回复像系统提示一闪而过（类比 Slack 的 `/help` 不进频道历史）。

### 5.3 CLI

`ChatSession._run_agent_turn`（`noesis_cli/client.py:78`）构造 `InboundMessage(channel_type="cli", ...)`，先 `dispatch`。命中 → `render.py` 输出；未命中 → 照常调 Agent。

`noesis help` / `noesis skills` Typer 子命令（`main.py`）直接调用同一 `registry` 的 handler，经 `StreamRenderer` 投影，与端内 `/help` 输出一致。

## 6. 命名风格

统一斜杠 `/cmd`，三端一致；CLI 交互模式内也是 `/help`。`noesis help` 子命令复用同一 registry 作为 CLI 原生入口。

## 7. 扩展点（D 类预留，首批不实现）

- **技能快捷命令**：所有 skill 自动暴露为 `/命令名`（= skill 目录名）。dispatch fallback：`name not in CONTROL_COMMANDS` 且匹配某 skill 目录 → 返回 `CommandResult(handled=True, rewrite_request=RequestRewrite(query=args, enabled_skills=[name]))`。
- **三条规则**（首批设计须遵守，避免后续冲突）：
  1. 控制命令保留字优先；skill 目录不得与之重名。
  2. skill 命令改写为 Agent run，不另起执行路径。
  3. `/skills` 列表与 skill 命令同源（同一份扫描结果）。

## 8. 不变项

- mention 机制（`/skill-id` → `MentionResolveService`）不变。
- 各通道出站投影（`ChannelCapabilities` + `project_for_capabilities`）不变。
- Agent 类（`SuperAgent` 等）不变。
