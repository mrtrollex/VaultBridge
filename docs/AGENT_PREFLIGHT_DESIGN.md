# Early risk and feasibility preflight

Phase 2A is a design proposal. It changes no workflow execution.
[AGENTS.md](../AGENTS.md) governs invariants and the [Codex Playbook](CODEX_PLAYBOOK.md) governs execution.

## Current workflow

[agent_task.py](../scripts/agent_task.py) takes one exact task source, records branch and pre-existing
changes, and writes `.agent/task_packet.md` with bounded, explicitly selected context. Its role ends
at context preparation. The implementer reads the relevant code and tests and states a short plan.

Implementation and focused checks lead to [agent_finish.py](../scripts/agent_finish.py) with the same
task source. Finish runs [agent_check.py](../scripts/agent_check.py), which owns change discovery and
selection of required checks. Finish binds results to the observed checkout using Git and raw tracked
input fingerprints, verifies any declared evidence, and manages publication of `.agent/review_packet.md`.
Failed or required-but-unavailable checks leave the task incomplete. Any applicable manual requirements
remain separate. Documentation-only scope can select zero automated checks.

The implementation session stops at packet handoff. A separate read-only review starts from the packet
and returns `APPROVE` or `FIXES REQUIRED`. Confirmed findings are repaired within the original scope,
followed by a new finish run and fresh review for material changes. After approval, PR work requires
explicit authorization. GitHub CI provides independent evidence.

## Proposed workflow

```text
agent_task.py -> assess risk and test critical assumptions -> implementation -> focused testing
-> agent_finish.py -> separate independent review -> authorized PR / independent GitHub CI
```

Add prompts to task preparation. The implementer completes the assessment after reading the exact
task and nearest code/tests, before production edits. The result replaces the current short plan in
the implementation session. Keep it adjacent to the task packet in the session, with the exact task
source left intact for finish. Record early probe results there and use the existing evidence and
handoff paths for acceptance evidence the task requires. Revisit affected entries if a probe, scope
change or new platform requirement invalidates an assumption. Escalate only the affected work.

## Risk triggers and depth

Use the highest applicable trigger. Classify by what could fail and what can be verified. A
one-line change to authorization or check selection can be HIGH. A local prose correction can be LOW.

| Level | Triggers | Minimum preflight |
| --- | --- | --- |
| LOW | Bounded wording or local cleanup with clear ownership, unchanged contracts, and an obvious check. No material unknowns. | One short checklist with intended result, preserved behavior, exclusions and a check. Combine answers and omit irrelevant prompts. |
| MEDIUM | Bounded behavior or test change with a predictable failure path, compatible internal refactor, portable behavior needing confirmation, or a reversible configuration change with clear ownership. | Name the affected invariants, assumptions, relevant environments, likely failure paths and focused checks. Confirm prerequisite availability where it matters. |
| HIGH | Affects a public compatibility contract, a security or privacy boundary, filesystem or Git identity used for access or validation, concurrency or lifecycle ownership, persisted data or migrations, or deployment with consequential rollback or availability risk. Also use HIGH when a material behavior has no credible test path or a multi-owner task has unresolved acceptance or scope boundaries. | Investigate feasibility and run the smallest decisive probe for each material uncertainty before production edits. Define negative cases, required environments, evidence and rollback or failure behavior. |

Use this table to check the actual impact. Public aliases, operation IDs, request/error semantics and
omitted scope are compatibility concerns. Traversal, symlinks, reparse points, note writes, logs,
credentials and content disclosure are security or privacy concerns. Compare the kernel, filesystem
and Git worktree layout when Windows, Linux or WSL semantics can differ. Consider interrupted work,
retries and overlapping owners for lifecycle changes. For persistence and operations, identify
existing data, rollback and any operator or manual verification requirements. Confirm that the
available fixture or environment can exercise the failure. Major dependencies and changes covered
by the ADR rule require the existing approval first. File count alone does not determine risk.

## Questions and output

Answer these at the depth the risk warrants. A LOW task can cover them in a few lines.

1. What observable behavior changes, and what must remain true?
2. Which assumptions might be wrong, and which platforms or environments can invalidate them?
3. What can fail or be denied? What should the caller, data and artifacts look like afterward?
4. What is the smallest early test of each critical assumption? What result would falsify it?
5. Which dependencies, fixtures, access, decisions or external prerequisites are needed and available?
6. What is explicitly outside the task, and what observable results constitute acceptance?

Use a single compact Markdown section in the implementation plan.

```text
Task summary
Risk level and rationale
Critical invariants
Top risks and mitigations
Required early tests and expected outcomes
Missing or unconfirmed prerequisites
Acceptance criteria
Scope boundary
Recommendation (GO / NEEDS CLARIFICATION / BLOCKED)
```

Mark assumptions as unverified until checked. For material probes, record the command or fixture,
platform/filesystem, observed result and any remaining limit. Label planned checks as planned.
Use `GO` when the agreed scope is feasible and critical assumptions are resolved. Noncritical
unknowns can carry a named check and resolution point. Use `NEEDS CLARIFICATION` for a missing
decision, conflicting requirements or uncertain scope. Use `BLOCKED` when a required platform,
fixture, dependency, approval or credible test path is unavailable, or an early probe rules out
the proposed approach and no feasible in-scope approach remains. In either case, record what
would clear the issue and pause affected production work. Continue safe investigation in scope.
The recommendation guides the implementer. Existing finish, review and CI gates still apply.

## Worked examples

### A. Correct a typo in contributor prose

- **Task / risk** — Fix one misspelled word in a contributor guide. LOW because its meaning is clear.
- **Invariant / scope** — Preserve instructions, commands and links. Change only the affected sentence.
- **Risk / early check** — Read the sentence in context to confirm the correction preserves meaning.
- **Prerequisites / acceptance / recommendation** — No additional prerequisites. Verify the one-line
  diff and whitespace, then use normal finish. GO.

### B. Change native absolute-path classification in validation tooling

- **Task / risk** — Classify supported native absolute paths by unambiguous repository identity. HIGH
  because a spelling or Git index mismatch can omit required checks or select a different scope.
- **Invariants / scope** — Preserve the full check set for canonical and supported alias inputs.
  Reject ambiguity, unreadable identity and out-of-checkout paths. Preserve distinct entries on
  case-sensitive filesystems, including paths that share an inode through a hard link. Limit changes
  to the affected path discovery and tests. Exclude changes to check policy, evidence and publication.
- **First probe** — In a disposable Git repository with an isolated index, verify that both
  `app/main.py` and `APP/MAIN.PY` are indexed while the filesystem resolves both to one entry.
  Exercise both exact spellings and a third case variant with `agent_check.py --dry-run --changed-file`.
  Each must report ambiguity and exit 2 before printing a check selection. This catches the Phase 1
  exact-index-match shortcut even when testing a non-exact alias would already show ambiguity.
- **Controls and denial** — With one indexed entry, verify alternate spellings select the same full
  set as the canonical path, including Python, UI and MCP/container checks for `app/main.py`. Compare
  distinct files on case-sensitive Linux, a new Python path, and a hard link under a distinct name.
  Check identity lookup errors, outside-root paths and malformed Windows spellings. Verify a real
  8.3 alias when applicable and available. Reuse the [selector cases](../tests/test_agent_check.py).
- **Platform evidence / prerequisites** — Run with native Windows and WSL on the Windows mount, and
  use a case-sensitive Linux filesystem as a control. Verify the actual case behavior and index
  contents, and exercise a Windows-created linked worktree from WSL if applicable. Record which
  features exist and which results are missing. A symlink fixture on Linux exercises alias resolution
  and leaves Windows case semantics unverified.
- **Acceptance / recommendation** — The ambiguous cases deny, unique aliases retain the original
  check set, and ordinary supported paths still work. In this example, assume access to the required
  WSL mount is missing. Recommend BLOCKED until access is restored and pre-edit probes establish
  the failure and feasible test path, or confirm the invariant on the baseline. Collect final
  focused results on matching input bytes when the task requires retained cross-platform evidence.

For changes that also touch source or packet identity, add probes at the affected boundary before
coding. Test a Git-clean LF/CRLF byte change, replacement with a narrower valid manifest, or source
drift and competing finish runs at publication. Assert stale evidence is rejected, failed runs leave
no managed packet of their own and a competing run cannot overwrite the owner's packet. The existing
[identity tests](../tests/test_agent_validation.py) and [evidence tests](../tests/test_agent_evidence.py)
show these Phase 1 cases. Early probes reduce predictable gaps. Independent review still matters.

## Integration and adoption

| Option | Design and tradeoff |
| --- | --- |
| A. Instructions and template | Put a short static prompt under the existing packet's implementation instructions and link to the playbook. The implementer writes the assessment in the same session. Adds no probes or new inputs to task generation, but relies on the implementer to use judgment. |
| B. Deterministic task-tool assistance | Let the caller explicitly request an advisory check preview for supplied candidate paths, delegating to existing selector logic. This could surface likely checks, but adds input and error handling and can mislead when candidate paths are incomplete. It cannot infer feasibility, filesystem semantics or risk. |

Start with A. Existing owners and evidence flows stay in place. Consider B only if the pilot shows
repeated, costly check-discovery misses. Use no keyword scoring, external service or new dependency.

Budget **1–2 minutes** for LOW, **5–10 minutes** for MEDIUM and **15–30 minutes** for an initial HIGH
pass, including a small disposable probe where feasible. These are planning timeboxes. If a material
unknown remains, record the blocker or clarification, then narrow the task with authorization or
agree a bounded investigation. Reuse existing fixtures and the normal implementation plan. Avoid
mandatory full test runs at this stage. Run the existing completion checks at finish.

For a small pilot, record task start when generating the task packet. Use existing PR notes or
task summaries to record the risk level, count of finished independent reviews (including the first),
count of distinct confirmed material findings first raised after implementation began, and elapsed
time to accepted PR. Use merge time as the acceptance timestamp and mark absent PRs as unavailable.
Compare similar-risk tasks with a recent baseline and look for fewer extra cycles and late findings
without increasing elapsed time. Record missing data as such. Use no new tracker or automation.

## Phase 2B boundary and acceptance

Propose one independently reviewable change limited to `scripts/agent_task.py` (short static prompt),
`docs/CODEX_PLAYBOOK.md` (the handoff and depth instructions), and `tests/test_agent_task.py`.
Keep the skill and playbook ownership split. Use the existing task packet writer.

- Verify that both inline and file tasks produce one prompt and preserve the exact original task
  block, existing context and status summaries. Exercise default and explicit output.
- Keep CLI flags, defaults, exit codes, UTF-8, destination restrictions and byte limits. Count the
  new fixed text within the existing limit. Test optional context omission and verify that a packet
  whose required framing no longer fits fails cleanly while preserving prior output.
- Verify the prompt adds no subprocess calls or output files. Keep the existing alias, no-mutation
  and managed-destination regression tests passing. Confirm the selected checks via normal finish.
- Acceptance includes a manual walk-through of the LOW and HIGH examples with the same template.
  LOW fits in a few lines. HIGH names a falsifiable early test, required environments and the
  condition that pauses implementation. Either recommendation leaves validation selection intact.
- Exclude option B, risk inference, recommendation enforcement, new packet or report formats,
  changes to `agent_check.py`, finish, evidence, review or CI, runtime changes, caching, review
  automation and measurement infrastructure.

Open decisions for the pilot are the cohort size and observation window, and whether costly
check-discovery misses justify option B. Phase 2B can proceed with A without resolving either.
