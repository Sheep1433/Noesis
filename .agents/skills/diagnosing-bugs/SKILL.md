---
name: diagnosing-bugs
description: Diagnosis loop for hard bugs and performance regressions. Use when the user says "diagnose"/"debug this", or reports something broken/throwing/failing/slow.
---

# Diagnosing Bugs

A discipline for hard bugs. The phases are a default investigation path, not the
boundary of the problem. New evidence that does not fit the current model must
reopen the investigation instead of being forced into the nearest hypothesis.

Job boundary: this skill is the generic diagnosis loop. For Noesis agent-run
forensics specifically (tracing a run through Postgres, backend logs, and
Langfuse), `noesis-run-trace-analysis` is the specialized entry point — use it
first when the subject is a Noesis run, then return here for the causal loop.

## Sources of truth (read, don't re-summarize)

- [docs/engineering/](../../../docs/engineering/) — the relevant module's current architecture, before forming hypotheses about paths and boundaries.
- [docs/debugging/](../../../docs/debugging/) — root causes and diagnosis methods that paid off before; check it before re-deriving a known diagnosis.
- [AGENTS.md](../../../AGENTS.md) — verification commands and the rule that a root cause comes before any fallback or compatibility patch.

## Phase 0 — Model the problem before narrowing it

Record a compact problem model before declaring a cause:

- the user's observed symptom and expected behavior;
- the end-to-end path that could produce it (UI, protocol, service, state,
  dependency, persistence, and environment boundaries as applicable);
- concrete artifacts already available: error, trace, log, request, screenshot,
  timing, recent change;
- adjacent signals that may be part of the same failure;
- facts, inferences, and unverified assumptions kept separate.

The exact symptom anchors the investigation, but does not limit its scope. Check
whether the same broken contract can affect sibling events, callers, states, or
recovery paths. Do not broaden into unrelated cleanup.

## Phase 1 — Build a feedback loop

Build two signals when the system warrants it:

1. **Symptom loop** — observes the user's real failure at the highest practical
   seam. It may be slower or artifact-backed.
2. **Diagnostic loop** — a minimized, fast signal used to distinguish causes and
   develop the fix.

A fast unit test must not silently replace an end-to-end symptom that depends on
protocol, middleware order, persistence, retries, or multiple callers.

Spend disproportionate effort here. **Be aggressive. Be creative. Refuse to give up.**

### Ways to construct one — try them in roughly this order

1. **Failing test** at whatever seam reaches the bug — unit, integration, e2e.
2. **Curl / HTTP script** against a running dev server.
3. **CLI invocation** with a fixture input, diffing stdout against a known-good snapshot.
4. **Headless browser script** (Playwright / Puppeteer) — drives the UI, asserts on DOM/console/network.
5. **Replay a captured trace.** Save a real network request / payload / event log to disk; replay it through the code path in isolation.
6. **Throwaway harness.** Spin up a minimal subset of the system (one service, mocked deps) that exercises the bug code path with a single function call.
7. **Property / fuzz loop.** If the bug is "sometimes wrong output", run 1000 random inputs and look for the failure mode.
8. **Bisection harness.** If the bug appeared between two known states (commit, dataset, version), automate "boot at state X, check, repeat" so you can `git bisect run` it.
9. **Differential loop.** Run the same input through old-version vs new-version (or two configs) and diff outputs.
10. **HITL bash script.** Last resort. If a human must click, drive _them_ with `scripts/hitl-loop.template.sh` (shipped alongside this skill) so the loop is still structured. Captured output feeds back to you.

Build the right feedback loop, and the bug is 90% fixed.

### Tighten the loop

Treat the loop as a product. Once you have _a_ loop, **tighten** it:

- Can I make it faster? (Cache setup, skip unrelated init, narrow the test scope.)
- Can I make the signal sharper? (Assert on the specific symptom, not "didn't crash".)
- Can I make it more deterministic? (Pin time, seed RNG, isolate filesystem, freeze network.)

A 30-second flaky loop is barely better than no loop; a 2-second deterministic one is tight — a debugging superpower.

### Non-deterministic bugs

The goal is not a clean repro but a **higher reproduction rate**. Loop the trigger 100×, parallelise, add stress, narrow timing windows, inject sleeps. A 50%-flake bug is debuggable; 1% is not — keep raising the rate until it's debuggable.

### When you genuinely cannot build a runnable loop

Use a captured artifact as an evidence loop when it preserves the actual failure:
HAR, trace, event stream, log dump, core dump, or timestamped recording. You may
form provisional hypotheses from it, but you do not call a cause confirmed or apply
a fix unless the evidence distinguishes it from plausible alternatives. If it does
not, list what was tried and ask for access, a stronger artifact, or permission to
add temporary instrumentation.

### Completion criterion — a tight loop that goes red

Before implementing a fix, name at least one command or replay procedure that you
have already run and whose output is preserved. State whether it is the symptom
loop, diagnostic loop, or both. It should be:

- [ ] **Red-capable** — it drives the actual bug code path and asserts the **user's exact symptom**, so it can go red on this bug and green once fixed. Not "runs without erroring" — it must be able to _catch this specific bug_.
- [ ] **Deterministic** — same verdict every run (flaky bugs: a pinned, high reproduction rate, per above).
- [ ] **Fast enough for its role** — the diagnostic loop should usually take seconds; the symptom loop may be slower when the real boundary requires it.
- [ ] **Agent-runnable** — you can run it unattended; a human in the loop only via the shipped `hitl-loop.template.sh`.

Reading code to understand the path is allowed in Phase 0. Editing code or
announcing a root cause before a red-capable signal or discriminating artifact is
not. If this skill is loaded after a theory already exists, relabel that theory
`unverified` and return to the problem model.

## Phase 2 — Reproduce + minimise

Run the loop. Watch it go red — the bug appears.

Confirm:

- [ ] The loop produces the failure mode the **user** described — not a different failure that happens to be nearby. Wrong bug = wrong fix.
- [ ] The failure is reproducible across multiple runs (or, for non-deterministic bugs, reproducible at a high enough rate to debug against).
- [ ] You have captured the exact symptom (error message, wrong output, slow timing) so later phases can verify the fix actually addresses it.

### Minimise

Once it's red, shrink the diagnostic repro to the **smallest scenario that still
goes red**. Preserve the original symptom loop separately. Cut inputs, callers,
config, data, and steps **one at a time**, re-running the loop after each cut —
keep only what is load-bearing for the failure.

Why bother: a minimal repro shrinks the hypothesis space in Phase 3 (fewer moving parts left to suspect) and becomes the clean regression test in Phase 5.

Done when **every remaining element is load-bearing** — removing any one of them makes the loop go green.

Before a fix, do not proceed until the failure is reproduced and the diagnostic
case is minimized, or a discriminating real-world artifact makes the same causal
distinction. For a diagnosis-only request, you may report an evidence-backed
probable cause without local reproduction, but must state confidence and the
remaining alternative explanations.

## Phase 3 — Hypothesise

Generate enough ranked hypotheses to cover the plausible failing boundaries in the
Phase 0 path. Usually this means at least two; do not invent alternatives when
one cause is already mechanically proven by discriminating evidence.

Each hypothesis must be **falsifiable**: state the prediction it makes.

> Format: "If <X> is the cause, then <changing Y> will make the bug disappear / <changing Z> will make it worse."

If you cannot state the prediction, the hypothesis is a vibe — discard or sharpen it.

Share the ranked list when the investigation is long-running, needs a product or
environment choice, or user knowledge can materially re-rank it. Do not turn a
small diagnosis into a ceremonial checkpoint.

## Phase 4 — Instrument

Each probe must map to a specific prediction from Phase 3. **Change one variable at a time.**

After each material result, ask whether it supports the current model, contradicts
it, or exposes a new boundary. Contradictory evidence reopens Phases 0–3. Keep
separate labels for:

- root cause;
- trigger;
- amplifier;
- missing guard or observability that allowed the symptom to escape.

Tool preference:

1. **Debugger / REPL inspection** if the env supports it. One breakpoint beats ten logs.
2. **Targeted logs** at the boundaries that distinguish hypotheses.
3. Never "log everything and grep".

**Tag every debug log** with a unique prefix, e.g. `[DEBUG-a4f2]`. Cleanup at the end becomes a single grep. Untagged logs survive; tagged logs die.

**Perf branch.** For performance regressions, logs are usually wrong. Instead: establish a baseline measurement (timing harness, `performance.now()`, profiler, query plan), then bisect. Measure first, fix second.

## Phase 5 — Fix + regression test

Write the regression test **before the fix** — but only if there is a **correct seam** for it.

A correct seam is one where the test exercises the **real bug pattern** as it occurs at the call site. If the only available seam is too shallow (single-caller test when the bug needs multiple callers, unit test that can't replicate the chain that triggered the bug), a regression test there gives false confidence.

**If no correct seam exists, that itself is the finding.** Note it. The codebase architecture is preventing the bug from being locked down. Flag this for the next phase.

If a correct seam exists:

1. Turn the minimised repro into a failing test at that seam.
2. Watch it fail.
3. Apply the fix.
4. Watch it pass.
5. Re-run the minimized diagnostic loop.
6. Re-run the original symptom loop; both must agree.

## Phase 6 — Cleanup + post-mortem

Required before declaring done:

- [ ] Original repro no longer reproduces (re-run the Phase 1 loop)
- [ ] Regression test passes (or absence of seam is documented)
- [ ] All `[DEBUG-...]` instrumentation removed (`grep` the prefix)
- [ ] Throwaway prototypes deleted (or moved to a clearly-marked debug location)
- [ ] The hypothesis that turned out correct is stated in the commit / PR message — so the next debugger learns
- [ ] If the root cause or diagnosis method is reusable, record it in `docs/debugging/` (one home per topic; link it from the bug record if one exists)

**Then ask: what would have prevented this bug?** If the answer involves an
architectural change (no good test seam, tangled callers, hidden coupling), record
the concrete follow-up in the repository's debugging or architecture workflow.
Recommend it after the fix, when the evidence is strongest; do not silently expand
the current bug fix into a refactor.
