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
    {"help", "skills", "agents", "model", "status", "compact", "reset", "approve", "reject", "stop"}
)

CommandHandler = Callable[[InboundMessage], Awaitable[CommandResult]]

_registry: dict[str, tuple[CommandHandler, str]] = {}


def command(name: str, *, description: str = "") -> Callable[[CommandHandler], CommandHandler]:
    """装饰器：注册一个命令，任何通道都能触发。

    ``description`` 用于三端命令发现（Web 补全、Telegram setMyCommands、CLI help），
    单一来源：控制命令在此就近写，skill 命令由 scan_installed_skills 从 SKILL.md 取。
    """

    def decorator(fn: CommandHandler) -> CommandHandler:
        if name in _registry:
            raise ValueError(f"command /{name} already registered")
        _registry[name] = (fn, description)
        return fn

    return decorator


def list_commands() -> list[str]:
    """所有已注册命令名，按字典序。"""
    return sorted(_registry)


def list_command_descriptions() -> list[tuple[str, str]]:
    """所有已注册命令的 (name, description)，按字典序；供三端命令发现 UI。"""
    return [(name, desc) for name, (_, desc) in sorted(_registry.items())]


def get_handler(name: str) -> CommandHandler | None:
    return _registry.get(name, (None, ""))[0]


async def dispatch(msg: InboundMessage) -> CommandResult:
    """核心分发：任何通道的 /cmd 都走这里。

    返回 ``handled=False`` 即放行（非命令或无斜杠）；命中已注册控制命令则执行；
    匹配已安装 skill 名则转译为一次 Agent run（D 类：rewrite_request）；
    其余返回提示文本而非放行，避免把 ``/typo`` 当成普通 query 喂给 Agent。
    """
    name = msg.command_name()
    if name is None:
        return CommandResult(handled=False)
    handler = get_handler(name)
    if handler is not None:
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
