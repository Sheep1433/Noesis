---
name: code-review
description: Review the changes since a fixed point (commit, branch, tag, or merge-base) along two axes — Standards (does the code follow this repo's documented coding standards?) and Spec (does the code match what the originating issue/PRD asked for?). Runs both reviews in parallel sub-agents and reports them side by side. Use when the user wants to review a branch, a PR, work-in-progress changes, or asks to "review since X".
---

# Code Review (Noesis)

Two-axis review of the diff between `HEAD` and a fixed point the user supplies:

- **Standards** — does the code conform to this repo's documented coding standards?
- **Spec** — does the code faithfully implement the originating issue / PRD / spec?

Both axes run as **parallel sub-agents** so they don't pollute each other's context, then this skill aggregates their findings.

Job boundary: this skill gates **one diff**. It is not `code-simplification` (which executes a simplification pass on working code) and not `noesis-code-quality-audit` (which surveys accumulated debt across the whole repository). Do not turn a review into a refactor.

## Sources of truth (read, don't re-summarize)

- [AGENTS.md](../../../AGENTS.md) — collaboration rules, conventions, and the **high-concern list** (SSE 持久化、Qdrant 异常、配置硬编码、JWT/DB 密钥、MCP 远程执行). Findings in these areas outrank style findings.
- [frontend/AGENTS.md](../../../frontend/AGENTS.md) / [backend/AGENTS.md](../../../backend/AGENTS.md) — module standards.
- `openspec/specs/**` and the originating change under `openspec/changes/**` — the Spec axis' authority.
- [docs/engineering/](../../../docs/engineering/) — current long-term architecture; a diff that contradicts it without a spec change is a Spec finding.

## Process

### 1. Pin the fixed point

Whatever the user said is the fixed point — a commit SHA, branch name, tag, `main`, `HEAD~5`, etc. If they didn't specify one, ask for it.

Capture the diff command once: `git diff <fixed-point>...HEAD` (three-dot, so the comparison is against the merge-base). Also note the list of commits via `git log <fixed-point>..HEAD --oneline`.

Before going further, confirm the fixed point resolves (`git rev-parse <fixed-point>`) and the diff is non-empty. A bad ref or empty diff should fail here — not inside two parallel sub-agents.

### 2. Identify the spec source

Look for the originating spec, in this order:

1. An OpenSpec change: derive the change name from the branch name or commit messages, then read `openspec/changes/<name>/` (proposal.md, design.md, specs/). No active change matching? Check `openspec/changes/archive/` for the most recent landed one covering the diff's subject.
2. A path the user passed as an argument.
3. A PRD/spec file under `docs/` matching the branch name or feature.
4. If nothing is found, ask the user where the spec is. If they say there isn't one, the **Spec** sub-agent will skip and report "no spec available".

### 3. Identify the standards sources

The repo's AGENTS.md files (root + the module the diff touches) plus anything else that documents how code should be written.

On top of whatever the repo documents, the Standards axis always carries the **smell baseline** below — a fixed set of Fowler code smells (_Refactoring_, ch.3) that applies even when a repo documents nothing. Two rules bind it:

- **The repo overrides.** A documented repo standard always wins; where it endorses something the baseline would flag, suppress the smell.
- **Always a judgement call.** Each smell is a labelled heuristic ("possible Feature Envy"), never a hard violation — and, like any standard here, skip anything tooling already enforces.

Each smell reads *what it is* → *how to fix*; match it against the diff:

- **Mysterious Name** — a function, variable, or type whose name doesn't reveal what it does or holds. → rename it; if no honest name comes, the design's murky.
- **Duplicated Code** — the same logic shape appears in more than one hunk or file in the change. → extract the shared shape, call it from both.
- **Feature Envy** — a method that reaches into another object's data more than its own. → move the method onto the data it envies.
- **Data Clumps** — the same few fields or params keep travelling together (a type wanting to be born). → bundle them into one type, pass that.
- **Primitive Obsession** — a primitive or string standing in for a domain concept that deserves its own type. → give the concept its own small type.
- **Repeated Switches** — the same `switch`/`if`-cascade on the same type recurs across the change. → replace with polymorphism, or one map both sites share.
- **Shotgun Surgery** — one logical change forces scattered edits across many files in the diff. → gather what changes together into one module.
- **Divergent Change** — one file or module is edited for several unrelated reasons. → split so each module changes for one reason.
- **Speculative Generality** — abstraction, parameters, or hooks added for needs the spec doesn't have. → delete it; inline back until a real need shows.
- **Message Chains** — long `a.b().c().d()` navigation the caller shouldn't depend on. → hide the walk behind one method on the first object.
- **Middle Man** — a class or function that mostly just delegates onward. → cut it, call the real target direct.
- **Refused Bequest** — a subclass or implementer that ignores or overrides most of what it inherits. → drop the inheritance, use composition.

### 4. Spawn both sub-agents in parallel

Send a single message with two `Agent` tool calls. Use the `general-purpose` subagent for both.

**Standards sub-agent prompt** — include:

- The full diff command and commit list.
- The list of standards-source files you found in step 3, **plus the smell baseline from step 3** pasted in full — the sub-agent has no other access to it.
- The brief: "Report — per file/hunk where relevant — (a) every place the diff violates a documented standard: cite the standard (file + the rule); and (b) any baseline smell you spot: name it and quote the hunk. Distinguish hard violations from judgement calls — documented-standard breaches can be hard, but baseline smells are always judgement calls, and a documented repo standard overrides the baseline. Skip anything tooling enforces. Under 400 words."

**Spec sub-agent prompt** — include:

- The diff command and commit list.
- The path or fetched contents of the spec.
- The **semantic-consumer test** pasted in full (below) — the sub-agent has no other access to it.
- The brief: "Report: (a) requirements the spec asked for that are missing or partial; (b) behaviour in the diff that wasn't asked for (scope creep); (c) requirements that look implemented but where the implementation looks wrong; (d) additions that fail the semantic-consumer test. Quote the spec line for each finding. Under 400 words."

If the spec is missing, skip the Spec sub-agent and note this in the final report.

#### The semantic-consumer test (paste into the Spec sub-agent)

For every new durable field, event, API, table, config key, or module the diff adds, establish:

| Question | Required answer |
| --- | --- |
| Producer or lifecycle owner | What creates, updates, or owns it? |
| Committed consumer | Which named caller, component, or user reads or acts on it now, in this same change? |
| Semantic use | What behavior or externally visible result changes after consumption? |
| Reachable path | Where does production reach the consumption in the current system? |
| Absence test | Which verified scenario from the spec fails if the addition is removed? |

A roadmap, "future flexibility", a generic read/debug API, or storage alone is **not** a semantic consumer. An addition with no semantic consumer is scope creep — report it under (d). For public or wire contracts, absent in-repo callers are uncertainty, not proof that no consumer exists.

### 5. Aggregate under review economics

Present the two reports under `## Standards` and `## Spec` headings, verbatim or lightly cleaned. Do **not** merge or rerank findings across the two axes — a change can pass one and fail the other, and separate reporting stops one axis from masking the other.

Three economics rules bind the final report:

1. **Machine-proven properties are out of scope.** Anything CI, lint, typecheck, or an already-passing gate proves must not appear as a finding ("lint should pass", "types should be correct" are noise). If a gate is red, say "gate X is red" once — don't enumerate the violations its output already lists.
2. **Separate blockers from suggestions.** Blockers carry location, impact, and evidence; suggestions are everything else. One substantiated blocker beats a list of nits — if there are only nits, say the diff is clean at blocker level.
3. **Receiving review: verify or rebut, never perform agreement.** When responding to review findings (from humans or other agents), each point gets a technical verification result — accept-and-fix, or rebut on technical grounds. Unverified agreement ("你说得对" with no verification) is a process violation.

### 6. Prose in the diff is a blocking review surface

Any new or changed prose in the diff (spec text, design docs, `docs/`, comments, decision records) gets semantic review against two repo standards — mechanical gates do not cover writing quality:

- [noesis-prose-standard](../noesis-prose-standard/SKILL.md): readability — conclusion-first, present-state narration, tables for enumerable facts only, nesting ≤ 2, and the AI-flavor symptom checklist (boilerplate openings, mechanical structure, homogenized wording).
- [noesis-prose-hygiene](../noesis-prose-hygiene/SKILL.md): session-perspective residue — dead references, change narration, review choreography.

A diff whose prose fails either standard is a blocker, not a suggestion.

End with a one-line summary: total findings per axis, and the worst issue _within each axis_ (if any). Don't pick a single winner across axes — that's the reranking the separation exists to prevent.
