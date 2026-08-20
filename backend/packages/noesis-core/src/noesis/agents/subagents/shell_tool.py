"""后台命令任务：替换 FilesystemMiddleware 的 ``execute`` 工具。

单工具 + ``run_in_background`` 参数（默认 false，前台行为零变化）：

- ``run_in_background=false``：原样委托给被替换的原工具（timeout 校验、
  输出格式、错误语义均不变）。
- ``run_in_background=true``：``executor.start_shell(...)`` 立即返回
  task_id，命令作为 ``kind="shell"`` 任务进现有注册表 / 状态机 / 完成通知
  / 前端任务面板管线。

必须保留工具名 ``execute``：``interrupt_on["execute"]``（危险 Shell 命令
审批）按名匹配，替换后审批继续发生在启动前——HITL 与后台化天然组合。
后台化只发生在工具层；backend 接口与文件系统工具（ls/read/write/edit/
glob/grep）不动。
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from noesis.runtime.logging import logger

# 长命令后台化提示（工具描述与返回文本共用同一语义）
_BACKGROUND_HINT = (
    "Set run_in_background=true for long-running commands (expected to take "
    "tens of seconds or more): the call returns a task id immediately; "
    "collect its exit code and output tail with check_task later."
)


class _ExecuteWithBackgroundSchema(BaseModel):
    """execute 工具入参：原 schema + run_in_background。"""

    command: str = Field(description="Shell command to execute in the sandbox environment.")

    timeout: Optional[int] = Field(
        default=None,
        description="Optional timeout in seconds for this command. Overrides the default timeout. Use 0 for no-timeout execution on backends that support it.",
    )

    run_in_background: bool = Field(
        default=False,
        description=(
            "Run in the background and return a task id immediately "
            "(collect with check_task). Default false runs synchronously. "
            + _BACKGROUND_HINT
        ),
    )


def replace_execute_tool(
    filesystem_middleware: Any,
    *,
    executor: Any,
    backend: Any,
    session_id: str,
    user_id: str,
) -> None:
    """原地替换 FilesystemMiddleware 生成的 execute 工具（同名 + 参数）。

    找不到 execute 工具（backend 无执行能力）时静默跳过。
    """
    tools = getattr(filesystem_middleware, "tools", None) or []
    original: Optional[StructuredTool] = None
    for index, tool in enumerate(tools):
        if getattr(tool, "name", None) == "execute":
            original = tool
            original_index = index
            break
    if original is None:
        logger.debug("execute tool not found, skip background replacement")
        return

    original_description = original.description or ""

    def _reject_background(reason: str, tool_call_id: str) -> str:
        return f"后台命令启动失败：{reason}；请改用前台执行（run_in_background=false）。（tool_call_id={tool_call_id}）"

    async def aexecute_bg(
        command: Annotated[str, "Shell command to execute in the sandbox environment."],
        runtime: ToolRuntime,
        timeout: Optional[int] = None,
        run_in_background: bool = False,
    ):
        if run_in_background:
            try:
                task_id = executor.start_shell(
                    command=command,
                    backend=backend,
                    session_id=session_id,
                    user_id=user_id,
                )
            except ValueError as exc:
                return _reject_background(str(exc), runtime.tool_call_id)
            return (
                f"后台命令任务已启动：{task_id}\n"
                "无需等待——可继续其他工作，之后用 check_task 收取 exit code 与输出尾部。"
            )
        # 前台：直接调原工具协程（runtime 透传），行为与替换前完全一致
        if timeout is not None:
            return await original.coroutine(
                command=command, runtime=runtime, timeout=timeout,
            )
        return await original.coroutine(command=command, runtime=runtime)

    def execute_bg(
        command: Annotated[str, "Shell command to execute in the sandbox environment."],
        runtime: ToolRuntime,
        timeout: Optional[int] = None,
        run_in_background: bool = False,
    ):
        if run_in_background:
            try:
                task_id = executor.start_shell(
                    command=command,
                    backend=backend,
                    session_id=session_id,
                    user_id=user_id,
                )
            except ValueError as exc:
                return _reject_background(str(exc), runtime.tool_call_id)
            return (
                f"后台命令任务已启动：{task_id}\n"
                "无需等待——可继续其他工作，之后用 check_task 收取 exit code 与输出尾部。"
            )
        if timeout is not None:
            return original.func(command=command, runtime=runtime, timeout=timeout)
        return original.func(command=command, runtime=runtime)

    replacement = StructuredTool.from_function(
        name="execute",
        description=(original_description + "\n\n" + _BACKGROUND_HINT).strip(),
        func=execute_bg,
        coroutine=aexecute_bg,
        infer_schema=False,
        args_schema=_ExecuteWithBackgroundSchema,
    )
    tools[original_index] = replacement
    logger.info(
        "execute tool replaced with background-capable variant session_id={}",
        session_id,
    )


__all__ = ["replace_execute_tool"]
