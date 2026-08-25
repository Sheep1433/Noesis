"""Pipeline seam used by contract tests before the implementation exists."""

from __future__ import annotations

from typing import Protocol

from evals.memory_cortex.schema import FixtureObservation, RunMemoryFixture


class MemoryEvalPipeline(Protocol):
    def observe(self, fixture: RunMemoryFixture) -> FixtureObservation: ...


class StructuralMemoryPipeline:
    """Deterministic capture contract used by default fixture tests."""

    @staticmethod
    def observe(fixture: RunMemoryFixture) -> FixtureObservation:
        run = fixture.run
        stable_kinds = {
            "user_correction",
            "assistant_conclusion",
            "tool_outcome",
            "artifact",
            "validation",
            "compaction",
        }
        captured = (
            run.memory_enabled
            and run.root_run
            and not run.internal_memory_run
            and run.status in {"completed", "partial", "error", "interrupted"}
            and any(span.kind in stable_kinds for span in run.evidence)
        )
        return FixtureObservation(fixture_id=fixture.id, captured=captured)


def get_pipeline() -> MemoryEvalPipeline:
    return StructuralMemoryPipeline()


__all__ = ["MemoryEvalPipeline", "StructuralMemoryPipeline", "get_pipeline"]
