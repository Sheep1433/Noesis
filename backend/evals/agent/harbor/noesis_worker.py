"""Harbor 子进程 Worker：在 backend venv 内运行 Noesis Agent。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import HumanMessage

from agent.profiles.base_agent import DEFAULT_RECURSION_LIMIT
from agent.factory import create_noesis_agent
from config.checkpointer import get_checkpointer
from evals.agent.harbor.harbor_proxy_client import resolve_container_working_dir_via_proxy
from evals.agent.harbor.proxy_backend import ProxyHarborBackend
from evals.agent.harbor.noesis_runner import (
    HarborRunCollector,
    build_harbor_system_prompt,
    resolve_harbor_llm,
    write_run_artifacts,
)
from evals.bootstrap import eval_runtime


async def _run_worker(
    *,
    proxy_host: str,
    proxy_port: int,
    instruction: str,
    model_name: str | None,
    session_id: str,
    logs_dir: Path,
) -> int:
    working_dir = await resolve_container_working_dir_via_proxy(proxy_host, proxy_port)
    backend = ProxyHarborBackend(
        host=proxy_host,
        port=proxy_port,
        cwd=working_dir,
        session_id=session_id,
    )
    llm = resolve_harbor_llm(model_name)
    resolved_model_name = model_name or "opencode/deepseek-v4-flash-free"

    collector = HarborRunCollector(
        instruction=instruction,
        model_name=resolved_model_name,
    )
    collector.add_user_step()

    async with eval_runtime():
        agent = create_noesis_agent(
            tools=[],
            system_prompt=build_harbor_system_prompt(working_dir=working_dir),
            checkpointer=get_checkpointer(),
            backend=backend,
            extra_middleware=[TodoListMiddleware()],
            model=llm,
        )
        config = {
            "configurable": {"thread_id": session_id},
            "recursion_limit": DEFAULT_RECURSION_LIMIT,
        }
        try:
            async for event in agent.astream_events(
                {"messages": [HumanMessage(content=instruction)]},
                config=config,
                version="v2",
            ):
                collector.consume(event)
        except Exception as exc:
            collector.error = str(exc)
            write_run_artifacts(
                logs_dir=logs_dir,
                session_id=session_id,
                collector=collector,
            )
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    write_run_artifacts(
        logs_dir=logs_dir,
        session_id=session_id,
        collector=collector,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Noesis Harbor worker")
    parser.add_argument("--proxy-host", required=True)
    parser.add_argument("--proxy-port", type=int, required=True)
    parser.add_argument("--instruction-file", required=True)
    parser.add_argument("--logs-dir", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--model-name", default="")
    args = parser.parse_args()

    instruction = Path(args.instruction_file).read_text(encoding="utf-8")
    logs_dir = Path(args.logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    model_name = args.model_name.strip() or None
    return asyncio.run(
        _run_worker(
            proxy_host=args.proxy_host,
            proxy_port=args.proxy_port,
            instruction=instruction,
            model_name=model_name,
            session_id=args.session_id,
            logs_dir=logs_dir,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
