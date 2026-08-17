"""命令注册表 + 分发器（跨端唯一实现）。

核心约束：
1. 命令只解析一次 —— 在 ``InboundMessage.command_name()``，任何 adapter 不自行解析。
2. 命令逻辑只写一次 —— ``@command`` 装饰器注册，``dispatch`` 分发。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from noesis.chat.delivery.channels import InboundMessage
from noesis.chat.commands.result import CommandResult, RequestRewrite

#: 控制命令保留字。skill 目录 SHALL NOT 与之重名；dispatch 匹配时控制命令先于 skill 命令。
CONTROL_COMMANDS: frozenset[str] = frozenset(
    {"help", "skills", "agents", "model", "status", "compact", "reset", "new",
     "approve", "reject", "stop"}
)

CommandHandler = Callable[[InboundMessage], Awaitable[CommandResult]]

#: value: (handler, description, channels)。channels 为 None 表示全通道可用；
#: 非 None（frozenset[str]）表示仅在这些 channel_type 上可用——其余通道 dispatch
#: 视为该命令不存在（handled=False 放行），list_command_descriptions 也不返回。
_RegistryEntry = tuple[CommandHandler, str, "frozenset[str] | None"]

_registry: dict[str, _RegistryEntry] = {}


def command(
    name: str,
    *,
    description: str = "",
    channels: "tuple[str, ...] | frozenset[str] | None" = None,
) -> Callable[[CommandHandler], CommandHandler]:
    """装饰器：注册一个命令。

    ``description`` 用于三端命令发现（Web 补全、Telegram setMyCommands、CLI help），
    单一来源：控制命令在此就近写，skill 命令由 scan_installed_skills 从 SKILL.md 取。

    ``channels`` 限定命令可用的 ``channel_type``（如 ``("telegram", "feishu")``）；
    默认 None 表示全通道可用，保持现有命令行为不变。声明 channels 后，其余通道
    dispatch 命中该命令时放行（handled=False），命令发现也不返回它。
    """
    if channels is not None:
        normalized = frozenset(channels)
        if not normalized:
            raise ValueError(f"command /{name} channels 不能为空集合")
    else:
        normalized = None

    def decorator(fn: CommandHandler) -> CommandHandler:
        if name in _registry:
            raise ValueError(f"command /{name} already registered")
        _registry[name] = (fn, description, normalized)
        return fn

    return decorator


def list_commands(channel: "str | None" = None) -> list[str]:
    """已注册命令名，按字典序。

    传 ``channel`` 时只返回在该通道可用的命令（channels 为 None 或含 channel）；
    不传则返回全部（兼容现有调用）。
    """
    return sorted(
        name
        for name, (_, _, ch) in _registry.items()
        if channel is None or ch is None or channel in ch
    )


def list_command_descriptions(channel: "str | None" = None) -> list[tuple[str, str]]:
    """已注册命令的 (name, description)，按字典序；供三端命令发现 UI。

    传 ``channel`` 时按通道过滤（同 ``list_commands``）；不传返回全部。
    """
    return [
        (name, desc)
        for name, (_, desc, ch) in sorted(_registry.items())
        if channel is None or ch is None or channel in ch
    ]


def get_handler(name: str) -> CommandHandler | None:
    return _registry.get(name, (None, "", None))[0]


async def dispatch(msg: InboundMessage) -> CommandResult:
    """核心分发：任何通道的 /cmd 都走这里。

    返回 ``handled=False`` 即放行（非命令或无斜杠）；命中已注册控制命令则执行；
    匹配已安装 skill 名则转译为一次 Agent run（D 类：rewrite_request）；
    其余返回提示文本而非放行，避免把 ``/typo`` 当成普通 query 喂给 Agent。

    命令声明了 channels 且当前 msg.channel_type 不在其中时，视为该命令对此通道
    不存在——返回 ``handled=False`` 放行（当普通文本进 Agent）。
    """
    name = msg.command_name()
    if name is None:
        return CommandResult(handled=False)
    entry = _registry.get(name)
    if entry is not None:
        handler, _desc, ch = entry
        if ch is not None and msg.channel_type not in ch:
            # 该命令在当前通道不可用：放行，当普通文本进 Agent
            return CommandResult(handled=False)
        return await handler(msg)
    # D 类：skill 快捷命令。控制命令保留字不得被 skill 覆盖。
    if name not in CONTROL_COMMANDS:
        from noesis.chat.config_skills_scan import scan_all_skill_names

        if name in scan_all_skill_names(msg.user_id):
            args = msg.command_args()
            if not args:
                return CommandResult(
                    handled=True,
                    text=f"用法: /{name} <你的问题或参数>\n（该 skill 已启用，请补充问题）",
                )
            return CommandResult(
                handled=True,
                rewrite_request=RequestRewrite(query=args, enabled_skills=[name]),
            )
    return CommandResult(handled=True, text=f"未知命令 /{name}（试试 /help）")
