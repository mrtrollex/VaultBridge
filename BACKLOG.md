# VaultBridge Backlog

Tasks are intentionally small enough to hand to Codex one at a time.

Legend:

- **P0** — required before public `v1.0.0`
- **P1** — high-value improvement
- **P2** — later / optional
- ✅ — completed
- ▶ — next recommended task

`BACKLOG.md` is the authoritative source for individual task scope. `ROADMAP.md` describes milestones and direction.

## Foundation

### VB-001 — Establish public project metadata — P0 ✅

**Status:** Completed.

**Goal:** normalize README/project naming around VaultBridge without changing runtime behaviour.

### VB-002 — Add typed configuration — P0 ✅

**Status:** Completed.

**Goal:** replace scattered environment reads with a tested typed settings object.

### VB-003 — Split FastAPI routers from domain logic — P0 ✅

**Status:** Completed.

**Depends on:** VB-002

### VB-004 — Extract VaultService — P0 ✅

**Status:** Completed.

### VB-005 — Extract semantic repository/service — P0 ✅

**Status:** Completed.

Semantic orchestration/ranking is separated from SQLite persistence. Existing index compatibility is preserved.

---

## Indexing

### VB-010 — Add index state model — P0 ✅

**Status:** Completed.

States:

- `uninitialized`
- `indexing`
- `ready`
- `error`

**Implemented behavior**

- SQLite `meta.index_state` is the persisted source of truth.
- `SemanticSearchService` owns transitions.
- `SemanticRepository` persists state only.
- compatible legacy index with chunks → `ready`
- no indexed chunks → `uninitialized`
- synchronization start → `indexing`
- synchronization success → `ready`
- synchronization failure → `error`
- interrupted persisted `indexing` after restart → `error`
- signature mismatch invalidation → `uninitialized`

### VB-011 — Batch index commits — P0 ▶

**Status:** Next recommended task.

**Goal:** make large initial/full synchronization durable in bounded batches instead of one large transaction.

**Depends on:** VB-010

**Acceptance criteria**

- configurable batch size,
- no single transaction covers the entire initial vault,
- completed batches survive interruption,
- interruption loses at most the active batch,
- incremental indexing semantics remain unchanged,
- ranking, embedding generation, chunking and result ordering remain unchanged,
- no background worker is introduced in this task,
- tests cover successful batches and interruption/retry behavior.

**Out of scope**

- background startup tasks,
- async indexing,
- filesystem watcher,
- rich health progress,
- ranking/chunking changes.

### VB-012 — Background startup indexing — P0

**Depends on:** VB-010, VB-011

**Goal:** move synchronization out of normal semantic-search request latency.

**Acceptance criteria**

- API can start without blocking on a complete vault synchronization,
- indexing lifecycle is application-managed,
- no Redis/Celery/task queue,
- shutdown behavior is deterministic,
- previous valid index may remain searchable during refresh where safe.

### VB-013 — Enqueue reindex after note writes — P0

**Depends on:** VB-012

**Goal:** successful note writes enqueue only affected notes for semantic refresh.

### VB-014 — Optional filesystem watcher — P1

**Depends on:** VB-012, VB-013

Add debounced `watchdog`/inotify only after background indexing exists.

### VB-015 — Rich health/readiness output — P0

**Depends on:** VB-010; progress fields also depend on VB-011/VB-012 as applicable.

Expose:

- index state,
- ready-for-search condition,
- note/chunk counts,
- progress counters,
- last successful update.

Preserve existing compatibility unless an explicit API-shape decision is made.

---

## Retrieval quality

### VB-020 — Markdown heading-aware chunker — P0

**Acceptance criteria**

- chunk metadata includes heading hierarchy,
- fenced code is not arbitrarily split when avoidable,
- tests cover headings, lists, code fences and long sections.

### VB-021 — Embed title + heading hierarchy + chunk — P0

**Depends on:** VB-020

Requires automatic index-signature invalidation.

### VB-022 — Retrieval evaluation fixture — P0

Create sanitized English, Slovak and cross-language query cases.

### VB-023 — Retrieval benchmark command — P1

Output latency, returned paths and scores as JSON and/or Markdown.

### VB-024 — Tune hybrid ranking from evaluation data — P1

**Depends on:** VB-022

No arbitrary weight changes without before/after evaluation results.

---

## Knowledge operations

### VB-030 — Duplicate candidate service — P1

Return candidates only; no automatic merges.

### VB-031 — Verified related-note suggestions — P1

Only return actual vault paths.

### VB-032 — Section-level update design/ADR — P1

Design before implementation.

### VB-033 — `updateNoteSection` endpoint — P1

**Depends on:** VB-032

Must include conflict detection/content hash.

### VB-034 — Opt-in verified backlink insertion — P2

**Depends on:** VB-031

---

## Operations and security

### VB-040 — Structured JSON logging — P0

### VB-041 — Request IDs and latency logging — P0

**Depends on:** VB-040

### VB-042 — API key rotation — P1

Allow current + previous key during a rotation window.

### VB-043 — Lightweight rate limiting — P1

In-process by default; do not add Redis.

### VB-044 — Liveness and readiness endpoints — P0

### VB-045 — Index integrity/rebuild CLI — P0

---

## Developer experience / distribution

### VB-050 — Introduce `/api/v1` — P0

Requires migration/compatibility plan.

### VB-051 — Add VaultBridge CLI — P1

Commands:

```text
status
index
reindex
search
related
```

### VB-052 — Generic Docker deployment docs — P0

### VB-053 — TrueNAS deployment docs — P0

### VB-054 — Publish GHCR image workflow — P0

### VB-055 — Multi-arch image — P1

### VB-056 — GitHub v1.0 release checklist — P0

---

## Recommended Codex sequence

```text
VB-001 ✓
→ VB-002 ✓
→ VB-004 ✓
→ VB-003 ✓
→ VB-005 ✓
→ VB-010 ✓
→ VB-011  NEXT
→ VB-012
→ VB-013
→ VB-015
→ VB-020
→ VB-021
→ VB-022
→ VB-024
```

Do not infer scope from sequence alone. Always read the exact task definition before implementation.
