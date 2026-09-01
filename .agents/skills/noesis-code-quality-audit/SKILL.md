---
name: noesis-code-quality-audit
description: Audit the Noesis repository for AI-generated code smells, over-splitting into shallow helpers, oversized stateful functions, duplicated logic, dead code, broad exception swallowing, weak type boundaries, and architectural boundary violations. Use when reviewing the whole Noesis codebase, checking whether AI-written code has accumulated maintenance debt, preparing a refactor plan, or deciding which quality scanners and manual reviews to run.
---

# Noesis Code Quality Audit

Audit first, refactor second. Produce an evidence-backed report and do not modify code unless the user explicitly asks for fixes.

## 1. Establish scope and preserve the worktree

Read these files before reviewing:

- `AGENTS.md`
- `frontend/AGENTS.md` when reviewing frontend code
- `backend/AGENTS.md` when reviewing backend code
- relevant `docs/architecture/` and `docs/engineering/` documents
- `CONTEXT.md` if present

Run:

```bash
git status --short
git diff --stat
```

Treat existing uncommitted changes as user-owned. Do not reset, checkout, format, or auto-fix the repository. State whether findings are from the current worktree, a diff, or the committed baseline.

Exclude generated and vendored code from the primary report unless it is the subject of the review:

- `.git/`, `.noesis/`, `node_modules/`, `frontend/dist/`, caches
- `backend/packages/noesis-core/src/noesis/knowledge/_ragflow_compat/`
- archived OpenSpec changes and evaluation fixtures

## 2. Build the deterministic baseline

Run the bundled metrics script from the repository root:

```bash
uv run python .agents/skills/noesis-code-quality-audit/scripts/collect_metrics.py --root .
```

Run available project-native checks:

```bash
cd backend && ruff check packages/noesis-core/src server
cd ../frontend && pnpm lint && pnpm build
```

For targeted complexity warnings, use temporary CLI rules rather than changing project config:

```bash
pnpm exec eslint \
  src/views/chat.vue \
  src/views/chat/messageParts.ts \
  src/views/chat/useSSEStream.ts \
  --rule 'complexity:["warn",15]' \
  --rule 'max-lines-per-function:["warn",100]' \
  --rule 'max-depth:["warn",4]' \
  --rule 'no-nested-ternary:warn'
```

Use optional scanners when available. They are evidence sources, not automatic truth:

```bash
pnpm dlx jscpd backend/packages/noesis-core/src backend/server frontend/src \
  --min-lines 8 --min-tokens 60 --reporters console \
  --ignore '**/node_modules/**,**/dist/**,**/_ragflow_compat/**'

cd backend && uvx vulture packages/noesis-core/src/noesis server --min-confidence 80
cd ../frontend && pnpm dlx knip --reporter compact
```

Record tool availability, command exit status, and known baseline failures. Never hide existing failures by changing thresholds during an audit.

## 3. Review AI-specific smells

Inspect findings manually. Report a smell only when the code shows a maintenance or correctness cost.

### Shallow helper proliferation

Flag a helper when it only forwards arguments or returns one property and has no domain name, invariant, validation, ownership, or test seam. Do not flag:

- abstract interface methods;
- typed domain predicates;
- path accessors that centralize a filesystem contract;
- adapters required by an external framework;
- lifecycle hooks whose name documents an event boundary.

Prefer one meaningful function over a chain such as `get_x()` → `resolve_x()` → `load_x()` → `service.get_x()` when all layers add no behavior.

### Oversized stateful functions

Prioritize functions that combine three or more responsibilities, especially:

- SSE parsing and event dispatch;
- Run state transitions and persistence;
- message part normalization;
- chat send orchestration;
- tool failure classification;
- document parsing and storage.

Use 50 lines or complexity 10 as investigation signals, not automatic defects. A function is a confirmed smell when a reader must track unrelated state or when one change requires editing several unrelated branches.

### Duplicate logic

Look for repeated:

- snapshot reconstruction;
- status/terminal-state checks;
- error-to-user-message mapping;
- session ownership queries;
- frontend Reasoning/Tool/Subagent layout styles;
- retry and reconnect loops.

Unify only when the duplicated code has the same invariant. Do not merge code merely because it looks similar but belongs to different domains.

### Compatibility and dead-code residue

Search for `legacy`, `compat`, `temporary`, `removed`, `TODO`, `need to delete`, unreachable blocks, no-op adapters, and old field fallbacks. For each result, determine whether it is:

1. an active migration boundary with a removal condition;
2. a required external compatibility layer;
3. dead code that should be deleted.

An unreachable block or a compatibility function that always returns `None` is a high-confidence finding unless an external caller is demonstrated.

### Error swallowing

Search for `except Exception`, `except BaseException`, and `except ...: pass`. Flag catches that:

- hide a failed request or state transition;
- convert an error into success;
- make the UI show a normal state after a failed stream;
- omit structured logging or a terminal status;
- catch cancellation together with ordinary exceptions without preserving cancellation semantics.

Narrow catches around cleanup are usually acceptable. Do not report every broad catch without tracing its caller and user-visible effect.

### Weak type boundaries

Inspect `Any`, `Dict[str, Any]`, `Record<string, unknown>`, `as unknown as`, and unvalidated JSON. Prioritize event payloads, Run snapshots, tool inputs, provider usage, and persistence fields. Prefer a discriminated event union or a typed boundary parser over repeated casts at every consumer.

## 4. Review Noesis architecture boundaries

Check these invariants from `AGENTS.md`:

- `server` depends on `noesis`; `noesis` never imports `server`.
- API routes do not contain business service or ORM logic.
- `noesis.chat` and `noesis.auth` do not import `noesis.services` or `noesis.agents`.
- stream delivery does not own persistence; Run/Service owns lifecycle and terminal state.
- frontend reduces SSE into memory and does not become the persistence authority.

Use `rg` to verify imports, then inspect the call path. A dependency injection provider is justified only when it preserves the boundary and has a real runtime owner; do not add providers just to bypass an import cycle.

## 5. Rank findings by confidence and impact

Use three labels:

- **Confirmed defect**: reproducible failure, static error, unreachable code, or violated project boundary.
- **High-confidence smell**: concrete complexity, duplication, or hidden state that makes a foreseeable change risky.
- **Candidate**: scanner result requiring caller/history review; do not recommend deletion yet.

For every confirmed or high-confidence finding, report:

1. path and line;
2. evidence from the scanner or call path;
3. why it increases defect or maintenance risk;
4. the smallest structural remedy;
5. a regression test or verification command.

Group results as P0 correctness, P1 architectural risk, P2 maintainability, and P3 cleanup. Keep scanner noise in a separate appendix.

## 6. Refactor policy

Do not perform a whole-repository auto-refactor. Work in this order:

1. fix confirmed static/runtime defects;
2. add a regression test at the real seam;
3. simplify one stateful module;
4. remove duplicate or dead code after callers are checked;
5. tighten types and exception boundaries;
6. rerun the same scanners and targeted tests.

Preserve behavior, side-effect order, error semantics, and public API shape. Separate behavior changes from simplification commits. If no suitable test seam exists, report that as an architectural finding instead of writing a shallow unit test.

## 7. Final report format

Return:

```text
审查范围与工作区状态
扫描结果与基线失败
P0/P1/P2/P3 findings
被判定为误报或暂不处理的候选项
建议的分阶段整改顺序
验证命令
```

Do not claim the project is clean because lint passes. Lint, complexity, duplication, dead-code, architecture, and runtime behavior are separate evidence layers.
