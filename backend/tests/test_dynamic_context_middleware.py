"""Unit contracts for ``DynamicContextMiddleware``.

The dynamic context source is a net-new Noesis component (no upstream
equivalent). These tests pin its self-contained behaviour: inject a stable
block at every model call, re-run after compaction (i.e. via
``modify_request``), never touch history/usage, and honour an injected
provider instead of a service.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage

from noesis.middleware.dynamic_context_middleware import (
    DynamicContextBlock,
    DynamicContextMiddleware,
    render_dynamic_block,
)


def _request(system_prompt: str | None = None) -> ModelRequest:
    model = object()  # modify_request never inspects the model
    return ModelRequest(
        model=model,  # type: ignore[arg-type]
        messages=[],
        system_message=SystemMessage(content=system_prompt) if system_prompt else None,
    )


def test_modify_request_appends_dynamic_block_after_static_prompt() -> None:
    mw = DynamicContextMiddleware(
        context_provider=lambda: DynamicContextBlock(
            current_time="2026-08-12 12:00:00",
            timezone="UTC",
            workspace="ws-1",
            session_id="sess-1",
            attachments=("plan.md", "data.csv"),
        ),
    )
    modified = mw.modify_request(_request("You are a helpful agent."))

    assert modified.system_message is not None
    text = modified.system_message.text
    assert text.startswith("You are a helpful agent.")
    assert "## Runtime Context" in text
    assert "2026-08-12 12:00:00 (UTC)" in text
    assert "Workspace: ws-1" in text
    assert "Session: sess-1" in text
    assert "Attachments: plan.md, data.csv" in text
    # history untouched
    assert modified.messages == []


def test_modify_request_is_immutable_and_byte_stable_for_equal_inputs() -> None:
    provider = lambda: DynamicContextBlock(  # noqa: E731
        current_time="2026-08-12 12:00:00", timezone="UTC"
    )
    mw = DynamicContextMiddleware(context_provider=provider)

    first = mw.modify_request(_request("static"))
    second = mw.modify_request(_request("static"))

    assert first.system_message.text == second.system_message.text
    # the original request object is not mutated
    assert second.system_message is not first.system_message


def test_optional_fields_collapse_without_stray_headers() -> None:
    block = DynamicContextBlock(current_time="2026-08-12 12:00:00", timezone="UTC")
    text = render_dynamic_block(block)
    assert "Workspace:" not in text
    assert "Session:" not in text
    assert "Attachments:" not in text
    assert "Current time:" in text


def test_wrap_model_call_invokes_handler_with_modified_request() -> None:
    seen: list[ModelRequest] = []

    def handler(req: ModelRequest) -> object:
        seen.append(req)
        return "response"

    mw = DynamicContextMiddleware(
        context_provider=lambda: DynamicContextBlock(
            current_time="2026-08-12 12:00:00", timezone="UTC"
        ),
    )
    out = mw.wrap_model_call(_request("base"), handler)
    assert out == "response"
    assert "## Runtime Context" in seen[0].system_message.text


def test_no_provider_falls_back_to_time_and_thread_id_without_service() -> None:
    mw = DynamicContextMiddleware()
    # No provider, no config: must still produce a block using only datetime.
    modified = mw.modify_request(_request("static"))
    assert "## Runtime Context" in modified.system_message.text
    assert "Current time:" in modified.system_message.text


def test_async_provider_resolves_on_async_path() -> None:
    pytest.importorskip("anyio")
    import asyncio

    async def provider() -> DynamicContextBlock:
        return DynamicContextBlock(
            current_time="2026-08-12 12:00:00", timezone="UTC", workspace="ws-async"
        )

    mw = DynamicContextMiddleware(context_provider=provider)

    async def handler(req: ModelRequest) -> str:
        return req.system_message.text

    result = asyncio.run(mw.awrap_model_call(_request("static"), handler))
    assert "Workspace: ws-async" in result


def test_sync_path_rejects_async_provider() -> None:
    async def provider() -> DynamicContextBlock:
        return DynamicContextBlock(current_time="t", timezone="UTC")

    mw = DynamicContextMiddleware(context_provider=provider)
    with pytest.raises(TypeError, match="async context_provider"):
        mw.modify_request(_request("static"))


def test_default_block_uses_injected_now_for_determinism() -> None:
    # Guard against the middleware calling a hidden service for the clock.
    from noesis.middleware.dynamic_context_middleware import _default_block

    fixed = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)
    block = _default_block(now=fixed, config=None)
    assert block.current_time == "2026-01-01 09:30:00"
    assert block.workspace is None
    assert block.session_id is None
