"""
L1 原子工具：read / grep / glob / bash
参数与截断策略对齐 Claude Code（见文件系统工具对比分析.md）。
成功返回: {"success": true, "data": {...}}
失败通过 raise MCPError 子类由 FastMCP 转为协议级错误响应。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from config import VCS_DIRECTORIES_TO_EXCLUDE, get_config
from executor import exec_remote
from utils.errors import (
    CommandExecutionError,
    InternalError,
    PathNotFoundError,
)
from utils.output_handler import (
    CommandResult,
    apply_head_limit,
    compress_long_lines,
    success_result,
    truncate_chars,
)

GrepOutputMode = Literal["content", "files_with_matches", "count"]

# Claude Code FileReadTool device path blacklist (subset)
BLOCKED_DEVICE_PATHS = {
    "/dev/zero",
    "/dev/random",
    "/dev/urandom",
    "/dev/full",
}

# Common ripgrep --type mappings for remote grep --include
_TEXT_MIME_EXACT = frozenset({
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
    "application/javascript",
    "application/sql",
    "inode/x-empty",
})

GREP_TYPE_INCLUDES: Dict[str, str] = {
    "py": "*.py",
    "js": "*.js",
    "ts": "*.ts",
    "rust": "*.rs",
    "go": "*.go",
    "java": "*.java",
    "cpp": "*.{cpp,h,hpp}",
    "c": "*.{c,h}",
    "rb": "*.rb",
    "php": "*.php",
    "swift": "*.swift",
    "kotlin": "*.kt",
}


def _require_ip(ip: str) -> None:
    if not ip:
        raise InternalError("ip is required")


def _ssh_user_port(username: str) -> tuple[str, int]:
    cfg = get_config()
    return username or cfg.ssh.default_user, cfg.ssh.default_port


def _is_text_mime(mime: str) -> bool:
    if mime.startswith("text/"):
        return True
    if mime in _TEXT_MIME_EXACT:
        return True
    return mime.endswith("+json") or mime.endswith("+xml")


def _assert_text_file(path: str, ip: str, user: str, port: int) -> None:
    """Reject binary/non-text files before reading."""
    probe = exec_remote(
        ip,
        user,
        port,
        f"file -b --mime-type '{path}' 2>/dev/null || echo application/octet-stream",
    )
    mime = probe.stdout.strip().lower()
    if not _is_text_mime(mime):
        raise InternalError(
            f"Refusing to read non-text file (mime={mime}): {path}. "
            "This tool only supports text files."
        )


def _grep_exclude_dirs_flags() -> str:
    return " ".join(f"--exclude-dir={d}" for d in VCS_DIRECTORIES_TO_EXCLUDE)


def _find_vcs_prune_expr() -> str:
    prune_paths = " -o ".join(f"-path '*/{d}/*'" for d in VCS_DIRECTORIES_TO_EXCLUDE)
    return f"\\( {prune_paths} \\) -prune -o"


def _build_grep_base_flags(
    *,
    ignore_case: bool,
    multiline: bool,
    output_mode: GrepOutputMode,
    show_line_numbers: bool,
    context_before: Optional[int],
    context_after: Optional[int],
    context: Optional[int],
    context_c: Optional[int],
) -> List[str]:
    flags: List[str] = ["grep", "-r"]
    if multiline:
        flags.append("-P")
    else:
        flags.append("-E")
    if ignore_case:
        flags.append("-i")
    flags.append(_grep_exclude_dirs_flags())

    if output_mode == "files_with_matches":
        flags.append("-l")
    elif output_mode == "count":
        flags.append("-c")
    elif show_line_numbers:
        flags.append("-n")

    if output_mode == "content":
        if context is not None:
            flags.extend(["-C", str(context)])
        elif context_c is not None:
            flags.extend(["-C", str(context_c)])
        else:
            if context_before is not None:
                flags.extend(["-B", str(context_before)])
            if context_after is not None:
                flags.extend(["-A", str(context_after)])

    return flags


def read(
    path: str,
    ip: str,
    offset: int = 1,
    limit: Optional[int] = None,
    username: str = "root",
) -> Dict[str, Any]:
    """
    Read text file content from a remote host over SSH (text files only).

    Args:
        path: Absolute path to the text file on the remote host.
        ip: Remote host IP address (required).
        offset: 1-indexed starting line (default 1).
        limit: Maximum lines to read (optional; default from config).
        username: SSH username (default "root").
    """
    _require_ip(ip)

    if path in BLOCKED_DEVICE_PATHS or path.startswith("/proc/self/fd/"):
        raise InternalError(f"Blocked device path: {path}")

    cfg = get_config()
    user, port = _ssh_user_port(username)
    start_line = max(1, offset)
    effective_limit = limit if limit is not None else cfg.tool_limits.read_default_limit

    _assert_text_file(path, ip, user, port)

    command = (
        f"if [ ! -f '{path}' ]; then echo 'no such file or directory: {path}' >&2; exit 1; fi; "
        f"tail -n +{start_line} '{path}' | head -n {effective_limit}"
    )

    result = exec_remote(ip, user, port, command)

    stderr_lower = result.stderr.lower()
    if result.exit_code != 0 and any(
        kw in stderr_lower for kw in ("no such file", "cannot access", "not a regular file")
    ):
        raise PathNotFoundError(f"File not found: {path}")

    content = result.stdout

    if not content:
        return success_result({
            "content": "",
            "lines": 0,
            "truncated": False,
            "offset": start_line,
            "limit": effective_limit,
        }, empty=True)

    content, line_truncated = compress_long_lines(content, cfg.tool_limits.read_max_line_chars)
    lines = content.splitlines()
    truncated = len(lines) >= effective_limit or line_truncated

    return success_result({
        "content": content,
        "lines": len(lines),
        "truncated": truncated,
        "offset": start_line,
        "limit": effective_limit,
    })


def grep(
    pattern: str,
    ip: str,
    path: str = ".",
    glob: Optional[str] = None,
    output_mode: GrepOutputMode = "files_with_matches",
    context_before: Optional[int] = None,
    context_after: Optional[int] = None,
    context: Optional[int] = None,
    context_c: Optional[int] = None,
    show_line_numbers: bool = True,
    ignore_case: bool = False,
    file_type: Optional[str] = None,
    head_limit: Optional[int] = None,
    offset: int = 0,
    multiline: bool = False,
    username: str = "root",
) -> Dict[str, Any]:
    """
    Search file contents on a remote host (regex; aligned with Claude Code GrepTool).

    Default output_mode is files_with_matches; head_limit defaults to 250.
    """
    _require_ip(ip)

    cfg = get_config()
    user, port = _ssh_user_port(username)
    limits = cfg.tool_limits

    effective_pattern = f"(?s){pattern}" if multiline else pattern
    grep_flags = _build_grep_base_flags(
        ignore_case=ignore_case,
        multiline=multiline,
        output_mode=output_mode,
        show_line_numbers=show_line_numbers,
        context_before=context_before,
        context_after=context_after,
        context=context,
        context_c=context_c,
    )

    include_glob = glob
    if not include_glob and file_type:
        include_glob = GREP_TYPE_INCLUDES.get(file_type.lower())

    include_flag = f"--include='{include_glob}' " if include_glob else ""
    grep_cmd = " ".join(grep_flags) + f" {include_flag}-e '{effective_pattern}'"

    if glob and not file_type:
        command = (
            f"find '{path}' -maxdepth 5 {_find_vcs_prune_expr()} -type f -name '{glob}' -print 2>/dev/null | "
            f"head -n 5000 | xargs -r {grep_cmd}"
        )
    else:
        command = f"{grep_cmd} '{path}'"

    result = exec_remote(ip, user, port, command)

    if result.exit_code == 1:
        return success_result(_empty_grep_data(output_mode), empty=True)
    if result.exit_code != 0:
        raise CommandExecutionError(f"grep failed: {result.stderr}")

    raw_lines = [line for line in result.stdout.splitlines() if line]
    default_head = limits.grep_head_limit

    if output_mode == "files_with_matches":
        filenames, applied_limit, truncated = apply_head_limit(
            raw_lines, head_limit, offset, default_head
        )
        content_str = "\n".join(filenames)
        content_str, char_truncated = truncate_chars(content_str, limits.grep_max_result_chars)
        return success_result({
            "mode": output_mode,
            "numFiles": len(filenames),
            "filenames": filenames,
            "content": content_str,
            "truncated": truncated or char_truncated,
            **({"appliedLimit": applied_limit} if applied_limit is not None else {}),
            **({"appliedOffset": offset} if offset > 0 else {}),
        }, empty=len(filenames) == 0)

    if output_mode == "count":
        count_lines, applied_limit, truncated = apply_head_limit(
            raw_lines, head_limit, offset, default_head
        )
        total_matches = 0
        file_count = 0
        for line in count_lines:
            colon_idx = line.rfind(":")
            if colon_idx > 0:
                try:
                    total_matches += int(line[colon_idx + 1 :])
                    file_count += 1
                except ValueError:
                    continue
        content_str = "\n".join(count_lines)
        content_str, char_truncated = truncate_chars(content_str, limits.grep_max_result_chars)
        return success_result({
            "mode": output_mode,
            "numFiles": file_count,
            "filenames": [],
            "content": content_str,
            "numMatches": total_matches,
            "truncated": truncated or char_truncated,
            **({"appliedLimit": applied_limit} if applied_limit is not None else {}),
            **({"appliedOffset": offset} if offset > 0 else {}),
        }, empty=total_matches == 0)

    # content mode
    content_lines, applied_limit, truncated = apply_head_limit(
        raw_lines, head_limit, offset, default_head
    )
    content_str = "\n".join(content_lines)
    content_str, char_truncated = truncate_chars(content_str, limits.grep_max_result_chars)

    filenames: List[str] = []
    for line in content_lines:
        parts = line.split(":", 1)
        if parts and parts[0] not in filenames:
            filenames.append(parts[0])

    return success_result({
        "mode": output_mode,
        "numFiles": len(filenames),
        "filenames": filenames,
        "content": content_str,
        "numLines": len(content_lines),
        "numMatches": len(content_lines),
        "truncated": truncated or char_truncated,
        **({"appliedLimit": applied_limit} if applied_limit is not None else {}),
        **({"appliedOffset": offset} if offset > 0 else {}),
    }, empty=len(content_lines) == 0)


def _empty_grep_data(output_mode: GrepOutputMode) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "mode": output_mode,
        "numFiles": 0,
        "filenames": [],
        "truncated": False,
    }
    if output_mode == "content":
        base.update({"content": "", "numLines": 0, "numMatches": 0})
    elif output_mode == "count":
        base.update({"content": "", "numMatches": 0})
    else:
        base["content"] = ""
    return base


def glob(
    pattern: str,
    ip: str,
    path: str = ".",
    username: str = "root",
) -> Dict[str, Any]:
    """
    Match files by glob pattern on a remote host (aligned with Claude Code GlobTool).

    Truncates to 100 files and 100_000 chars by default.
    """
    _require_ip(ip)

    cfg = get_config()
    user, port = _ssh_user_port(username)
    max_files = cfg.tool_limits.glob_max_files

    command = (
        f"find '{path}' {_find_vcs_prune_expr()} -type f -name '{pattern}' -print 2>/dev/null | "
        f"head -n {max_files + 1}"
    )
    result = exec_remote(ip, user, port, command)

    raw = result.stdout.strip()
    all_files = raw.splitlines() if raw else []
    truncated = len(all_files) > max_files
    filenames = all_files[:max_files]

    listing = "\n".join(filenames)
    listing, char_truncated = truncate_chars(listing, cfg.tool_limits.glob_max_result_chars)

    return success_result({
        "numFiles": len(filenames),
        "filenames": filenames,
        "truncated": truncated or char_truncated,
    }, empty=len(filenames) == 0)


def bash(
    command: str,
    ip: str,
    timeout_ms: Optional[int] = None,
    username: str = "root",
) -> Dict[str, Any]:
    """
    Execute a shell command on a remote host over SSH.

    timeout_ms aligns with Claude Code BashTool (default 120_000 ms).
    Output truncated to 30_000 chars per stream.
    """
    _require_ip(ip)

    cfg = get_config()
    user, port = _ssh_user_port(username)
    effective_timeout_ms = (
        timeout_ms if timeout_ms is not None else cfg.tool_limits.bash_default_timeout_ms
    )
    timeout_sec = max(1, (effective_timeout_ms + 999) // 1000)

    result = exec_remote(ip, user, port, command, timeout=timeout_sec)

    stdout, stdout_truncated = truncate_chars(result.stdout, cfg.tool_limits.bash_max_result_chars)
    stderr, stderr_truncated = truncate_chars(result.stderr, cfg.tool_limits.bash_max_result_chars)

    return success_result({
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": result.exit_code,
        "timed_out": False,
        "truncated": stdout_truncated or stderr_truncated,
        "timeout_ms": effective_timeout_ms,
    })
