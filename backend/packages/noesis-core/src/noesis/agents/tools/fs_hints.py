"""文件系统工具描述下沉的运行规则（原 system prompt `<model_operational>` 段）。

规则跟工具走：cwd 与 Shell 约定归 execute，读后改归 edit_file，落盘目录
约定归 write_file——system prompt 不再重复。只在描述尾部追加，不改参数
schema；目标工具缺失时静默跳过（与 replace_execute_tool 同约定）。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

_EXECUTE_HINT = (
    "\n\nSandbox notes: every call starts with cwd=/workspace; prefer relative "
    "paths for artifacts, and chain cd dependencies inside one command with && "
    "(cwd does not persist across calls). Use non-interactive flags (-y/--yes) "
    "to avoid hangs. Memory files (/memory/) are only accessible via the memory "
    "tools, not from the shell."
)
_EDIT_HINT = (
    "\n\nRead the file first (read_file or grep) to confirm its current content "
    "before editing, unless you created or edited it earlier in this session."
)
_WRITE_HINT = (
    "\n\nWrite task artifacts under /workspace/ (root or a task-specific "
    "subdirectory); use /workspace/research/ only for research-style work such "
    "as deep-research skills."
)

_HINTS = {
    "execute": _EXECUTE_HINT,
    "edit_file": _EDIT_HINT,
    "write_file": _WRITE_HINT,
}


def augment_filesystem_tool_descriptions(filesystem_middleware: Any) -> None:
    """向 FilesystemMiddleware 的 execute / edit_file / write_file 追加运行规则描述。"""
    tools = getattr(filesystem_middleware, "tools", None) or []
    for tool in tools:
        hint = _HINTS.get(getattr(tool, "name", None))
        if hint is None:
            continue
        description = tool.description or ""
        if hint.strip() in description:
            continue
        tool.description = (description + hint).strip()


__all__ = ["augment_filesystem_tool_descriptions"]


def guard_worker_filesystem_tools(filesystem_middleware: Any) -> None:
    """task-worker 专用：execute 的危险命令（网络类）确定性拒绝。

    后台任务无人值守——等待审批是反模式（最坏情形挂起至超时拒绝）。
    危险命令不执行、不挂起，返回结构化拒绝文本：worker 据此改道或如实
    说明限制，拒绝事实随任务结果回流，主 Agent 可在主 run（用户在场、
    审批 UX 自然）中升级执行。找不到 execute（backend 无执行能力）跳过。
    """
    from noesis.agents.guardrails.policy import is_dangerous_execute

    tools = getattr(filesystem_middleware, "tools", None) or []
    original: Any = None
    for index, tool in enumerate(tools):
        if getattr(tool, "name", None) == "execute":
            original = tool
            original_index = index
            break
    if original is None:
        return

    def _deny(command: str) -> str | None:
        if is_dangerous_execute(str(command or "")):
            return (
                "后台任务不允许执行需审批的网络类命令（命令未执行）。"
                "请改用无需联网的本地方案，或在结果中说明该限制——"
                "主 Agent 可在主对话中代为执行。"
            )
        return None

    async def aexecute_guarded(command: str, runtime: Any = None, timeout: Any = None):
        refused = _deny(command)
        if refused is not None:
            return refused
        return await original.coroutine(command=command, runtime=runtime, timeout=timeout)

    def execute_guarded(command: str, runtime: Any = None, timeout: Any = None):
        refused = _deny(command)
        if refused is not None:
            return refused
        return original.func(command=command, runtime=runtime, timeout=timeout)

    replacement = StructuredTool.from_function(
        name="execute",
        description=(
            (original.description or "")
            + "\nNetwork-dependent commands (curl/wget/pip install etc.) are "
            "denied in background tasks; state the limitation in your result."
        ).strip(),
        func=execute_guarded,
        coroutine=aexecute_guarded,
        infer_schema=False,
        args_schema=original.args_schema,
    )
    tools[original_index] = replacement
