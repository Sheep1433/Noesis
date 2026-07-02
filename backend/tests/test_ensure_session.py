"""PUT /api/chat/sessions/{id}/ensure 单测。"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.chat_api import ensure_session
from schemas.chat_vo import EnsureSessionRequest


@pytest.mark.asyncio
async def test_ensure_session_creates_or_gets():
    mock_session = MagicMock()
    mock_session.id = "sess-1"
    mock_session.parent_id = None
    mock_session.user_id = "user-1"
    mock_session.title = "新对话"
    mock_session.extra = {"qa_type": "COMMON_QA"}
    mock_session.created_at = 1
    mock_session.updated_at = 1
    mock_session.deleted_at = None

    mock_user = MagicMock()
    mock_user.user_id = "user-1"
    db = AsyncMock()

    with (
        patch(
            "api.chat_api.ChatService.is_session_owned_by_other",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "api.chat_api.ChatService.get_or_create_session",
            new_callable=AsyncMock,
            return_value=mock_session,
        ) as mock_goc,
        patch(
            "api.chat_api.ChatService.merge_session_extra",
            new_callable=AsyncMock,
        ) as mock_merge,
        patch(
            "api.chat_api.ChatService.get_session_by_id",
            new_callable=AsyncMock,
            return_value=mock_session,
        ),
    ):
        resp = await ensure_session(
            session_id="sess-1",
            request=EnsureSessionRequest(extra={"qa_type": "COMMON_QA"}),
            current_user=mock_user,
            db=db,
        )

        mock_goc.assert_awaited_once_with(
            user_id="user-1",
            session_id="sess-1",
            title=None,
            extra={"qa_type": "COMMON_QA"},
            db=db,
        )
        mock_merge.assert_awaited_once_with(
            "sess-1",
            "user-1",
            {"qa_type": "COMMON_QA"},
            db=db,
        )
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["code"] == 200
        assert body["data"]["id"] == "sess-1"
