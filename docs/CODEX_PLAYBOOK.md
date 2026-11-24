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
Explicit changed files may use native absolute paths. Those paths are resolved against the
canonical checkout root, and the resulting repository-relative names are checked for portability.
Ambiguous Windows spellings and parent traversal are rejected before resolution.

Documentation-only changes may select no runtime checks. Validate local links/fragments, examples
against source, release claims against authoritative metadata and changed deployment guidance
against settings/Compose. Run relevant documentation/release/package regression tests.
TrueNAS/deployment and Action/OpenAPI changes can report additional manual verification requirements.
Selector exit success does not certify those manual requirements or an operator lifecycle gate.
A failed or required-but-unavailable check means incomplete work; GitHub CI remains independent evidence.

### Read the timing and result report

Each completed automated run prints an elapsed time for every selected check and for the whole
execution phase. Per-check timing includes the availability probe, when needed, and the command
attempt. The aggregate covers the loop from the first probe through the last result, including
time between checks. Discovery, source snapshots, and report writing are outside that interval.
UTC timestamps identify when the checks ran. Elapsed durations use the monotonic clock.

`.agent/validation-results.json` holds the most recent completed run. Open it directly or run
`python -m json.tool .agent/validation-results.json`. The versioned report records a run ID,
resolved base commit, canonical base reference when available, merge base, changed paths and
their classifications, ordered commands and outcomes,
wall-clock timestamps, elapsed nanoseconds, exit codes, and outstanding manual requirements.
Each selected check is required. An unavailable check distinguishes a missing prerequisite from
a command that could not launch. Terminal diagnostics and subprocess output remain available for
investigating failures such as application errors or broken fixtures.

Command identities use repository-defined arguments and a logical `python` executable name.
The report records platform, architecture, Python and selected verification-tool versions.
It omits process environment values, executable paths and subprocess output. Git identity
combines HEAD with a fingerprint of the Git-visible index and worktree delta, including
nonignored untracked paths. A separate raw SHA-256 covers the working-tree bytes of every path in
HEAD or the index, including files that Git reports as clean. This includes test code,
configuration and data anywhere in the repository. The inventory is sorted by Git path bytes,
and the digest distinguishes missing and regular files. A tracked symlink, reparse point,
non-regular file or unreadable input makes the snapshot unavailable. Fingerprinting checks file
metadata before and after each read and checks the inventory and path metadata again at the end.

Each source snapshot enumerates and reads the tracked inputs again. This adds inventory and
filesystem I/O to ordinary validation and each managed handoff comparison. Dry-runs take no
snapshots, and check timings keep excluding snapshot work. The two digests cover different
observations. For example, a line-ending change can preserve Git's blob and change the raw
digest. Snapshots cover the observed checkout. Ignored untracked inputs, tools outside the
checkout, and edits made and reverted between observations remain outside that coverage.

The report is an ignored local diagnostic, replaced atomically when possible. Dry-runs leave it
untouched. A failed write leaves the prior report in place and keeps the standalone selector exit
code. Managed finish requires the report from its own invocation.
For durable qualification evidence, keep reviewed, sanitized artifacts in the PR. Identify
their exact source, platform and relevant tool versions, executed checks, outcomes and limitations.
The local report does not automatically become PR evidence and is never used to reuse a result
or skip a check.

For browser regression setup, install development dependencies and run
`python -m playwright install chromium`. `tests/e2e/` uses a disposable local application without
real vaults, credentials, model downloads, Docker or external services. Significant new browser
interactions may also need exploratory acceptance inspection; do not invent it from green E2E.

### Declare durable qualification evidence

When the task explicitly requires durable evidence, pass the repository-relative manifest path
to `agent_finish.py --evidence-manifest` alongside the original task source. The declaration makes
every listed artifact mandatory. Ordinary tasks can use the flag when they have evidence to
check. To run just the evidence preflight:

```text
python scripts/agent_evidence.py docs/qualification/evidence.json
```

Use a UTF-8 JSON manifest with this shape, substituting the actual SHA-256 of each artifact
(64 lowercase hexadecimal characters):

```json
{
  "schema_version": 1,
  "artifacts": [
    {
      "path": "docs/qualification/results.md",
      "sha256": "<sha256 of results.md>",
      "references": ["docs/qualification/cases.json"]
    },
    {
      "path": "docs/qualification/cases.json",
      "sha256": "<sha256 of cases.json>"
    }
  ]
}
```

Paths are canonical, slash-separated and repository-relative. The manifest and artifacts must
be regular files outside `.agent/` and Git metadata, without symlink indirection, and must pass
Git ignore checks. Hashes cover on-disk bytes, so use consistent line endings for evidence files
shared across platforms. Optional `references` name other hashed files in the same manifest.
Preflight checks these declared references without following Markdown links, anchors or external
URLs. Finish binds the original manifest SHA-256 to that invocation and rechecks it and its
artifacts through packet publication. A replacement manifest fails even if it validates by itself.
Linux opens evidence under the repository directory handle using a scoped `openat2` lookup.
Windows opens each component relative to a verified directory handle on a local drive, rejects
reparse points, and holds the directory handles while reading the file. Both platforms hash the
same file handle they checked. Evidence preflight fails on platforms or filesystems where the
required operations are unavailable.

Preflight establishes availability and integrity against the declaration. The author must
include the manifest and artifacts in the PR, and the reviewer must assess what the evidence
covers, whether it supports the claims, and whether it is safe to share. Keep raw traces and
confidential artifacts out of the PR. The tool does not copy evidence or publish anything.

## Finish and hand off for fresh review

Use the **same task source** as preflight:

```text
python scripts/agent_finish.py --task-file path/to/task.md
# Or the same inline task:
python scripts/agent_finish.py --task "Implement the explicitly scoped change."
```

`agent_finish.py` takes the managed packet lock, removes its prior packet, checks declared
evidence and runs `agent_check.py` with a unique run ID. It matches the report to both source
digests, the full changed-file scope and every required command and outcome. It stages the packet,
checks the diff and evidence again before publication, and checks the published result's source
before reporting success. A missing, malformed or mismatched report blocks the packet. The
packet still omits diffs over 100 KB. After a forced termination, check that the finish process
has exited before removing an abandoned empty `.agent/finish.lock` directory. Only
`agent_finish.py` publishes `.agent/review_packet.md`. That filename and its case variants
are reserved in the managed directory, including when it has no packet yet. Use stdout or a
distinct file for standalone packet generation.

For a task that requires retained focused Windows and WSL results, repeat `--focused-results`
with the applicable `.agent/focused-windows-<timestamp>.json` and
`.agent/focused-wsl-<timestamp>.json` paths. The local schema version 1 records the normalized
pytest command, UTC timestamps, elapsed nanoseconds, exit code, environment, test counts and
before/after source identities. Finish includes only successful focused records that match both
source digests. A Windows and WSL/Linux pair must have matching input bytes. Finish passes
the approved record digests to the renderer, which verifies the bytes it parses. Finish checks
the staged and published packet against the approved records. Keep failed attempts as local
diagnostics.
Report exact checks/results, manual limitations and remaining acceptance gates.
The underlying `agent_review.py` can generate a packet after standalone verification, but does not
run checks itself and must not be presented as proof of verification.

**Stop implementation after the packet exists.** Tell the user to start a separate fresh Codex
session with only `.agent/review_packet.md`, without implementer history. Do not automatically spawn,
delegate to or wait for a reviewer, or perform a review/fix/review loop in the implementation session.

```text
agent_task.py -> implementation -> agent_finish.py invokes agent_check.py
-> agent_review.py writes review_packet.md -> separate fresh read-only review
-> APPROVE -> explicitly authorized PR / independent CI
```

| Step | Owner |
| --- | --- |
| Task context and pre-existing working-tree status | `agent_task.py` |
| Change discovery from base, HEAD, index, worktree and nonignored untracked files | `agent_check.py` |
| Classification, check selection, execution, timing, result reporting and manual requirements | `agent_check.py` |
| Evidence gate and completion sequence | `agent_finish.py`, with `agent_evidence.py` for manifest validation |
| Repository summary, diff and packet generation | `agent_review.py`, using discovery and selection from `agent_check.py` |
| Findings, qualification judgment and independent CI | Fresh reviewer and GitHub Actions |

Running the full selector during implementation and again through finish repeats the selected
commands. Finish always executes its own checks, and CI separately runs its configured jobs.
The packet generator repeats discovery and selection to describe the review scope. It does not
execute verification. Use focused checks while iterating and use finish for the full local run.

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
