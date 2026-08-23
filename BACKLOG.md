# VaultBridge Backlog

Tasks are intentionally small enough to hand to Codex one at a time.

Legend: **P0** required before public release, **P1** high value, **P2** later.

## Foundation

### VB-001 — Establish public project metadata — P0

**Goal:** normalize README/project naming around VaultBridge without changing runtime behaviour.

**Acceptance criteria**
- README title and description use VaultBridge.
- Existing deployment instructions remain valid.
- No private hostnames/secrets are introduced.

### VB-002 — Add typed configuration — P0

**Goal:** replace scattered `os.getenv()` calls with a tested settings object.

**Acceptance criteria**
- Invalid numeric settings fail with a useful startup error.
- Defaults match current behaviour.
- tests cover default and overridden settings.

### VB-003 — Split FastAPI routers from domain logic — P0

**Depends on:** VB-002

**Acceptance criteria**
- all existing routes/operation IDs unchanged,
- API tests pass unchanged,
- `main.py` contains mostly app construction/router registration.

### VB-004 — Extract VaultService — P0

**Acceptance criteria**
- safe path resolution, Markdown reads/writes and size checks live in one service,
- traversal tests remain green,
- service has unit tests independent of FastAPI.

### VB-005 — Extract semantic repository/service — P0

**Acceptance criteria**
- SQLite persistence is separated from ranking/embedding logic,
- fake embedder remains supported in tests,
- no semantic behaviour change.

## Indexing

### VB-010 — Add index state model — P0

States: `uninitialized`, `indexing`, `ready`, `error`.

### VB-011 — Batch index commits — P0

**Acceptance criteria**
- configurable batch size,
- progress survives a process interruption at batch boundaries,
- no single transaction covers the entire initial vault.

### VB-012 — Background startup indexing — P0

**Depends on:** VB-010, VB-011

### VB-013 — Enqueue reindex after note writes — P0

### VB-014 — Optional filesystem watcher — P1

Add debounced `watchdog`/inotify only after the background indexer exists.

### VB-015 — Rich health/readiness output — P0

Expose note/chunk counts, index state and last successful update.

## Retrieval quality

### VB-020 — Markdown heading-aware chunker — P0

**Acceptance criteria**
- chunk metadata includes heading hierarchy,
- fenced code is not arbitrarily split when avoidable,
- chunker unit tests cover headings, lists and long sections.

### VB-021 — Embed title + heading hierarchy + chunk — P0

Requires automatic index-signature invalidation.

### VB-022 — Retrieval evaluation fixture — P0

Create sanitized EN/SK/cross-language query cases.

### VB-023 — Retrieval benchmark command — P1

Output latency, returned paths and scores as JSON/Markdown.

### VB-024 — Tune hybrid ranking from evaluation data — P1

No arbitrary weight changes without before/after eval results.

## Knowledge operations

### VB-030 — Duplicate candidate service — P1

Return candidates only; no automatic merges.

### VB-031 — Verified related-note suggestions — P1

Only return actual vault paths.

### VB-032 — Section-level update design/ADR — P1

Design before implementation.

### VB-033 — `updateNoteSection` endpoint — P1

Must include conflict detection/content hash.

### VB-034 — Opt-in verified backlink insertion — P2

## Operations and security

### VB-040 — Structured JSON logging — P0

### VB-041 — Request IDs and latency logging — P0

### VB-042 — API key rotation — P1

Allow current + previous key during a rotation window.

### VB-043 — Lightweight rate limiting — P1

In-process by default; do not add Redis.

### VB-044 — Liveness and readiness endpoints — P0

### VB-045 — Index integrity/rebuild CLI — P0

## Developer experience / distribution

### VB-050 — Introduce `/api/v1` — P0

Requires migration/compatibility plan.

### VB-051 — Add VaultBridge CLI — P1

Commands: `status`, `index`, `reindex`, `search`, `related`.

### VB-052 — Generic Docker deployment docs — P0

### VB-053 — TrueNAS deployment docs — P0

### VB-054 — Publish GHCR image workflow — P0

### VB-055 — Multi-arch image — P1

### VB-056 — GitHub v1.0 release checklist — P0

## Suggested first Codex sequence

```text
VB-001 → VB-002 → VB-004 → VB-003 → VB-005
       → VB-010 → VB-011 → VB-012 → VB-015
       → VB-020 → VB-021 → VB-022 → VB-024
```
