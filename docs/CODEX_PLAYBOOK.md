# Codex Playbook for VaultBridge

Use this file when working on the project in Codex.

## Recommended workflow

Do not ask Codex to "implement the roadmap" in one request. Give it one backlog item at a time.

### Prompt template

```text
Work on VaultBridge task <TASK-ID> from BACKLOG.md.

Before changing code:
1. Read AGENTS.md, ROADMAP.md, ARCHITECTURE.md and the task definition.
2. Inspect the existing implementation and tests.
3. State a short implementation plan.

Requirements:
- keep the change limited to this task,
- preserve existing API behaviour unless the task explicitly says otherwise,
- add/update tests,
- do not add infrastructure or dependencies unless necessary,
- run the checks required by AGENTS.md.

At the end report:
- files changed,
- behaviour changed,
- tests/checks run and results,
- migration/reindex/security implications,
- any follow-up task you recommend.
```

## First five Codex prompts

### 1. VB-001 — project identity

```text
Implement VB-001 from BACKLOG.md. Normalize the public project identity to VaultBridge in documentation and user-facing application metadata, but do not rename deployment paths/container names that would break the existing TrueNAS installation. Document remaining legacy names as migration work. Run the required checks.
```

### 2. VB-002 — typed config

```text
Implement VB-002. Introduce a typed Settings object for all runtime configuration currently read from environment variables. Preserve every existing default. Add focused tests for valid overrides and invalid numeric configuration. Do not refactor routers/services in the same change.
```

### 3. VB-004 — VaultService

```text
Implement VB-004. Extract vault path resolution, Markdown I/O, filename sanitation, size checks and note enumeration from app/main.py into a VaultService. Preserve the current API contract and error behaviour. Add service-level tests including traversal failures.
```

### 4. VB-003 — routers

```text
Implement VB-003 after VB-002 and VB-004. Split FastAPI routes into routers while keeping paths, operationIds, request/response behaviour and authentication unchanged. Keep main.py as application wiring. Existing API tests should remain valid.
```

### 5. VB-005 — semantic boundaries

```text
Implement VB-005. Refactor semantic search so SQLite persistence is separated from embedding/ranking logic. Preserve current index format if practical; if not, bump the index signature and document automatic rebuild. Keep FakeEmbedder tests model-download-free.
```

## Review prompts

After a significant task, ask Codex separately:

```text
Review the changes made for <TASK-ID> as a senior Python/FastAPI maintainer. Look specifically for API regressions, path/security issues, unnecessary abstractions, concurrency problems, index corruption risks, and missing tests. Do not modify files yet; report findings ordered by severity.
```

Then, if findings are valid:

```text
Fix only the confirmed findings from the previous review. Keep scope narrow and rerun all relevant checks.
```

## Architecture-change rule

For changes that introduce a service, storage engine, authentication scheme, API-breaking behaviour, or major dependency, have Codex create an ADR first and stop for review before implementation.
