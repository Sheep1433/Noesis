"""Per-agent-stream model attempt observation shared by callbacks and middleware."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler


@dataclass
class ModelAttemptTracker:
    attempt_id: int = 1
    visible_output_started: bool = False
    side_effect_boundary_crossed: bool = False

    @property
    def can_retry(self) -> bool:
        return not self.visible_output_started and not self.side_effect_boundary_crossed


_CURRENT_TRACKER: ContextVar[ModelAttemptTracker | None] = ContextVar(
    "noesis_model_attempt_tracker", default=None
)


def current_model_attempt_tracker() -> ModelAttemptTracker | None:
    return _CURRENT_TRACKER.get()


def bind_model_attempt_tracker(tracker: ModelAttemptTracker) -> Token:
    return _CURRENT_TRACKER.set(tracker)


def reset_model_attempt_tracker(token: Token) -> None:
    _CURRENT_TRACKER.reset(token)


class ModelAttemptCallback(BaseCallbackHandler):
    """Conservatively records any streamed model token and tool execution boundary."""

    def __init__(self, tracker: ModelAttemptTracker) -> None:
        self.tracker = tracker

    def on_llm_new_token(self, _token: str, **_kwargs: Any) -> None:
        self.tracker.visible_output_started = True

    def on_tool_start(self, *_args: Any, **_kwargs: Any) -> None:
        self.tracker.side_effect_boundary_crossed = True
