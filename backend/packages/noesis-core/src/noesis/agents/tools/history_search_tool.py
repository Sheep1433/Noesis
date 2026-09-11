"""Agent 会话历史检索工具：原文层召回（session-history-search）。

与 ``memory_tools``（蒸馏层）成对：本层对 ``t_chat_message`` 原文做 pg_trgm
确定性全文检索，结果即原文片段，零 LLM 参与。身份闭包绑定（构造期固定
user_id / 当前 session_id，运行期模型不可传用户标识）——对齐 memory_tools
的绑定形态。跨会话发现后可把返回的 session_id 交给 ``search_history``
定点跟进；归属不符时返回统一错误，不泄露会话存在性。
"""

from __future__ import annotations

import json
from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from noesis.runtime.logging import logger
from noesis.services.history_search import (
    SessionAccessDenied,
    search_session_history,
    search_user_sessions,
)
from noesis.storage.postgres.manager import pg_manager

_SOURCE_FIRST = (
    "来源优先：本工具只检索对话历史，仅证明「曾经说过」，不构成外部事实的证据；"
    "用户给出 URL、文件路径、账号等直接来源时先查原来源。"
)

# 工具描述为模块级常量：压缩评测 recovery 臂复用同一份文本与 schema，
# 保证「工具 schema 与产品一致」的口径（evals/compression/recovery.py）
SEARCH_HISTORY_DESCRIPTION = (
    "在指定会话（默认当前会话）的消息原文中做确定性全文检索。"
    "按需使用：会话摘要与近期对话是首先依赖的信息来源，"
    "仅当所需细节（原文措辞、错误串、数值、路径、行号）未被摘要保留时才检索；"
    "上下文已有足够信息时直接作答，不为已有答案多做检索。"
    "结果为原文片段：含消息序号/角色/时间、按序号升序；"
    "单条超长会截断，可用 around_sequence 按序号滚动读取前后原文。"
    f"{_SOURCE_FIRST}"
    "与 search_memory 分工：找回说过的原话/原始细节用本工具；"
    "用户偏好与既往结论用 search_memory（蒸馏记忆层）。"
)

SEARCH_SESSIONS_DESCRIPTION = (
    "按关键词在当前用户的历史会话中做跨会话发现（「我们之前讨论过什么」），"
    "按会话分组返回：标题、时间、最强匹配片段、会话类型（root/subagent）与父会话；"
    "返回的 session_id 可交给 search_history 定点检索该会话。当前会话不在结果中。"
    f"{_SOURCE_FIRST}"
)


class SearchHistoryInput(BaseModel):
    query: str = Field(
        default="",
        description="检索关键词，建议具体词（路径、错误码、函数名）；滚动形态（around_sequence）下忽略",
    )
    session_id: str = Field(default="", description="目标会话 ID；空 = 当前会话")
    before_compaction: bool = Field(
        default=False,
        description="只搜最近一次压缩边界之前的「被压缩遮蔽区」；从未压缩时退化为全历史（标注边界未知）",
    )
    limit: int = Field(default=5, ge=1, le=10, description="返回命中条数上限")
    around_sequence: Optional[int] = Field(
        default=None,
        description="滚动深读：返回该消息序号前后各 window 条原文（用于查看被截断命中的完整上下文）",
    )
    window: int = Field(default=5, ge=1, le=10, description="滚动形态窗口大小（服务端有上限）")


class SearchSessionsInput(BaseModel):
    query: str = Field(default="", description="检索关键词，建议具体词（路径、错误码、函数名）")
    limit: int = Field(default=5, ge=1, le=10, description="返回会话分组数上限")


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def build_history_search_tools(
    *, user_id: str, session_id: str
) -> list[StructuredTool]:
    """绑定当前用户与当前会话的两个历史检索工具（SuperAgent 原文层召回）。

    ``session_id`` 是闭包绑定的当前会话；注意内层 ``search_history`` 的
    参数也叫 session_id（模型可传的目标会话），闭包值经 ``current_session_id``
    别名避免被参数遮蔽。
    """
    current_session_id = session_id

    async def search_history(
        query: str = "",
        session_id: str = "",
        before_compaction: bool = False,
        limit: int = 5,
        around_sequence: Optional[int] = None,
        window: int = 5,
    ) -> str:
        target = (session_id or "").strip() or current_session_id
        try:
            async with pg_manager.get_async_session_context() as db:
                result = await search_session_history(
                    db,
                    user_id=user_id,
                    session_id=target,
                    query=query,
                    before_compaction=before_compaction,
                    limit=limit,
                    around_sequence=around_sequence,
                    window=window,
                )
        except SessionAccessDenied:
            return _error("会话不存在或无权限访问")
        except ValueError as exc:
            return _error(str(exc))
        except Exception:
            logger.exception("search_history failed session_id={}", target)
            return _error("历史检索暂不可用")
        return json.dumps(
            {
                "session_id": target,
                "mode": result.mode,
                "boundary_unknown": result.boundary_unknown,
                "results": [
                    {
                        "sequence": hit.sequence,
                        "role": hit.role,
                        "created_at": hit.created_at,
                        "text": hit.text,
                        "truncated": hit.truncated,
                    }
                    for hit in result.hits
                ],
            },
            ensure_ascii=False,
        )

    async def search_sessions(query: str = "", limit: int = 5) -> str:

        try:
            async with pg_manager.get_async_session_context() as db:
                groups = await search_user_sessions(
                    db,
                    user_id=user_id,
                    exclude_session_id=current_session_id,
                    query=query,
                    limit=limit,
                )
        except ValueError as exc:
            return _error(str(exc))
        except Exception:
            logger.exception("search_sessions failed user_id={}", user_id)
            return _error("会话检索暂不可用")
        return json.dumps(
            {
                "results": [
                    {
                        "session_id": group.session_id,
                        "title": group.title,
                        "kind": group.kind,
                        "parent_id": group.parent_id,
                        "created_at": group.created_at,
                        "updated_at": group.updated_at,
                        "matched": {
                            "sequence": group.matched_sequence,
                            "role": group.matched_role,
                            "fragment": group.fragment,
                            "truncated": group.truncated,
                        },
                    }
                    for group in groups
                ]
            },
            ensure_ascii=False,
        )

    history_tool = StructuredTool.from_function(
        coroutine=search_history,
        name="search_history",
        description=SEARCH_HISTORY_DESCRIPTION,
        args_schema=SearchHistoryInput,
    )
    sessions_tool = StructuredTool.from_function(
        coroutine=search_sessions,
        name="search_sessions",
        description=SEARCH_SESSIONS_DESCRIPTION,
        args_schema=SearchSessionsInput,
    )
    return [history_tool, sessions_tool]


__all__ = [
    "SEARCH_HISTORY_DESCRIPTION",
    "SEARCH_SESSIONS_DESCRIPTION",
    "SearchHistoryInput",
    "SearchSessionsInput",
    "build_history_search_tools",
]
