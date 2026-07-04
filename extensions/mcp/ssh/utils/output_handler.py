from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TypeVar

from config import get_config

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Unified success wrapper (工具成功时返回 dict，由 FastMCP 序列化)
# ---------------------------------------------------------------------------


def success_result(data: Any, empty: bool = False) -> Dict[str, Any]:
    """
    Wrap successful result in unified format.

    Args:
        data: Serializable result data.
        empty: True if result is empty (e.g. no matches found).

    Returns:
        {"success": true, "data": ..., "empty": bool (optional)}
    """
    result: Dict[str, Any] = {"success": True, "data": data}
    if empty:
        result["empty"] = True
    return result


def get_result_data(result: Dict[str, Any]) -> Any:
    """Extract data payload from a successful tool result."""
    return result["data"]


def truncate_chars(text: str, max_chars: int) -> tuple[str, bool]:
    """Truncate text to max_chars (UTF-8 safe). Returns (text, truncated)."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    encoded = text.encode("utf-8")
    if len(encoded) <= max_chars:
        return text, False
    chunk = encoded[:max_chars]
    while chunk and chunk[-1] & 0x80 == 0x80:
        chunk = chunk[:-1]
        if not chunk:
            break
    try:
        return chunk.decode("utf-8"), True
    except UnicodeDecodeError:
        return chunk.decode("utf-8", errors="replace"), True


def apply_head_limit(
    items: List[T],
    head_limit: Optional[int],
    offset: int = 0,
    default_limit: int = 250,
) -> tuple[List[T], Optional[int], bool]:
    """
    Apply offset + head_limit like Claude Code grep/glob.

    head_limit=0 means unlimited. None uses default_limit.
    Returns (items, applied_limit, truncated).
    """
    if head_limit == 0:
        sliced = items[offset:]
        return sliced, None, False

    effective_limit = head_limit if head_limit is not None else default_limit
    sliced = items[offset : offset + effective_limit]
    truncated = len(items) - offset > effective_limit
    applied_limit = effective_limit if truncated else None
    return sliced, applied_limit, truncated


def compress_long_lines(content: str, max_line_chars: int) -> tuple[str, bool]:
    """Compress lines longer than max_line_chars (Claude Code read long-line handling)."""
    if not content:
        return content, False
    truncated = False
    lines: List[str] = []
    for line in content.splitlines():
        if len(line) > max_line_chars:
            lines.append(line[:max_line_chars] + "… [line truncated]")
            truncated = True
        else:
            lines.append(line)
    return "\n".join(lines), truncated


# ---------------------------------------------------------------------------
# CommandResult and OutputHandler
# ---------------------------------------------------------------------------


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int


class OutputHandler:
    def __init__(self, max_size: Optional[int] = None, max_lines: Optional[int] = None):
        cfg = get_config()
        self.max_size = max_size if max_size is not None else cfg.execution.max_output_size
        self.max_lines = max_lines if max_lines is not None else cfg.execution.max_output_lines

    def truncate(self, output: str) -> str:
        """Truncate output by line count, then by byte size, respecting UTF-8 boundaries."""
        if not output:
            return output

        lines = output.splitlines()
        if len(lines) > self.max_lines:
            lines = lines[: self.max_lines]
            truncated = "\n".join(lines)
        else:
            truncated = output

        if len(truncated.encode("utf-8")) > self.max_size:
            truncated = self._truncate_utf8_boundary(truncated.encode("utf-8"))

        return truncated

    def truncate_result(self, result: CommandResult) -> CommandResult:
        return CommandResult(
            stdout=self.truncate(result.stdout),
            stderr=self.truncate(result.stderr),
            exit_code=result.exit_code,
        )

    def _truncate_utf8_boundary(self, data: bytes) -> str:
        """Truncate bytes to max_size ensuring we cut at a valid UTF-8 character boundary."""
        chunk = data[: self.max_size]
        while chunk and chunk[-1] & 0x80 == 0x80:
            truncated = chunk[:-1]
            if not truncated:
                break
            try:
                return truncated.decode("utf-8")
            except UnicodeDecodeError:
                chunk = truncated
        return chunk.decode("utf-8", errors="replace")
