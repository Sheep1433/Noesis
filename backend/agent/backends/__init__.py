"""Agent 文件系统 / Shell 后端工厂。"""

from agent.backends.local_shell import create_local_shell_backend

__all__ = ["create_local_shell_backend"]
