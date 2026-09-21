# Codex Playbook for VaultBridge

Use this file when working on VaultBridge in Codex.

## Source-of-truth order

For every task, start with the smallest authoritative context:

1. `AGENTS.md` — mandatory project rules and constraints
2. the exact relevant section of `BACKLOG.md` — authoritative task scope and acceptance criteria
3. the relevant implementation files and tests

Load additional project documents only when the task needs them:

- `PROJECT_STATE.md` when current project status, compatibility, release state, or historical evidence
  affects the task
- `ARCHITECTURE.md` when module boundaries, lifecycle, ownership, architecture, or cross-cutting
  behavior matters
- `ROADMAP.md` when milestone direction or prioritization matters
- relevant ADRs for architecture-sensitive work

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

Before implementation, prepare the compact implementation packet:

```text
python scripts/agent_task.py --task-file path/to/task.md
# Or provide short task context inline:
python scripts/agent_task.py --task "Implement the explicitly scoped change."
```

Exactly one task source is required. The tool writes `.agent/task_packet.md` by default, records the
current branch and pre-existing working-tree changes, and can embed a known relevant repository file
with a repeatable `--context-file`. It uses conservative size limits and does not infer requirements
from branch names or Git history, scan the repository broadly, invoke Codex, or replace direct
inspection of task-relevant code and authoritative documentation.

Start the implementation Codex session with `.agent/task_packet.md`. After implementation, run:

```text
python scripts/agent_finish.py --task-file path/to/task.md
# Or provide short task context inline:
python scripts/agent_finish.py --task "Implement the explicitly scoped change."
```

Exactly one task source is required: `--task-file` or `--task`. `agent_finish.py` removes its prior
`.agent/review_packet.md`, runs the repository-local verification selector, and creates a fresh packet
only after all selected checks pass. It does not invoke Codex or an external service.

The underlying tools remain independently usable. Run verification alone with:

```text
python scripts/agent_check.py
```

The selector uses the affected files to run the canonical Python, semantic, Docker, deployment, MCP,
and UI checks that apply. A failed or required-but-unavailable check means the task is incomplete.
TrueNAS/deployment and Action/OpenAPI changes may additionally require Codex verification. The
selector complements GitHub CI and does not replace its independent checks; `AGENTS.md` retains the
underlying commands.

After standalone verification passes, generate a packet directly with
`python scripts/agent_review.py --task-file path/to/task.md --output review_packet.md`. The review
tool also accepts `--task`; exactly one task source is required. It does not run verification or
invoke Codex.

Start a fresh Codex session and provide only `.agent/review_packet.md` from `agent_finish.py`, not the
implementer's full conversation or history. The reviewer must not modify files during this pass. If
it reports concrete findings, confirm and fix only those findings, rerun `agent_finish.py`, and
repeat review when the fix materially changes the implementation.

```text
agent_task.py
-> start implementation Codex session with .agent/task_packet.md
implement
-> agent_finish.py
-> fresh Codex review of .agent/review_packet.md
-> fix confirmed findings if necessary
-> agent_finish.py again
-> APPROVE
-> PR / CI
```

## Dashboard browser verification

Install the development dependencies, then install the only supported E2E browser with
`python -m playwright install chromium`. The Playwright suite under `tests/e2e/` is persistent
known-regression protection against a disposable local VaultBridge instance; it never uses an
operator vault, credentials, semantic model download, Docker, or an external service.

Codex browser inspection is exploratory acceptance verification for new user-visible behavior.
Playwright passing does not replace that inspection when a significant new interaction benefits
from it, but manual browser exploration is not a duplicate mandatory gate for every trivial UI or
CSS change when the automated regression coverage is sufficient.

## Standard implementation prompt

```text
Implement <TASK-ID> from BACKLOG.md.

Before changing code:

1. Read AGENTS.md.
2. Read the exact <TASK-ID> section in BACKLOG.md.
3. Inspect the relevant implementation files and tests.
4. Read PROJECT_STATE.md when current project status, compatibility, release state or historical
   evidence affects the task.
5. Read ARCHITECTURE.md and the relevant ADRs when module boundaries, lifecycle, ownership,
   architecture or cross-cutting behavior matters.
6. Read ROADMAP.md when milestone direction or prioritization matters.

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

The next recommended coding task is **VB-102 — Verified backlinks**. Implement only the exact
`BACKLOG.md` scope by reusing the completed VB-100 parser/resolver, VB-101 outgoing-relationship
service, and existing `VaultService` boundaries. Do not start VB-103 or add REST, MCP, dashboard,
ranking, persistence, or Markdown-mutation behavior as part of VB-102.

VB-082 remains an incomplete milestone and lifecycle-validation item. Its remaining gates require
live/operator TrueNAS evidence and must not be treated as a normal autonomous coding task. VB-091 is
complete. VB-092 is implemented, and its production-image container validation is covered by the
completed VB-093 task. VB-032 and VB-033 remain deferred/optional; VB-034 remains a later opt-in
write capability after verified read-only relationship resolution.

Always verify current task status against the relevant `BACKLOG.md` section and, when project status
matters, `PROJECT_STATE.md` before starting.

## Index-maintenance CLI convention

Future semantic lifecycle, schema, signature, chunking, embedding-input or synchronization changes
must keep `python -m app.cli index check` a persisted, filesystem-immutable, stopped-service view and
keep `python -m app.cli index rebuild` on the production full-sync path. Check must remain cheap,
model-free, and distinct from authoritative live `/health` and `/health/ready` reporting. Rebuild must
preserve Markdown, current signature semantics and durable batches. Do not claim cross-process
locking; operators must stop the application before check or rebuild until a
separate task explicitly introduces coordination. Preserve exit codes `0` healthy/success, `1`
integrity/readiness or operational rebuild failure, and `2` CLI/configuration/programming failure.

## Health probe convention

Preserve the distinct endpoint roles: `/health/live` is dependency-free process liveness,
`/health/ready` is minimal usable-vault plus semantic-search workload readiness, and `/health` is rich
operator diagnostics. Probe checks must remain public, side-effect free, cheap, and covered by the
normal request observability middleware without additional per-probe application events. Do not
derive readiness only from `semantic_index_state == ready`; a compatible previous index can remain
searchable during or after a failed refresh, and a compatible legacy index with chunks can be
searchable without persisted lifecycle state. Expected filesystem/SQLite availability failures are
not-ready states; unexpected programming errors retain the normal server-error boundary.

## API versioning convention

New application integrations should use `/api/v1`. The original unversioned note/search routes are
compatibility aliases and retain their established operation IDs for clients such as the current
ChatGPT Action. V1 operation IDs use the explicit stable `V1` suffix. Register both paths from one
endpoint function so authentication, validation, domain calls, responses, errors, and observability
cannot drift or execute twice.

Keep `/health`, `/health/live`, `/health/ready`, and the schema-hidden `/privacy` endpoint outside the
application API namespace. Do not enable `/docs`, `/redoc`, or `/openapi.json`. Removing legacy
routes or migrating the external Action configuration requires a separate task and migration
decision; do not invent a removal date.

## Generic Docker deployment convention

Keep `README.md` as the canonical normal-Docker path and `README_TRUENAS.md` as the TrueNAS-specific
path. Verify every command and value against `Dockerfile`, `docker-compose.yml`, `.env.example`, and
typed settings. Generic Compose bind-mounts host `OBSIDIAN_VAULT_PATH` at `/vault`, stores derived
semantic data at `/vault/.obsidian-chatgpt-data`, maps host-loopback `API_PORT` to container port
`8000`, and runs as `PUID:PGID`; do not substitute the TrueNAS `/data` layout. Prefer `/api/v1` in
new smoke tests, preserve public unversioned health probes, and keep `index check`/`index rebuild` as
stopped-service operations. Do not expose resolved secrets through environment dumps or pasted
Compose output.

## TrueNAS deployment convention

The accepted VaultBridge Community App through **Apps > Discover Apps** is the preferred normal
TrueNAS installation path. It uses the catalog-generated configuration and storage choices, pulls the
published VaultBridge image, and currently defaults its Web UI port to `30491`.

The source-built / Custom App deployment remains supported as an advanced/manual compatibility path.
For that path, preserve the compatibility locations under
`/mnt/Apps/AppsData/ObsidianChatGPT*`, Compose service `obsidian-api`, container
`obsidian-chatgpt`, runtime identity `568:568`, `/vault` and `/data` mounts, and `8765:8000` mapping
unless a separate migration task says otherwise. Its TrueNAS-managed Custom App YAML includes
`truenas-install.yml`; shell-managed Compose is a distinct alternative and must not create a
duplicate serving stack. Source bundles come from the existing `make-bundle.ps1` `git archive`
helper, preserve `.env`, and never contain or overwrite the external vault/data datasets.
Maintenance remains stopped-service and uses an isolated container with the same `.env`, mounts, and
semantic settings.

Do not apply Community App defaults such as port `30491` to the source-built compatibility path, or
copy legacy/custom values such as `8765:8000` into the Community App contract.

## Container publication convention

Keep the repository-root `Dockerfile` as the single production image definition. GitHub Release
publication uses a `v`-prefixed semantic version, the repository-scoped `GITHUB_TOKEN`, and
full-commit-SHA-pinned actions to publish `ghcr.io/<lowercase-repository-owner>/vaultbridge`. Every
release gets its exact version tag without `v`; stable releases also update `major.minor`, `major`,
and `latest`, while prereleases update no stable alias. The metadata action owns deterministic GHCR
lowercasing and OCI labels. Release version tags are not reusable; deployments that require a
cryptographically immutable reference should use the published digest.

Use minimal BuildKit provenance when it remains supported by the existing build action and requires
no broader token permissions. Stronger GitHub attestations, SBOM policy, or external signing need a
separate hardening decision; do not introduce credentials or services implicitly.

Keep package-write permission on the publish job only, never publish from a pull request or
`pull_request_target`, and do not add PAT credentials. The first package publication may be private;
visibility and repository linkage must be checked deliberately in GitHub Packages. Source-build
Docker Compose and TrueNAS deployment remain supported, and a release image must not change runtime
paths, mounts, ports, settings, APIs, or semantic-index compatibility. Publish only the normal Linux
architecture until VB-055 explicitly introduces multi-architecture support.

## Structured logging convention

Future runtime features should emit VaultBridge-owned events through `app/core/logging.py`. Use a
stable machine-readable event name, a static safe message, and only the existing allowlisted context
needed by operators. Never pass API keys, Authorization headers, note content, embedding/query text,
exception messages, or absolute host paths. Add a context field only when the feature has a concrete
operational need.

HTTP request IDs are generated internally at ASGI entry and are available automatically through the
logging context. Do not manually copy `X-Request-ID`, Authorization, raw paths/query strings, or body
fields into events. Use matched route templates and the middleware's monotonic duration for request
lifecycle records. Synchronous application events may inherit the active request ID; do not assume
executor/background work carries it, and do not extend queue payloads with request context without a
separate backlog decision. Uvicorn access/server logging remains outside VaultBridge's JSON event
contract.

## Fresh-agent review

Use the generated `agent_review.py` packet as the review prompt. Its bounded context replaces the
older practice of loading the full project documentation and implementer history into the reviewer.
The packet tells the reviewer to assume the change may be wrong, inspect concrete correctness and
risk areas, order actionable findings by severity, avoid speculative noise, and finish with one
`APPROVE` or `FIXES REQUIRED` recommendation.

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
- run `python -m pytest -q tests/eval`,
- record baseline results,
- compare before/after quality and latency,
- avoid accepting changes solely because the model is larger or newer.

The deterministic VB-022 baseline is checked in `tests/eval/baseline.json`; its generated table and
known limitations are documented in `tests/eval/README.md`. Future retrieval changes must compare
against the accepted VB-024 normalized-ranking baseline and report per-case ranks even when aggregate
metrics remain 100%.

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
