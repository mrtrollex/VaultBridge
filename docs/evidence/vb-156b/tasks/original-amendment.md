TASK: VB-156B — Qualification Contract Amendment

MODE: DOCUMENTATION / ARCHITECTURE ONLY

The VB-156B implementation received independent review verdict APPROVE.

However, full acceptance remains blocked by three documented qualification limitations.

The owner authorizes preparation of a PROPOSED, narrowly scoped qualification-contract amendment.

Do NOT automatically accept the amendment.

Read:
- AGENTS.md
- Repository issue-workflow skill
- docs/CODEX_PLAYBOOK.md
- Accepted ADR 0011
- BACKLOG.md
- Relevant qualification requirements

Review the existing ADR numbering before selecting a new ADR ID.

OBJECTIVE

Propose an explicit amendment to VB-156B qualification requirements without weakening any runtime security invariant.

ISSUE 1 — WINDOWS PLAYWRIGHT WEBKIT

External defect:
microsoft/playwright#44141

Upstream correction:
WebKit/WebKit#81431

Requirements:

- Preserve the original failing SameSite assertions.
- Do not describe Windows Playwright WebKit as qualified.
- Define precisely which browser/platform profiles are qualified.
- Keep Windows WebKit excluded until an actually corrected build passes the original tests.
- Do not imply Safari on macOS/iOS was tested.
- Preserve server-side Origin, Fetch Metadata and CSRF protections.
- Preserve Secure, HttpOnly, SameSite=Strict and host-only cookies.
- Retain dedicated-hostname trust across all ports.
- Define explicit criteria for removing the exception.

ISSUE 2 — CHROMIUM BOOTSTRAP

One historical bootstrap failure remains unexplained.

Requirements:

- Preserve the original evidence.
- Do not claim that subsequent passing runs established a fix.
- Classify the residual risk as unresolved availability behavior.
- Define the exact owner risk-acceptance decision.
- Retain deterministic bootstrap regressions.
- Define what recurrence or new evidence must reopen the issue.
- Do not introduce arbitrary retries or weakened tests.

ISSUE 3 — AUTHENTICATED BFCACHE

Authenticated BFCache admission was not observed in the tested Firefox/WebKit environments.

Requirements:

- Distinguish BFCache admission from regular history restoration.
- Document demonstrated native non-BFCache restoration.
- Preserve protected-state erasure and revalidation requirements.
- Never claim actual BFCache admission without evidence.
- Define alternative acceptance evidence where browser admission is unavailable.
- State the remaining limitation precisely.
- Do not weaken caching or privacy headers.

SCOPE

Create a narrowly scoped proposed ADR or qualification amendment consistent with repository ADR conventions.

Status must remain PROPOSED.

Update BACKLOG.md only as needed to reference the pending decision.

Keep ADR 0011 ACCEPTED and technically unchanged until a separately approved amendment establishes the new qualification contract.

Do not modify:
- Application code
- Tests
- Runtime authentication
- Cookie/session behavior
- Existing security controls
- Docker or deployment configuration
- Release metadata

The proposal must distinguish:

1. Implementation review approval
2. Qualification limitations
3. Owner acceptance of residual risks
4. Conditions for VB-156B completion
5. Conditions for future release/deployment
6. Conditions for removing each exception

VALIDATION

Run the repository's applicable documentation checks.

Use the prescribed agent_task.py and agent_finish.py workflow with the same exact task source.

Generate .agent/review_packet.md.

Do not perform an independent review in this session.
Do not commit, push, create a PR or deploy.

FINAL REPORT

A. Proposed amendment summary
B. Exact files changed
C. Qualified browser/platform matrix
D. Explicit exclusions and residual risks
E. Non-negotiable security invariants preserved
F. Required owner decisions
G. Documentation check results
H. Review packet location

Stop after producing the review packet.
