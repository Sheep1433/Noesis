"""文件原子写辅助：temp + ``os.replace``，读方永远看到完整文件。

共享数据目录（``.noesis/`` 多实例 bind 同源）上的直写目标路径是非原子的：
并发写同一文件时，读方可能拿到半截内容；覆盖写则可能互相截断。写入同
目录临时文件后原子改名，读方要么看到旧文件要么看到新文件，无中间态。
临时文件名带 uuid，跨进程与同进程多线程都不互踩。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """原子写字节：同目录临时文件 + ``os.replace``。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """原子写文本（``atomic_write_bytes`` 的文本封装）。"""
    atomic_write_bytes(path, text.encode(encoding))


__all__ = ["atomic_write_bytes", "atomic_write_text"]
