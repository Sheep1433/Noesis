"""首批内置命令 handler（A 类只读）。

导入本包即触发各 handler 模块的 ``@command`` 注册。新命令只需新增模块并在
此 import。
"""
from noesis.chat.commands.handlers import (  # noqa: F401
    agents,
    compact,
    help,
    model,
    skills,
    status,
)
