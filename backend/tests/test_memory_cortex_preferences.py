from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from noesis.services.memory.preferences import MemoryCortexPreferenceService


def test_preference_response_has_single_user_switch() -> None:
    result = MemoryCortexPreferenceService._response(enabled=True)
    assert result.model_dump() == {"enabled": True}


@pytest.mark.asyncio
async def test_missing_preference_defaults_to_disabled(monkeypatch) -> None:
    get_preference = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "noesis.services.memory.preferences.MemoryPreferenceRepository.get",
        get_preference,
    )
    result = await MemoryCortexPreferenceService.get(
        SimpleNamespace(), user_id="u1"  # type: ignore[arg-type]
    )
    assert result.enabled is False
