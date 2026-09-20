"""记忆写入门卫 + 索引同步：``/memory`` 写入的引擎侧附加语义。

路由表只挂目录（见 backends.factory._memory_route_backend）；「哪些
路径可写」的白名单与「条目写入后同步 MEMORY.md 索引行」在这里：

- 白名单（noesis.memory.policy，单一事实来源）外的 /memory 写入直接
  拒绝——含 MEMORY.md 索引与 journal（引擎维护，模型只读）和白名单
  外的任意路径。拒绝原因回给模型，不触发 HITL 审批（guardrails 的
  ``memory_write_when`` 用同一份白名单判定，两边不会打架）。
- 条目（五类目录下命名合法的 .md）写入/编辑成功后，按 frontmatter
  投影同步索引行；同步失败不阻断已确认的写入，整理任务重建索引兜底。
"""

from __future__ import annotations

from typing import Any, Callable

from langchain.agents.middleware.types import AgentMiddleware, ContextT, ResponseT
from langgraph.prebuilt.tool_node import ToolCallRequest

from noesis.runtime.logging import logger
from noesis.errors.tool_failure import ToolValidationError
from noesis.memory.policy import (
    MEMORY_ENTRY_RE,
    MEMORY_INDEX_FILE,
    MEMORY_TYPE_DIRS,
    is_memory_entry,
    is_memory_writable,
    strip_memory_route,
)
from noesis.memory.store import IndexEntry, MemoryStore

WRITE_TOOL_NAMES = frozenset({"edit_file", "write_file", "write", "edit"})

# 旧 backend 时代的等价拒绝码：可读不可写 = permission_denied，白名单外
# = file_not_found（不可见）。这里保留语义但给出原因，模型能自纠
_READABLE_NOT_WRITABLE_MESSAGE = (
    "用户记忆该路径只读（索引 MEMORY.md 与 journal 由引擎维护）："
    "条目请写入 /memory/{type}/<slug>.md，根文件仅 AGENTS.md / USER.md"
)
_INVISIBLE_PATH_MESSAGE = (
    "用户记忆路径不在白名单内：仅根文件（AGENTS.md / USER.md）与五类"
    "条目目录（{types}）可写"
)


class MemoryWriteRejected(ToolValidationError):
    """白名单外的 /memory 写入（继承 ToolValidationError：归类 invalid_arguments）。"""


def _successful(result: Any) -> bool:
    """兼容两层结果形状：ToolMessage（.status，经中间件链）与裸
    WriteResult/EditResult（.error，直调 backend 的调用方/测试）。"""
    if getattr(result, "status", None) == "error":
        return False
    return getattr(result, "error", None) is None


class MemoryWriteMiddleware(
    AgentMiddleware[Any, ContextT, ResponseT]
):
    """挂在挂载了可写 /memory 路由的 Agent 栈上（主 Agent 与同步子 Agent）。"""

    def __init__(self, *, user_id: str) -> None:
        self._user_id = str(user_id)

    def _memory_key(self, request: ToolCallRequest) -> str | None:
        """写工具的目标路径 → /memory 路由内键；路由外返回 None。"""
        if str(request.tool_call.get("name") or "") not in WRITE_TOOL_NAMES:
            return None
        args = request.tool_call.get("args") or {}
        path = args.get("file_path") or args.get("path")
        if not isinstance(path, str) or not path:
            return None
        return strip_memory_route(path)

    def _gate(self, key: str) -> None:
        if is_memory_writable(key):
            return
        name = key.lstrip("/")
        if name == MEMORY_INDEX_FILE or name.startswith("journal/"):
            raise MemoryWriteRejected(_READABLE_NOT_WRITABLE_MESSAGE)
        raise MemoryWriteRejected(
            _INVISIBLE_PATH_MESSAGE.format(types="/".join(sorted(MEMORY_TYPE_DIRS)))
        )

    def _sync_index(self, key: str) -> None:
        match = MEMORY_ENTRY_RE.match(key)
        if match is None:
            return
        try:
            front = MemoryStore.read_entry_file(
                MemoryStore.entry_path(self._user_id, match[1], match[2])
            )
            MemoryStore.sync_index_line(
                self._user_id,
                IndexEntry(
                    memory_type=match[1],
                    slug=match[2],
                    label=str(front.get("label") or match[2]),
                    description=str(front.get("description") or ""),
                ),
            )
        except Exception:  # noqa: BLE001
            # 索引同步失败不阻断已确认的写入；整理任务会重建索引兜底，
            # 但须留痕（对齐 kernel 快照落库的 best-effort 日志形态）
            logger.opt(exception=True).warning(
                "memory index sync failed after entry write user_id={} key={}",
                self._user_id, key,
            )

    def _after_write(self, key: str, result: Any) -> None:
        if key is not None and is_memory_entry(key) and _successful(result):
            self._sync_index(key)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        key = self._memory_key(request)
        if key is not None:
            self._gate(key)
        result = handler(request)
        self._after_write(key, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        key = self._memory_key(request)
        if key is not None:
            self._gate(key)
        result = await handler(request)
        self._after_write(key, result)
        return result


__all__ = ["MemoryWriteMiddleware", "MemoryWriteRejected", "WRITE_TOOL_NAMES"]
