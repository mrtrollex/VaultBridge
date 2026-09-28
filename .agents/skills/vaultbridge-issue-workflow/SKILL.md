---
name: vaultbridge-issue-workflow
description: "Implement or review a VaultBridge GitHub issue or BACKLOG.md item using the repository task, validation, and fresh-review packet workflow. Use for scoped issue implementation, confirmed review fixes, or a fresh findings-only review; do not use for general product operation."
---

# VaultBridge Issue Workflow

Use the exact GitHub issue, task file, or backlog item as the scope boundary. Follow
`docs/CODEX_PLAYBOOK.md` for detailed handoff instructions and project-specific conventions; do not copy
those conventions into this skill.

## Choose the mode

- Use **implementation** for a new issue or backlog item.
- Use **confirmed-finding repair** only after review findings have been reproduced or verified.
- Use **fresh review** for an independent assessment of a completed change.

## Implementation

1. Orient:
   - Read `AGENTS.md`, the exact issue and, when applicable, the matching `BACKLOG.md` section, then the
     relevant code and nearest tests.
   - Consult `ARCHITECTURE.md`, accepted ADRs, `PROJECT_STATE.md`, or `ROADMAP.md` only when the task needs
     their subject matter.
   - Before normal implementation, run `python scripts/agent_task.py` with exactly one `--task-file` or
     `--task` source. Generate `.agent/task_packet.md` as the implementation preflight context. If this is
     already the implementation session, read the packet and continue; otherwise use it to start the
     implementation session.
   - If a higher-priority instruction forbids creating the packet, do not violate that boundary; disclose
     the omitted preflight in the final report.
2. Bound:
   - State assumptions, likely files, explicit non-goals, and a falsifiable success criterion.
   - Stop if the task is missing, contradictory, or would silently change a protected API, security,
     storage, or deployment contract.
3. Change:
   - Inspect the responsible layer before editing and keep behavior in its existing owner.
   - Make the smallest coherent diff, preserve local style, and add focused tests for changed behavior.
   - Do not perform unrelated cleanup or begin the next backlog task.
4. Prove:
   - Run the narrowest relevant check while iterating.
   - Follow the validation contract below before handoff.
5. Handoff:
   - After `agent_finish.py` succeeds and `.agent/review_packet.md` exists, stop the implementation
     session. Do not spawn, delegate to, or wait for a reviewer or subagent, automatically perform a
     fresh review, or enter a review/fix/review loop.
   - Tell the user to start a separate fresh Codex session and provide only `.agent/review_packet.md`;
     do not include the implementer's conversation history.

## Validation

- Finish with `python scripts/agent_finish.py` using the same task source. It owns final check selection
  through `agent_check.py` and creates `.agent/review_packet.md` only after selected checks pass.
- Treat failure or required-but-unavailable verification as incomplete. Do not claim success from an
  unexecuted required check. GitHub CI remains independent evidence.

## Fresh review

1. Start from `.agent/review_packet.md` and inspect the repository directly when the packet omits a diff.
2. Perform one read-only review pass. Do not modify files, commit, run repair steps, run
   `agent_finish.py`, regenerate the review packet, broaden the task, or spawn or delegate to another
   reviewer or subagent.
3. Inspect repository evidence only as needed to check the exact task, behavior, failure paths, security
   boundaries, compatibility, lifecycle or
   concurrency risks, tests, and accidental scope.
4. Report only concrete actionable findings, ordered `BLOCKER`, `HIGH`, `MEDIUM`, then `LOW`.
5. Finish with exactly one recommendation on its own line: `APPROVE` or `FIXES REQUIRED`.
6. Stop immediately after the verdict.

## Confirmed-finding repair

1. Reproduce or verify each finding against the code before editing.
2. Fix only confirmed findings in the responsible layer; preserve public contracts and unrelated changes.
3. Rerun `agent_finish.py` with the original task source.
4. Once the new `.agent/review_packet.md` exists, stop. Do not automatically launch another reviewer.

## Completion report

Report the changed files, material design decisions, behavior and compatibility impact, security impact,
checks run with results, unavailable required evidence, remaining risk, and whether the exact acceptance
criteria are satisfied. Do not recommend or implement adjacent work unless the user asks for it.
