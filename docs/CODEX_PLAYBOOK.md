# Codex Playbook for VaultBridge

Use this file when working on VaultBridge in Codex.

## Source-of-truth order

For every task, use repository context in this order:

1. `AGENTS.md` — mandatory project rules and constraints
2. `PROJECT_STATE.md` — factual current state
3. `ARCHITECTURE.md` — current/target boundaries
4. `ROADMAP.md` — milestone direction
5. `BACKLOG.md` — authoritative scope and acceptance criteria for the exact task

If these documents disagree, do not guess. Report the inconsistency before implementation.

## Recommended workflow

Do **not** ask Codex to implement the roadmap as a whole.

Use one backlog item per branch/PR.

Typical flow:

```text
main
  |
  +-- vb-011-batch-index-commits
  |
  +-- vb-012-background-indexing
  |
  +-- ...
```

## Standard implementation prompt

```text
Implement <TASK-ID> from BACKLOG.md.

Before changing code:

1. Read AGENTS.md.
2. Read PROJECT_STATE.md.
3. Read ARCHITECTURE.md.
4. Read ROADMAP.md.
5. Read the exact <TASK-ID> section in BACKLOG.md.
6. Inspect the current implementation and relevant tests.

First provide a short implementation plan.

Requirements:

- keep the change strictly limited to this task,
- preserve existing API behavior unless the task explicitly changes it,
- preserve security and deployment compatibility,
- add/update focused tests,
- do not add infrastructure or dependencies unless required by the task,
- run all checks required by AGENTS.md,
- do not implement the next backlog task.

At the end report:

1. Summary
2. Files changed
3. Behavior changed
4. Compatibility/migration impact
5. Security impact
6. Tests/checks and results
7. Remaining risks
8. Confirmation that acceptance criteria are satisfied
9. Recommended next backlog task

Do not implement the recommended next task.
```

## Current next task

At the current project state, the next recommended task is:

```text
VB-011 — Batch index commits
```

Always verify this against `PROJECT_STATE.md` and `BACKLOG.md` before starting.

## Review prompt

After a significant task is implemented, run a separate review before merging:

```text
Review the changes made for <TASK-ID> as a senior Python/FastAPI maintainer.

Read AGENTS.md, PROJECT_STATE.md, ARCHITECTURE.md, ROADMAP.md and the exact
task in BACKLOG.md first.

Do not modify files.

Look specifically for:

- acceptance-criteria gaps,
- API regressions,
- path/security issues,
- authentication regressions,
- unnecessary abstractions,
- concurrency/lifecycle problems,
- SQLite/index corruption risks,
- backward-compatibility problems,
- missing failure-case tests,
- accidental scope creep.

Report findings ordered by severity.
```

If findings are confirmed:

```text
Fix only the confirmed findings from the previous review.
Keep scope narrow and rerun all relevant checks.
Do not implement unrelated cleanup or the next backlog task.
```

## Documentation update rule

After a task is merged:

- `BACKLOG.md` — mark the task completed and identify the next task
- `PROJECT_STATE.md` — update factual current state and test baseline
- `ARCHITECTURE.md` — update only when architecture actually changed
- `ROADMAP.md` — update only when milestone/current-position information changed
- `CHANGELOG.md` — update when appropriate for the public project history

Avoid mechanical documentation edits that do not reflect real behavior changes.

## Architecture-change rule

For changes that introduce any of the following, create/review an ADR before implementation:

- new external service,
- new storage engine,
- authentication scheme change,
- API-breaking behavior,
- major runtime dependency,
- distributed coordination model.

## Dependency rule

Do not add a dependency because it is convenient.

Before adding one, explain:

- concrete problem solved,
- why current stack/stdlib is insufficient,
- runtime and image-size impact,
- security/maintenance implications,
- whether it introduces a network/service dependency.

## Semantic-search rule

Before changing the default embedding model, chunk representation, ranking weights or thresholds:

- ensure repeatable evaluation cases exist,
- record baseline results,
- compare before/after quality and latency,
- avoid accepting changes solely because the model is larger or newer.

## Branch hygiene

Prefer one task per branch:

```text
vb-<number>-short-name
```

Examples:

```text
vb-011-batch-index-commits
vb-012-background-indexing
vb-020-markdown-chunker
```

Keep refactors and behavior changes separate unless the task explicitly requires both.
