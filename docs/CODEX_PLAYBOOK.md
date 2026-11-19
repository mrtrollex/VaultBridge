# Codex Playbook for VaultBridge

This file owns contributor/agent execution workflow. [AGENTS.md](../AGENTS.md) owns mandatory
invariants. The [repository skill](../.agents/skills/vaultbridge-issue-workflow/SKILL.md) routes
implementation, confirmed-finding repair and fresh review; it does not replace task scope.

## Select one task and read the owning sources

1. Read AGENTS.md.
2. Read the exact issue/task file and matching BACKLOG.md contract, when one exists.
3. Inspect the responsible implementation and nearest tests, including failure paths.
4. Consult architecture/ADRs for ownership or compatibility changes, PROJECT_STATE.md for factual
   release/operational state and ROADMAP.md for direction only when relevant.

Use the [current-work backlog entry point](../BACKLOG.md#current-work-entry-point).
There is no permanently recommended next task in this playbook. Roadmap candidates, historical
sequences and task numbering do not authorize implementation. Prefer one bounded task per branch/PR.
If authoritative requirements disagree, report the conflict before guessing or expanding scope.

## Prepare the implementation packet

From the repository root, with the development environment active:

```text
python scripts/agent_task.py --task-file path/to/task.md
# Or supply short exact task context inline:
python scripts/agent_task.py --task "Implement the explicitly scoped change."
```

Exactly one task source is required. The tool records branch and pre-existing changes and writes
`.agent/task_packet.md`; repeatable `--context-file` embeds explicitly selected context within
bounded size limits. It does not infer requirements from branch names, scan all project docs or
invoke Codex. Inspect the relevant code directly even when the packet contains excerpts.

Start implementation with that packet, or read it and continue if already in the implementation
session. State a short plan, assumptions, likely files, exclusions and observable success criteria.
Preserve unrelated working-tree changes. Never include real vaults or credentials in task files.

## Implement and check

Keep behavior in its existing owner and make the smallest coherent change. Preserve public aliases,
operation IDs, errors/status, omitted scope, authorization, path containment, ranking, persisted
formats and lifecycle contracts unless the exact task explicitly authorizes a change.
Add focused tests for changed behavior, including security denial and failure paths.
Do not remove assertions, insert arbitrary sleeps or add skips to hide a failed check.

Use [DEVELOPMENT.md](../DEVELOPMENT.md) for environment setup. Run focused checks while iterating.
The change-aware selector owns final check selection:

```text
python scripts/agent_check.py --dry-run
python scripts/agent_check.py
```

Canonical Python checks are Ruff on `app tests scripts`, full non-E2E pytest (including evaluation)
and `python -m compileall -q app`. UI changes select Chromium E2E; applicable Docker/MCP changes
select Compose/build/container checks. Do not run Ruff over vendored TrueNAS libraries.
Read the actual selector output; this paragraph is not a substitute for its current classification.

Documentation-only changes may select no runtime checks. Validate local links/fragments, examples
against source, release claims against authoritative metadata and changed deployment guidance
against settings/Compose. Run relevant documentation/release/package regression tests.
TrueNAS/deployment and Action/OpenAPI changes can report additional manual verification requirements.
Selector exit success does not certify those manual requirements or an operator lifecycle gate.
A failed or required-but-unavailable check means incomplete work; GitHub CI remains independent evidence.

For browser regression setup, install development dependencies and run
`python -m playwright install chromium`. `tests/e2e/` uses a disposable local application without
real vaults, credentials, model downloads, Docker or external services. Significant new browser
interactions may also need exploratory acceptance inspection; do not invent it from green E2E.

## Finish and hand off for fresh review

Use the **same task source** as preflight:

```text
python scripts/agent_finish.py --task-file path/to/task.md
# Or the same inline task:
python scripts/agent_finish.py --task "Implement the explicitly scoped change."
```

`agent_finish.py` removes its prior managed review packet, runs `agent_check.py` and creates
`.agent/review_packet.md` only after selected automated checks pass. It does not invoke Codex or
an external service. Report exact checks/results, manual limitations and remaining acceptance gates.
The underlying `agent_review.py` can generate a packet after standalone verification, but does not
run checks itself and must not be presented as proof of verification.

**Stop implementation after the packet exists.** Tell the user to start a separate fresh Codex
session with only `.agent/review_packet.md`, without implementer history. Do not automatically spawn,
delegate to or wait for a reviewer, or perform a review/fix/review loop in the implementation session.

```text
agent_task.py -> implementation -> agent_finish.py
-> separate fresh read-only review -> APPROVE -> explicitly authorized PR / independent CI
```

Fresh review starts from the packet and inspects tracked and untracked scope directly as needed.
It makes no edits, regenerates no packet, delegates no review and reports only concrete actionable
findings by severity (`BLOCKER`, `HIGH`, `MEDIUM`, `LOW`). Finish with exactly `APPROVE` or
`FIXES REQUIRED` on its own line and stop. Passing suites alone do not prove lifecycle/security safety.

In a subsequent repair session, verify and fix only confirmed findings in the responsible owner,
preserve the original task boundary and rerun `agent_finish.py` with the original task source.
Hand off the new packet and stop. A materially changed implementation needs another separate review.
Creating a PR, committing, pushing, publishing or deploying still requires explicit user instruction.

## Documentation ownership and update rule

Use the [documentation ownership table](README.md#document-ownership). Update an owning document
only when behavior, factual state, acceptance evidence or direction actually changes:

- BACKLOG.md: task status/evidence with acceptance criteria and IDs preserved. Distinguish design,
  implementation, publication, downstream delivery and operator lifecycle completion.
- PROJECT_STATE.md: compact current facts and limits, linking to detailed evidence.
- ARCHITECTURE.md and ADRs: current ownership/contracts and accepted decisions.
- ROADMAP.md: product direction and candidate priorities; no repeated execution ledger.
- CHANGELOG.md: published/user-visible changes and clearly separated unreleased work.
- docs/RELEASE_CHECKLIST.md: durable source/image/CI/operator evidence with dates and scope.
- Operator guides: accurate examples/settings and recovery instructions for affected workflows.

Archive superseded narratives when useful; search references before moving and update relative links.
Preserve historical SHA/digest records and original dated claims, labelled as historical.
Do not mechanically rewrite every document after each task or copy the same status story into each.

## Contract-sensitive conventions

- [API versioning](../ARCHITECTURE.md#http-api-versioning): new integrations use `/api/v1`;
  preserve legacy aliases/operation IDs. Health/privacy remain unversioned; docs/redoc/OpenAPI
  routes stay disabled. Removal/migration needs a separate decision.
- [Health and maintenance](DOCKER.md#first-startup-and-health-verification): liveness, workload
  readiness and rich diagnostics have separate roles. Ready lifecycle alone is not search availability.
  `index check` is model-free, persisted and filesystem-immutable; rebuild uses production full sync.
  Maintenance stays stopped-service, with existing exit codes 0/1/2 and no cross-process lock claim.
- [Generic Docker](DOCKER.md): Compose mounts host vault at `/vault`, data inside
  `/vault/.obsidian-chatgpt-data`, host-loopback port to container 8000 and `PUID:PGID` identity.
  README is the public entry point; the Docker guide owns detailed commands/configuration.
- [TrueNAS](../README_TRUENAS.md): Community App defaults (including port 30491) are distinct from
  manual compatibility paths `/mnt/Apps/AppsData/ObsidianChatGPT*`, service `obsidian-api`, container
  `obsidian-chatgpt`, UID/GID 568, `/vault` plus `/data` and `8765:8000`. Do not rename them incidentally.
  Preserve `.env`/external datasets during bundles and never start a duplicate serving stack.
- [Publication](RELEASE_CHECKLIST.md): root Dockerfile, repository-scoped token, pinned publish
  actions, exact version tags and stable-only aliases remain the contract. No PAT/new signing service
  or multi-architecture expansion is implied. Catalog image updates are automatic, forms are separate.
- [Structured logging](../ARCHITECTURE.md): use existing static events and allowlisted context;
  never log credentials, raw URLs, note/query/embedding content, exception text or absolute host paths.
  Internally generated request IDs and matched route templates remain the observability boundary.
- [Retrieval evaluation](../tests/eval/README.md): before changing model, chunks, scoring weights
  or thresholds, record the existing deterministic/benchmark baseline and compare quality, per-case
  ranks and latency. Bigger models or aggregate metrics alone do not justify changing ranking.

Architecture/dependency decisions follow AGENTS.md: accepted ADR first where required, and a concrete
problem/operational-cost explanation before any dependency. Keep functional refactors separate from
documentation work unless the exact task requires both.
