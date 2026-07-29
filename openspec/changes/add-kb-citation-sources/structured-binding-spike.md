# Structured binding provider spike

## 2026-07-28 deployment baseline

- Active endpoint: `opencode / deepseek-v4-flash-free`, streaming enabled.
- Method: LangChain `with_structured_output(CitedAnswer, method="function_calling")`.
- Fixed cases: Chinese single evidence, English multiple evidence, uncited answer, long Chinese answer.
- Result: the first schema request returned HTTP 400 (`Upstream request failed`). The endpoint therefore failed the release gate before semantic cases or structured streaming could be evaluated.
- Qwen: the current deployment has no Qwen endpoint/API credential configured, so it was not claimed as verified.

## Decision

COMMON_QA structured citation binding remains disabled for the active provider. The runtime keeps ordinary streamed text and persists retrieved evidence, but creates no inferred citations. There is no marker or Top-K fallback. Annotation timing is terminal-only for providers that later pass this gate, because no current endpoint has demonstrated safe completed-segment structured streaming.

The reusable gate is `backend/evals/kb/citation_binding_spike.py`. A provider may be enabled only after every fixed case parses and the streaming probe yields usable typed segments rather than partial JSON text.

Runtime telemetry records structured binding success/schema error, retrieval-only fallback, each binding rejection reason, and resolve success/forbidden/stale/missing. The current spike has zero successful structured bindings and one endpoint-level schema request failure, so a semantic verifier would not improve the active path; provider compatibility remains the blocking gate.

## Capacity baseline

`t_chat_message.content` is PostgreSQL JSON rather than a fixed-width text field. The tighter existing transport boundary is the 16 MiB run output limit. Citation snapshots therefore use a 2 MiB assistant JSON budget, with deterministic inner limits of 20 results per tool call, 100 unique evidence items per run, 2,000 Unicode characters / 8 KiB per excerpt, and 2 KiB per locator. Search tool output is compacted in the persisted tool part after its typed retrieval part has been registered, so the same excerpts are not stored twice. Limits live under `citation_limits` and default to disabled-feature-safe values.
