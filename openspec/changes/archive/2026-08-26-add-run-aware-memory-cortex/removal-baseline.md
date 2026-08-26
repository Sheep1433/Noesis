# Removal baseline

## Deleted implementation

- Automatic daily-memory generation, scheduler, backfill, source lookup, file search and Agent tools.
- Runtime `memory/YYYY-MM-DD.md` files and the session-context tree path that exposed them.
- Experience-only recovery adapter, failure identity/resolution, extractor, revision, retriever, action-card middleware, extraction worker and index worker.
- Experience-only item/evidence/job/outbox schema, API, UI, evals and tests.
- Completed-only/failure-only terminal hooks and the old `memory_cortex` deployment configuration.

No compatibility flag, data migration or legacy read path is retained because the feature was not released.

## Preserved neutral foundations

- Immutable Agent Run snapshots and terminal compare-and-set persistence.
- Private tool provider metadata and public-response redaction.
- User/scope authorization boundaries already present in chat and settings services.
- One PostgreSQL user preference (`enabled`), exposed through one user-controlled API and UI switch.
- Explicit `USER.md` and `AGENTS.md` editing and context preview.

## Removed runtime data manifest

- Scope: `.noesis/users/1/memory/`
- Files: 17 daily Markdown files, dated 2026-08-07 through 2026-08-23.
- SHA-256 values were captured before deletion for removal auditing; the contents are intentionally not migrated.

## Verification

- Removal baseline and affected backend suites: 62 passed.
- Frontend ESLint: passed.
- `server.main` import: passed.
- Uvicorn process creation: passed on a separate port; lifespan correctly rejected a second active backend because the existing user process held the PostgreSQL advisory lock. The existing process was not stopped.
