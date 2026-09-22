"""Noesis CLI entry point — typer commands."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid

# 必须在导入 noesis 链（触发配置加载）之前：CLI 进程关闭 DB echo——
# echo 日志由 SQLAlchemy 直接打到 stdout，会污染 -p 的 json/stream-json 契约
os.environ.setdefault("DB_ECHO", "false")

import typer
from rich.console import Console

from noesis_cli import __version__
from noesis_cli.client import (
    ChatSession,
    QA_TYPE_MAP,
    apply_env_model_direct,
    wire_langfuse,
)
from noesis_cli.render import StreamRenderer
from noesis_cli.streamjson import StreamCollector, event_to_stream_line, flag_empty_completion

# stdout 是程序输出，标准库日志一律走 stderr
logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)

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
        None, help="首轮消息；-p 模式必填，省略则进入交互式多轮模式(noesis> 提示符)。"
    ),
    print_mode: bool = typer.Option(
        False, "-p", "--print",
        help="单发即走（Claude Code print 模式）：跑完一问即退出，非零退出码=失败。",
    ),
    qa_type: str = typer.Option(
        "super", "--qa-type", "-t", help="super | common"
    ),
    model: str = typer.Option(
        None, "--model", "-m",
        help="模型 id；super -p 为内置目录 id 或账号自定义模型复合 id（如 "
             "provider/model），common 交互/env 直连模式下为端点真实模型名",
    ),
    session_id: str = typer.Option(
        None, "--session-id", help="会话 id(默认随机;多轮复用)"
    ),
    output_format: str = typer.Option(
        "text", "--output-format", help="text | json | stream-json（后两者建议配合 -p）"
    ),
    kb_collections: str = typer.Option(
        None, "--kb-collections",
        help="逗号分隔的知识库 collection 列表；提供即启用 KB 检索（common 类型）",
    ),
    web_search: bool = typer.Option(
        True, "--web-search/--no-web-search", help="是否启用 web 搜索（common 类型）"
    ),
) -> None:
    """流式对话,实时打印正文 + 思考 + 工具调用。"""
    wire_langfuse()
    kb_cols = [c.strip() for c in (kb_collections or "").split(",") if c.strip()]
    if print_mode and qa_type in ("super", "super_agent"):
        # super print 模式 = 生产 headless 入口（DB 持久化 + 生产 SSE 编码），
        # 不经 env 直连快照——模型由会话/用户偏好/平台目录解析。
        # common 的 headless 持久化是产品缺口（RunService typed 主路径未覆盖
        # COMMON_QA），见评测文档；路由回退内存路径。
        code = asyncio.run(_run_print_db(message, model, session_id, output_format))
        raise typer.Exit(code)
    effective_model = apply_env_model_direct(model)
    if print_mode:
        if not message:
            console.print("[red]错误:[/red] -p 模式需要位置参数作为问题")
            raise typer.Exit(2)
        if output_format not in ("text", "json", "stream-json"):
            console.print(f"[red]错误:[/red] 未知 --output-format: {output_format!r}")
            raise typer.Exit(2)
        code = asyncio.run(
            _run_print(message, qa_type, effective_model, session_id, output_format,
                       kb_collections=kb_cols, web_search_enabled=web_search)
        )
        raise typer.Exit(code)
    asyncio.run(
        _chat(message, qa_type, effective_model, session_id,
              kb_collections=kb_cols, web_search_enabled=web_search)
    )


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


async def _run_print_db(
    message: str, model: str | None, session_id: str | None, output_format: str,
) -> int:
    """-p DB 跑次（super）：复用生产 headless 入口，观测走 DB / Langfuse。"""
    from noesis_cli.client import current_user_id
    from noesis_cli.db_run import run_db_print, shutdown_db_run

    if output_format not in ("text", "json", "stream-json"):
        console.print(f"[red]错误:[/red] 未知 --output-format: {output_format!r}")
        return 2
    sid = session_id or f"cli-{uuid.uuid4().hex[:12]}"
    emit = output_format == "stream-json"

    def out(line: str) -> None:
        print(line, flush=True)

    try:
        record = await run_db_print(
            query=message,
            session_id=sid,
            user_id=current_user_id(),
            model_id=model,
            out=out,
            emit_stream=emit,
        )
    except ValueError as exc:
        console.print(f"[red]错误:[/red] {exc}")
        return 2
    finally:
        await shutdown_db_run()

    if emit:
        out(json.dumps({"type": "__tw_result__", **record}, ensure_ascii=False))
    elif output_format == "json":
        out(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        if record["final_text"]:
            console.print(record["final_text"])
        if record["error"]:
            console.print(f"[red]错误:[/red] {record['error']}")
    return 0 if record["completed"] else 1


async def _run_print(
    message: str, qa_type: str, model: str | None, session_id: str | None, output_format: str,
    *, kb_collections: list[str] | None = None, web_search_enabled: bool = True,
) -> int:
    """-p 单发：跑一问，按 --output-format 输出，返回退出码。"""
    try:
        session = ChatSession(
            qa_type=qa_type, model_id=model, thread_id=session_id,
            kb_collections=kb_collections, web_search_enabled=web_search_enabled,
        )
    except ValueError as exc:
        console.print(f"[red]错误:[/red] {exc}")
        return 2

    emit = output_format == "stream-json"
    collector = StreamCollector()

    def out(line: str) -> None:
        print(line, flush=True)

    if emit:
        out(json.dumps({
            "type": "__tw_init__", "session_id": session.thread_id,
            "user_id": session.user_id, "model": model,
        }, ensure_ascii=False))

    t0 = time.perf_counter()
    with session.enter_context():
        try:
            async for event in session.run_turn(message):
                line = event_to_stream_line(event)
                if line is None:
                    continue
                collector.consume(line)
                if emit:
                    out(json.dumps(line, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            collector.error = str(exc)

    record = collector.to_record()
    record.update(
        query=message, model=model, session_id=session.thread_id,
        user_id=session.user_id, latency_ms=int((time.perf_counter() - t0) * 1000),
    )
    record = flag_empty_completion(record)

    if emit:
        out(json.dumps({"type": "__tw_result__", **record}, ensure_ascii=False))
    elif output_format == "json":
        out(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        if record["final_text"]:
            console.print(record["final_text"])
        if record["error"]:
            console.print(f"[red]错误:[/red] {record['error']}")
    return 0 if record["completed"] else 1


async def _chat(
    message: str | None, qa_type: str, model_id: str | None, session_id: str | None,
    *, kb_collections: list[str] | None = None, web_search_enabled: bool = True,
) -> None:
    try:
        session = ChatSession(
            qa_type=qa_type, model_id=model_id, thread_id=session_id,
            kb_collections=kb_collections, web_search_enabled=web_search_enabled,
        )
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
