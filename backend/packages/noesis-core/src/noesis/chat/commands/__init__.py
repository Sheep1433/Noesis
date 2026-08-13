"""统一跨端斜杠命令层。

信道无关的命令注册表与分发器：所有通道（web / telegram / feishu / wechat / cli）
的斜杠命令在此唯一解析、唯一实现。加一个新命令只需 ``@command`` 装饰器，无需
碰任何 adapter 或通道入口。

设计参考：Hermes gateway 的「信道无关命令层」（见
``Interview/highlights/SSE/multichannel_demo.py``）。
"""
from __future__ import annotations

# 导入 handlers 包即触发各 handler 模块的 @command 注册（进程级一次）。
from noesis.chat.commands import handlers  # noqa: F401

