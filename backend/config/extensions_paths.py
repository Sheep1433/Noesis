"""
仓库扩展目录路径解析（extensions/skills、extensions/mcp/docker-ssh）。

skills_filesystem_root 配置项仍可完整覆盖 Skills 根路径；
MCP 启动脚本可通过 MCP_DIR 环境变量覆盖。
"""
from __future__ import annotations

import os
from pathlib import Path

from config.env import OtherConfig

_CONFIG_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _CONFIG_DIR.parent

_SKILLS_SUBDIR = Path("skills")
_MCP_DOCKER_SSH_SUBDIR = Path("mcp") / "docker-ssh"


def repo_root() -> Path:
    """仓库根目录。开发时为 backend 的父目录；Docker 仅复制 backend 时为 backend 自身。"""
    if (_BACKEND_DIR.parent / "extensions").is_dir():
        return _BACKEND_DIR.parent
    if (_BACKEND_DIR / "extensions").is_dir():
        return _BACKEND_DIR
    return _BACKEND_DIR.parent


def extensions_root() -> Path:
    raw = (os.environ.get("EXTENSIONS_DIR") or "").strip()
    if raw:
        p = Path(raw)
        return p.resolve() if p.is_absolute() else (repo_root() / raw).resolve()
    return (repo_root() / "extensions").resolve()


def skills_root() -> Path:
    """Skills 磁盘根目录；other.skills_filesystem_root 优先于 extensions/skills。"""
    raw = (OtherConfig.skills_filesystem_root or "").strip()
    if raw:
        p = Path(raw)
        return p.resolve() if p.is_absolute() else (repo_root() / raw).resolve()
    return (extensions_root() / _SKILLS_SUBDIR).resolve()


def mcp_docker_ssh_dir() -> Path:
    """MCP docker-ssh 服务目录（含 server.py）。"""
    raw = (os.environ.get("MCP_DIR") or "").strip()
    if raw:
        p = Path(raw)
        return p.resolve() if p.is_absolute() else (repo_root() / raw).resolve()
    return (extensions_root() / _MCP_DOCKER_SSH_SUBDIR).resolve()
