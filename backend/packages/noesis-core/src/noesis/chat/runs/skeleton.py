"""run / 消息骨架行的单一构造点。

主链路 `RunService.create`、子 Agent `SubagentSessionService.launch` 与
`create_followup_run` 共用：user 消息行、streaming assistant 骨架行、
queued run 行的字段与默认值（状态、last_sequence、snapshot 形状）在此
单点维护。只构造 ORM 行，不做 add/flush/commit——事务边界与 FK 插入
顺序由调用方持有。
"""

from __future__ import annotations

from typing import Any, Optional

from noesis.chat.message_builder import UserMessageBuilder
from noesis.storage.postgres.models.chat import TAgentRun, TChatMessage


def build_user_message_row(
    *,
    message_id: str,
    session_id: str,
    user_id: str,
    text: str,
    extra: dict[str, Any],
    message_sequence: int,
    created_at: int,
) -> TChatMessage:
    return TChatMessage(
        id=message_id,
        session_id=session_id,
        parent_id=None,
        user_id=user_id,
        role="user",
        content=UserMessageBuilder(text.strip()).to_dict(),
        extra=extra,
        status="completed",
        message_sequence=message_sequence,
        created_at=created_at,
    )


def build_assistant_skeleton_row(
    *,
    message_id: str,
    session_id: str,
    user_id: str,
    parent_id: Optional[str],
    extra: dict[str, Any],
    message_sequence: int,
    created_at: int,
) -> TChatMessage:
    return TChatMessage(
        id=message_id,
        session_id=session_id,
        parent_id=parent_id,
        user_id=user_id,
        role="assistant",
        content={"parts": []},
        extra=extra,
        status="streaming",
        message_sequence=message_sequence,
        created_at=created_at,
    )


def build_queued_run_row(
    *,
    run_id: str,
    user_id: str,
    session_id: str,
    assistant_message_id: str,
    client_request_id: str,
    request_digest: str,
    qa_type: str,
    origin: str,
    created_at: int,
    launch_payload: Optional[dict[str, Any]] = None,
) -> TAgentRun:
    return TAgentRun(
        id=run_id,
        user_id=user_id,
        session_id=session_id,
        assistant_message_id=assistant_message_id,
        client_request_id=client_request_id,
        request_digest=request_digest,
        qa_type=qa_type,
        origin=origin,
        status="queued",
        last_sequence=0,
        attempt_id=1,
        owner_instance_id=None,
        owner_term=0,
        launch_payload=launch_payload,
        snapshot={"parts": []},
        created_at=created_at,
        updated_at=created_at,
    )
