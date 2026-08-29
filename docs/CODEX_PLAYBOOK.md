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
VB-070 — Web Dashboard architecture and security design
```

Always verify this against `PROJECT_STATE.md` and `BACKLOG.md` before starting.

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

Preserve the production compatibility paths under `/mnt/Apps/AppsData/ObsidianChatGPT*`, Compose
service `obsidian-api`, container `obsidian-chatgpt`, runtime identity `568:568`, `/vault` and `/data`
mounts, and `8765:8000` mapping unless a separate migration task says otherwise. The primary
deployment uses a TrueNAS-managed Custom App whose YAML includes `truenas-install.yml`; shell-managed
Compose is a distinct alternative and must not create a duplicate serving stack. Source bundles come
from the existing `make-bundle.ps1` `git archive` helper, preserve `.env`, and never contain or
overwrite the external vault/data datasets. Maintenance remains stopped-service and uses an isolated
container with the same `.env`, mounts, and semantic settings.

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
