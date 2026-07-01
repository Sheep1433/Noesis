"""知识库上传暂存：解析前写入 ``.data/kb_uploads/``。"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from common.paths import data_path

_UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|]')


def sanitize_kb_filename(name: str) -> str:
    base = os.path.basename(name or "file").strip()
    base = _UNSAFE_NAME_RE.sub("_", base)
    return base or "file"


def sanitize_collection_name(name: str) -> str:
    safe = (name or "default").strip().replace("/", "_").replace("\\", "_")
    return safe or "default"


def staging_path(collection_name: str, file_hash: str, original_filename: str) -> Path:
    safe_col = sanitize_collection_name(collection_name)
    safe_name = sanitize_kb_filename(original_filename)
    fid = (file_hash or "")[:32] or "unknown"
    return data_path("kb_uploads", safe_col, f"{fid}_{safe_name}")


def write_staging(
    collection_name: str,
    content: bytes,
    original_filename: str,
) -> tuple[Path, str]:
    """写入暂存文件，返回 (路径, sha256 hex)。"""
    file_hash = hashlib.sha256(content).hexdigest()
    path = staging_path(collection_name, file_hash, original_filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path, file_hash
