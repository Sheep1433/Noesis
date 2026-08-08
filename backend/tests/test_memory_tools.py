import json

import pytest

from noesis.runtime.deps import temporary_memory_service
from noesis.agents.tools.memory_tools import build_memory_tools


class FakeMemoryService:
    @staticmethod
    def search_entries(user_id, query, **kwargs):
        assert user_id == "bound-user"
        assert query == "LangGraph"
        return [{"id": "m1", "summary": "采用 LangGraph"}]

    @staticmethod
    async def get_source(db, **kwargs):
        assert kwargs["user_id"] == "bound-user"
        return {"messages": [{"message_id": kwargs["message_id"], "text": "source"}]}


@pytest.mark.asyncio
async def test_memory_tools_bind_user_and_return_compact_json() -> None:
    with temporary_memory_service(FakeMemoryService):
        search, source = build_memory_tools(user_id="bound-user", db=object())
        search_result = json.loads(await search.ainvoke({"query": "LangGraph"}))
        source_result = json.loads(await source.ainvoke({"session_id": "s1", "message_id": "m1"}))

    assert search_result["items"][0]["id"] == "m1"
    assert source_result["messages"][0]["text"] == "source"
