"""Insert dynamic context after stable history and before the current user input."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage


def insert_late_context(messages: list, *, text: str, marker: str) -> list:
    if not text or any(
        isinstance(message, SystemMessage)
        and message.additional_kwargs.get("noesis_late_context") == marker
        for message in messages
    ):
        return messages
    index = next(
        (
            position
            for position in range(len(messages) - 1, -1, -1)
            if isinstance(messages[position], HumanMessage)
        ),
        len(messages),
    )
    block = SystemMessage(
        content=text,
        additional_kwargs={"noesis_late_context": marker},
    )
    return [*messages[:index], block, *messages[index:]]


__all__ = ["insert_late_context"]
