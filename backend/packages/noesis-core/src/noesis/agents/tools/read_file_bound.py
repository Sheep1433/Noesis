"""read_file 源头输出封顶：新鲜读取不进入预算中间件的事后替换。

deepagents 的 read_file 源头截断开关（``tool_token_limit_before_evict``）与
「结果驱逐到 artifact」共用一个参数。Noesis 在 stack 装配时传 ``None`` 关闭
驱逐——预算中间件单点拥有替换语义（保留 status/errorCategory/outcome）——
这同时让 read_file 失去了源头截断：500 行读取约 30k 字符，必超 24k 单条
预算被事后卸载，模型转而读 artifact 又超限，形成卸载追逐循环。

这里在 Noesis 层单独给 read_file 封顶：截断发生在源头、截断内容直接
可见（模型按行号用 offset 续读），与预算中间件职责不重叠——上限取
预算的 ~85%，正常读取永远不会触发事后替换。
禁用 ``from __future__ import annotations``：langchain 的
``StructuredTool._injected_args_keys`` 只检查 ``signature()`` 的原始注解对象
（不做字符串解析），注解一旦被延迟成字符串 "ToolRuntime"，注入键探测不到，
``_parse_input`` 会按 args_schema 剥掉注入的 runtime，真机调用即报
``missing 1 required positional argument: 'runtime'``（2026-09-03 问题 6）。
"""

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool

from noesis.runtime.logging import logger

_TRUNCATION_NOTICE = (
    "\n\n[Output was truncated at {max_chars} characters due to size limits. "
    "Line numbers are preserved — use the offset parameter to continue "
    "reading from the last line shown.]"
)


def _bound_tool_message(message: ToolMessage, max_chars: int) -> ToolMessage:
    content = message.content
    if not isinstance(content, str) or len(content) <= max_chars:
        return message
    notice = _TRUNCATION_NOTICE.format(max_chars=max_chars)
    bounded = content[: max_chars - len(notice)] + notice
    return message.model_copy(update={"content": bounded})


def apply_read_file_bound(filesystem_middleware: Any, *, max_chars: int) -> None:
    """原地包装 FilesystemMiddleware 的 read_file 工具（schema 不变）。

    找不到 read_file 工具时静默跳过（与 replace_execute_tool 同约定）。
    """
    tools = getattr(filesystem_middleware, "tools", None) or []
    for index, tool in enumerate(tools):
        if getattr(tool, "name", None) != "read_file":
            continue
        original: StructuredTool = tool

        # langgraph ToolNode 只按 func 签名决定注入：runtime 参数名（或 ToolRuntime 注解）
        # 必须保留在包装函数上，否则原函数收不到 runtime（与 replace_execute_tool 同约定）。
        def read_file_bounded(runtime: ToolRuntime, **kwargs: Any) -> ToolMessage:
            return _bound_tool_message(original.func(**kwargs, runtime=runtime), max_chars)

        async def aread_file_bounded(runtime: ToolRuntime, **kwargs: Any) -> ToolMessage:
            return _bound_tool_message(await original.coroutine(**kwargs, runtime=runtime), max_chars)

        tools[index] = StructuredTool.from_function(
            name="read_file",
            description=original.description or "",
            func=read_file_bounded,
            coroutine=aread_file_bounded,
            infer_schema=False,
            args_schema=original.args_schema,
        )
        logger.info("read_file source output bound to {} chars", max_chars)
        return
    logger.debug("read_file tool not found, skip output bound")


__all__ = ["apply_read_file_bound"]
