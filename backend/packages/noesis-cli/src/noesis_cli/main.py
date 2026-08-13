"""Noesis CLI entry point — typer commands."""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import typer
from rich.console import Console

from noesis_cli import __version__
from noesis_cli.client import ChatSession, QA_TYPE_MAP
from noesis_cli.render import EvalCollector, StreamRenderer

console = Console()
app = typer.Typer(
    name="noesis",
    help="Noesis local harness CLI — direct agent call, no HTTP.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(
        False, "--version", "-V", help="Show version and exit.", is_eager=True
    ),
) -> None:
    if version:
        console.print(f"noesis-cli {__version__}")
        raise typer.Exit()


@app.command()
def chat(
    message: str = typer.Argument(
        None, help="首轮消息;省略则进入交互式多轮模式(noesis> 提示符)。"
    ),
    qa_type: str = typer.Option(
        "super", "--qa-type", "-t", help="super | common | simple_mcp"
    ),
    model_id: str = typer.Option(
        None, "--model-id", "-m", help="catalog id 如 flash / mimo / big-pickle"
    ),
    thread_id: str = typer.Option(
        None, "--thread-id", help="会话 thread id(默认随机;多轮复用)"
    ),
) -> None:
    """流式对话,实时打印正文 + 思考 + 工具调用。"""
    asyncio.run(_chat(message, qa_type, model_id, thread_id))


@app.command()
def eval(
    query: str = typer.Option(..., "--query", "-q", help="评测问题"),
    qa_type: str = typer.Option("super", "--qa-type", "-t", help="super | common | simple_mcp"),
    model_id: str = typer.Option(None, "--model-id", "-m", help="catalog id"),
    time_budget: int = typer.Option(600, "--time-budget", help="超时秒数"),
) -> None:
    """单条评测,输出 JSON 结果(final_text / tool_stats / usage)。"""
    asyncio.run(_eval(query, qa_type, model_id, time_budget))


@app.command()
def agents() -> None:
    """列出可用 qa_type。"""
    console.print("[bold]qa_type[/]  Agent 类")
    for name, cls in QA_TYPE_MAP.items():
        if "_" in name and name.split("_")[0] in QA_TYPE_MAP:
            continue  # 别名跳过(super_agent / common_qa)
        console.print(f"  [cyan]{name:12}[/] {cls.__name__}")


@app.command()
def help() -> None:
    """列出可用斜杠命令（与端内 /help 同源）。"""
    text = asyncio.run(_invoke_slash_command("/help"))
    console.print(text)


@app.command()
def skills() -> None:
    """列出已安装 skill 包（与端内 /skills 同源）。"""
    text = asyncio.run(_invoke_slash_command("/skills"))
    console.print(text)


async def _invoke_slash_command(slash_text: str) -> str:
    """复用统一 registry 执行斜杠命令，返回回复文本。"""
    from noesis.chat.commands.registry import dispatch
    from noesis.chat.delivery.channels import InboundMessage

    inbound = InboundMessage(
        channel_type="cli",
        external_chat_id="cli-local",
        text=slash_text,
        user_id="cli-user",
    )
    result = await dispatch(inbound)
    return result.text or "（无输出）"


async def _chat(message: str | None, qa_type: str, model_id: str | None, thread_id: str | None) -> None:
    try:
        session = ChatSession(qa_type=qa_type, model_id=model_id, thread_id=thread_id)
    except ValueError as exc:
        console.print(f"[red]错误:[/red] {exc}")
        raise typer.Exit(1) from exc

    renderer = StreamRenderer(console)
    console.print(f"[dim]qa_type={qa_type} model={model_id or 'default'} thread={session.thread_id}[/]")

    with session.enter_context():
        _install_command_completer()
        if message:
            await _run_turn(session, renderer, message)

        while True:
            try:
                user_input = console.input("[bold cyan]noesis>[/] ")
            except (EOFError, KeyboardInterrupt):
                console.print()
                break
            if not user_input.strip():
                continue
            if user_input.strip().lower() in {"exit", "quit", ":q"}:
                break
            await _run_turn(session, renderer, user_input)


def _install_command_completer() -> None:
    """交互模式 readline 补全：输入 / 后 Tab 列出/补全斜杠命令。

    数据源与 noesis help 同源（list_command_descriptions），无需额外依赖。
    """
    try:
        import readline
    except ImportError:
        return

    from noesis.chat.commands.registry import list_command_descriptions
    from noesis.chat.config_skills_scan import scan_installed_skills

    names = [f"/{n}" for n, _ in list_command_descriptions()]
    names += [f"/{n}" for n, _ in scan_installed_skills()]

    def complete(text: str, state: int) -> str | None:
        matches = [n for n in names if n.startswith(text)] if text.startswith("/") else []
        return matches[state] if state < len(matches) else None

    readline.set_completer(complete)
    readline.parse_and_bind("tab: complete")


async def _run_turn(session: ChatSession, renderer: StreamRenderer, query: str) -> None:
    # 统一命令层：进 Agent 前先 dispatch。
    # 控制命令 → ephemeral 回复；skill 命令 → rewrite 为 Agent run；其余放行。
    from noesis.chat.commands.registry import dispatch
    from noesis.chat.delivery.channels import InboundMessage

    inbound = InboundMessage(
        channel_type="cli", external_chat_id="cli-local", text=query, user_id=session.user_id,
    )
    result = await dispatch(inbound)
    if result.handled and not result.rewrite_request:
        console.print(result.text)
        return
    if result.handled and result.rewrite_request:
        rw = result.rewrite_request
        console.print(f"[dim]启用 skill: {', '.join(rw.enabled_skills)}[/]")
        query = rw.query
        skills = rw.enabled_skills
    else:
        skills = None
    try:
        async for event in session.run_turn(query, enabled_skills=skills):
            renderer.consume(event)
    except Exception as exc:  # noqa: BLE001
        renderer.end_turn()
        console.print(f"[red]运行异常:[/red] {exc}")
        return
    renderer.end_turn()



async def _eval(query: str, qa_type: str, model_id: str | None, time_budget: int) -> None:
    try:
        session = ChatSession(qa_type=qa_type, model_id=model_id, thread_id=f"eval-{uuid.uuid4().hex[:8]}")
    except ValueError as exc:
        console.print(f"[red]错误:[/red] {exc}")
        raise typer.Exit(1) from exc

    collector = EvalCollector()
    t0 = time.perf_counter()
    with session.enter_context():
        try:
            async for event in session.run_turn(query):
                collector.consume(event)
        except Exception as exc:  # noqa: BLE001
            collector.error = str(exc)
    latency_ms = int((time.perf_counter() - t0) * 1000)

    result = collector.to_dict(query=query, model=model_id, latency_ms=latency_ms)
    console.print_json(json.dumps(result, ensure_ascii=False, indent=2))
