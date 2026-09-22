"""/memory 路由 backend：FilesystemBackend 全量复用，仅 grep 覆写。

deepagents 的 grep 是字面量搜索（ripgrep -F），而记忆检索契约（与
``search_memory`` 工具一致，见 tests/test_user_memory_backend.py）要求
「合法正则按正则（忽略大小写）、否则字面子串」。此处只覆写 grep 的
匹配语义，read/write/edit/ls/glob/upload/download 全部继承原实现。

match 键遵循 ``GrepMatch`` 规范（``text``）：旧实现的 ``content`` 键
曾使 agent 侧 grep 工具在 /memory 路由上 KeyError（格式化层只读
``text``），随塌缩一并修正。
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import FileUploadResponse, GrepResult

from noesis.memory.policy import is_memory_writable, memory_key
from noesis.memory.store import compile_keyword_matcher

_GREP_MATCH_CAP = 50


class MemoryFilesystemBackend(FilesystemBackend):
    """/memory 挂载根专用：正则-or-字面 grep + 匹配上限。"""

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """upload 通道的白名单门卫。

        upload 不是模型可调的工具（引擎内部大结果落盘等使用），不经
        MemoryWriteMiddleware 的工具层拦截——白名单（索引/journal 只读、
        条目目录外不可写）须在此执行，与旧 GuardedFilesystemBackend 对齐。
        """
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                writable = is_memory_writable(memory_key(path))
            except ValueError:
                # 含 ../~ 的路径不炸整批，归一为该文件的错误响应
                # （对齐基类 per-file try/except 的部分成功契约）
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            if not writable:
                responses.append(FileUploadResponse(path=path, error="permission_denied"))
                continue
            responses.extend(super().upload_files([(path, content)]))
        return responses

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        try:
            base = self._resolve_path(path or "/")
        except ValueError:
            return GrepResult(matches=[])
        if not base.exists():
            return GrepResult(matches=[])

        if base.is_dir():
            files = sorted(p for p in base.rglob("*") if p.is_file())
        else:
            files = [base]
        matches_pattern = compile_keyword_matcher(pattern)
        matches: list[dict] = []
        for file_path in files:
            if glob is not None and not fnmatch(file_path.name, glob):
                continue
            try:
                if file_path.stat().st_size > self.max_file_size_bytes:
                    continue
                text = file_path.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeDecodeError):
                continue
            key = "/" + str(file_path.relative_to(self.cwd)).replace("\\", "/")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if matches_pattern(line):
                    matches.append(
                        {"path": key, "line": line_no, "text": line.strip()}
                    )
        return GrepResult(matches=matches[:_GREP_MATCH_CAP])


__all__ = ["MemoryFilesystemBackend"]
