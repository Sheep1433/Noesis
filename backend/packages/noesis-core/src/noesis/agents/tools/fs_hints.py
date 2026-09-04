"""文件系统工具描述下沉的运行规则（原 system prompt `<model_operational>` 段）。

规则跟工具走：cwd 与 Shell 约定归 execute，读后改归 edit_file，落盘目录
约定归 write_file——system prompt 不再重复。只在描述尾部追加，不改参数
schema；目标工具缺失时静默跳过（与 replace_execute_tool 同约定）。
"""

from __future__ import annotations

from typing import Any

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
