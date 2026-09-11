"""read_before_write 中间件的预读判定测试：新文件不得因后端错误文案差异被误拒。"""

import pytest

from noesis.agents.middlewares.read_before_write_middleware import (
    WriteRejectedError,
    _hash_read_result,
)


class _ErrResult:
    def __init__(self, error):
        self.error = error


class _OkResult:
    file_data = {"content": "hello"}
    error = None


def test_not_found_message_variants_allow_new_file():
    # docker 沙箱的结构化错误码
    assert _hash_read_result(_ErrResult("file_not_found: /a.md"), "/a.md") is None
    assert _hash_read_result(_ErrResult("path_not_found: /a.md"), "/a.md") is None
    # local_shell 的 FilesystemBackend 文案（曾致全部新建文件被拒）
    assert _hash_read_result(
        _ErrResult("File '/writetest.md' not found"), "/workspace/writetest.md"
    ) is None
    assert _hash_read_result(
        _ErrResult("[Errno 2] No such file or directory: 'a.md'"), "/workspace/a.md"
    ) is None


def test_other_read_errors_still_reject():
    with pytest.raises(WriteRejectedError, match="cannot verify"):
        _hash_read_result(_ErrResult("Permission denied"), "/a.md")
    with pytest.raises(WriteRejectedError, match="cannot verify"):
        _hash_read_result(_ErrResult("directory not accessible"), "/a.md")


def test_existing_file_hashed():
    h = _hash_read_result(_OkResult(), "/a.md")
    assert h is not None and len(h) == 64
