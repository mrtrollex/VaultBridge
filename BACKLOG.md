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

### VB-011 — Batch index commits — P0 ✅

**Status:** Completed.

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

**Implemented behavior**

- `SEMANTIC_INDEX_BATCH_SIZE` configures the maximum number of notes mutated per transaction (default `25`).
- changed/new notes and stale-note removals commit in bounded batches.
- completed batches remain durable after a later batch fails; the active batch rolls back.
- retry reuses completed batches through the existing incremental synchronization logic.
- synchronization remains synchronous and preserves existing lifecycle, ranking, embedding, chunking, and API behavior.

**Out of scope**

- background startup tasks,
- async indexing,
- filesystem watcher,
- rich health progress,
- ranking/chunking changes.

### VB-012 — Background startup indexing — P0 ✅

**Status:** Completed on 2026-08-23.

**Depends on:** VB-010, VB-011

**Goal:** move synchronization out of normal semantic-search request latency.

**Acceptance criteria**

- API can start without blocking on a complete vault synchronization,
- indexing lifecycle is application-managed,
- no Redis/Celery/task queue,
- shutdown behavior is deterministic,
- previous valid index may remain searchable during refresh where safe.

**Implemented behavior**

- FastAPI lifespan starts one in-process background synchronization job without waiting for it to finish,
- concurrent job submission is rejected while synchronization is already running,
- initial semantic searches return no results until the first index reaches `ready`,
- an initial synchronization failure with no valid index makes semantic search return HTTP `503`,
- a previously ready committed index remains searchable while a refresh is `indexing`,
- a previously ready committed index also remains searchable after a failed compatible refresh,
- synchronization failures persist `error`; a later application startup or explicit manager retry can run synchronization again,
- embedder calls are serialized without locking the surrounding search/synchronization pipelines,
- shutdown requests cooperative cancellation, finishes the active batch normally, and skips remaining batches,
- shutdown can still wait for an active uninterruptible model or filesystem call to return.

### VB-013 — Enqueue reindex after note writes — P0 ✅

**Status:** Completed on 2026-08-23.

**Depends on:** VB-012

**Goal:** successful note writes enqueue only affected notes for semantic refresh.

**Implemented behavior**

- successful `createNote` and `appendNote` mutations enqueue their vault-relative note path,
- unchanged creates, already-applied appends, and failed writes do not enqueue work,
- enqueue/submission failure after a committed write does not fail or repeat the HTTP mutation,
- one process-local, uncapped path set coalesces duplicate pending notes to one entry per path,
- the VB-012 worker serializes full and targeted synchronization; no request embeds inline,
- writes arriving during active targeted work or a full synchronization remain queued for a targeted follow-up,
- a failed/cancelled full synchronization retains recovery debt, so later queued writes trigger a full retry before targeted work can restore `ready`,
- targeted refresh uses VB-011 note-count batches and preserves the previous committed note index if its active transaction fails,
- unavailable, unreadable, non-UTF-8, oversized, excluded, and escaping targeted paths fail and remain retryable rather than being silently skipped,
- failed targeted paths remain pending for a later write-triggered retry or full synchronization,
- shutdown discards the in-memory queue after requesting cancellation; the next startup full synchronization recovers from Markdown source files.

### VB-014 — Optional filesystem watcher — P1

**Depends on:** VB-012, VB-013

Add debounced `watchdog`/inotify only after background indexing exists.

### VB-015 — Rich health/readiness output — P0 ✅

**Status:** Completed on 2026-08-23.

**Depends on:** VB-010; progress fields also depend on VB-011/VB-012 as applicable.

Expose:

- index state,
- ready-for-search condition,
- note/chunk counts,
- progress counters,
- last successful update.

Preserve existing compatibility unless an explicit API-shape decision is made.

**Implemented behavior**

- `/health` preserves `ok`, `vault_exists`, `semantic_index_ready`, and operation ID `healthCheck`,
- lifecycle state and semantic-search availability are reported separately,
- process-local indexer activity and full-sync-required/recovery-debt state are exposed,
- indexed-note/chunk counts use one coherent read-only SQLite snapshot,
- vault-note count uses the same containment, exclusion and size policy as full synchronization
  without reading note contents,
- `last_successful_sync` is persisted in existing metadata only after a successful full synchronization,
- health does not initialize the model, synchronize, search, embed, or mutate lifecycle state.

The counts provide inferred completeness and operator context. VB-015 does not add explicit
per-sync counters such as current note, percentage complete, current batch or ETA.

---

## Retrieval quality

### VB-020 — Markdown heading-aware chunker — P0 ✅

**Status:** Completed on 2026-08-23.

**Acceptance criteria**

- chunk metadata includes heading hierarchy,
- fenced code is not arbitrarily split when avoidable,
- tests cover headings, lists, code fences and long sections.

**Implemented behavior**

- ATX headings outside fenced code divide notes into semantic sections,
- chunk metadata retains the complete available heading hierarchy,
- sections remain bounded by the configured character policy and oversized sections split
  deterministically at source-preserving line/word/character boundaries,
- adjacent undersized sections coalesce into bounded chunks with truthful first-to-last hierarchy
  metadata rather than producing one tiny embedding per heading,
- hierarchy metadata is bounded leaf-first so the current heading cannot be hidden by long ancestors,
- overlap reuses exact source slices and is limited to split oversized prose rather than crossing
  section, list or code boundaries,
- fenced code remains intact whenever the complete fence fits in one configured chunk,
- notes without headings, nested/heading-only sections, Unicode text, lists and empty notes have
  deterministic fallback behavior,
- the `v2-heading-aware` index signature invalidates old chunk data and triggers an automatic rebuild
  from Markdown without changing the SQLite schema.

### VB-021 — Embed title + heading hierarchy + chunk — P0 ✅

**Status:** Completed on 2026-08-23.

**Depends on:** VB-020

**Implemented behavior**

- headingless embedding input remains `title + chunk content`,
- headed embedding input adds the canonical VB-020 heading metadata between title and content,
- a matching plain or ATX heading already at the start of a chunk is not prepended again,
- embedding input is built separately from the source-preserving chunk persisted in SQLite,
- full synchronization and targeted refresh use the same deterministic embedding-input builder,
- query embedding and hybrid ranking behavior remain unchanged,
- the `v3-heading-context` signature invalidates VB-020 embeddings and triggers an automatic full
  rebuild from Markdown without a SQLite schema migration,
- targeted refresh against an older signature falls back to that safe full rebuild rather than
  mixing embedding generations.

### VB-022 — Retrieval evaluation fixture — P0 ✅

**Status:** Completed on 2026-08-23.

**Implemented behavior**

- nine sanitized Markdown notes exercise ambiguous backup, replication, storage, deployment,
  authentication, Oracle/APEX REST and unrelated-media/garden vocabulary,
- thirteen structured English, Slovak and cross-language cases define expected path, optional
  heading, accepted top-k rank and selected top-1 confusion exclusions,
- a deterministic concept-vector embedder replaces only FastEmbed while production chunking,
  embedding input, SQLite indexing, scoring, filtering, aggregation and ordering remain active,
- the runner reports per-case ranks with useful failure diagnostics and calculates Hit@1, Hit@3
  and mean reciprocal rank for all, English, Slovak, cross-language and heading-context groups,
- a checked `baseline.json`, documentation-table verification, material-tie guard and reversed-order
  run keep ranks and metrics reproducible,
- controlled test variants prove that structural cases depend on VB-021 hierarchy context and that
  the English-to-Slovak case depends on multilingual concept equivalence,
- the measured baseline is Hit@1 `13/13` (100%), Hit@3 `13/13` (100%) and MRR `13/13` (100%).

### VB-023 — Retrieval benchmark command — P1

Output latency, returned paths and scores as JSON and/or Markdown.

### VB-024 — Tune hybrid ranking from evaluation data — P1 ✅

**Status:** Completed on 2026-08-24.

**Depends on:** VB-022

No arbitrary weight changes without before/after evaluation results.

**Implemented behavior**

- the VB-022 baseline remains 13/13 Hit@1, 13/13 Hit@3 and 100% MRR before and after,
- the existing semantic-to-lexical ratio remains `1.0:0.70`; no boost or threshold changed,
- hybrid scores are normalized by the `1.70` total weight instead of clamped, preserving existing
  non-saturated ordering while preventing distinct high-scoring candidates from collapsing to `1.0`,
- equal note scores sort by semantic score, lexical score and canonical path, in that order,
- equal chunks within one note use the same relevance signals and then lower source chunk index,
- reversed repository/chunk iteration produces identical paths and selected chunks,
- controlled ablations prove semantic, lexical, heading-context and multilingual sensitivity,
- no API, schema, embedding, chunking, index-signature or dependency change was introduced.

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

### VB-040 — Structured JSON logging — P0 ✅

Completed:

- VaultBridge application records use standard-library logging with one UTF-8 JSON object per line;
- the stable core fields are `timestamp`, `level`, `logger`, `event`, and `message`;
- startup/shutdown, full synchronization, targeted refresh, and committed note writes expose stable events with safe operation context;
- exception records retain type and basename-only stack frames without exception text, vault content, query text, credentials, or absolute host paths;
- configuration is idempotent, emits to stderr for container collection, and deliberately leaves Uvicorn/FastAPI logging unchanged;
- logging failures cannot change note-write, indexing, lifecycle, or API behavior.

### VB-041 — Request IDs and latency logging — P0 ✅

**Depends on:** VB-040

Completed:

- every HTTP request receives one internally generated 32-character UUID hex request ID;
- the same ID is returned in `X-Request-ID` and added automatically to VaultBridge application logs
  emitted in the request context;
- `request_started`, `request_completed`, and `request_failed` expose safe method, route-template,
  observed response status when available, and monotonic `duration_ms` metadata without bodies, raw
  query strings, credentials, or exception messages;
- caller-provided request IDs are not accepted; incoming `X-Request-ID` values are ignored and
  replaced with the server-generated ID;
- context-local propagation isolates concurrent requests and is restored after completion;
- synchronous targeted-reindex scheduling retains request correlation, while later executor worker
  execution does not inherit stale HTTP context;
- logging failures remain isolated from response and exception behavior, and Uvicorn logging remains
  separately managed.

### VB-042 — API key rotation — P1

Allow current + previous key during a rotation window.

### VB-043 — Lightweight rate limiting — P1

In-process by default; do not add Redis.

### VB-044 — Liveness and readiness endpoints — P0 ✅

**Status:** Completed on 2026-08-24.

Added public, minimal orchestration probes while preserving the richer compatibility endpoint:

- `GET /health/live` (`livenessCheck`) returns HTTP `200` with `{"ok": true}` and does not
  consult the vault, semantic model, index, storage, or background indexer,
- `GET /health/ready` (`readinessCheck`) returns `{"ready": true}` with HTTP `200` only when
  the vault is an inspectable directory and semantic search is available; otherwise it returns the
  same minimal shape with HTTP `503`,
- readiness uses a read-only SQLite schema/signature/state plus chunk-existence snapshot without
  vault scans or note/chunk counts and does not initialize storage or lifecycle state,
- compatible legacy indexes with chunks but no persisted state are available without mutation, and
  a compatible previously searchable index remains ready during an active or failed refresh,
  while missing, corrupt, incompatible, uninitialized, initially indexing, or initially failed
  storage remains unavailable,
- expected vault/semantic filesystem and SQLite availability failures return not-ready rather than
  an application error, while unexpected programming errors retain normal HTTP `500` behavior,
- both probes use the existing request-ID header and standard request lifecycle events without
  feature-specific logging.

### VB-045 — Index integrity/rebuild CLI — P0 ✓

- `python -m app.cli index check` performs a cheap, filesystem-immutable stopped-service vault/index
  integrity check without constructing FastEmbed, scanning note contents, changing semantic storage,
  or exposing paths; it refuses inspection when SQLite WAL/SHM sidecars exist.
- check output distinguishes missing/unreadable/schema/signature/lifecycle conditions and reuses the
  persisted interpretation for compatible legacy indexes, standalone searchability, physical counts,
  and stored last-success timestamp; `/health` remains authoritative for live-process availability.
- `python -m app.cli index rebuild` validates the vault, atomically invalidates only derived semantic
  rows/metadata, and runs the production batched full synchronization with the current signature.
- exit codes are `0` for healthy/success, `1` for an integrity/readiness or operational rebuild
  problem, and `2` for CLI/configuration/programming failure.
- check and rebuild are offline administrative operations because VaultBridge has no cross-process
  index lock; the application must be stopped first.

---

## Developer experience / distribution

### VB-050 — Introduce `/api/v1` — P0 ✅

**Status:** Completed on 2026-08-24.

The protected note/search application API is available under `/api/v1` with explicit `*V1`
operation IDs. The original unversioned paths and operation IDs remain compatibility aliases for
existing clients, including the current ChatGPT Action. Both path families register the same
endpoint functions, dependencies, models, services, error handling, and request observability.

Operational `/health`, `/health/live`, and `/health/ready` routes remain public and unversioned.
The schema-hidden `/privacy` endpoint also remains unversioned. Runtime OpenAPI/docs endpoints remain
disabled. Contract tests cover methods, schemas, auth, success/validation/failure parity, unique
operation IDs, shared endpoint ownership, exactly-once domain calls, and versioned route-template
logging.

### VB-051 — Add VaultBridge CLI — P1

Commands:

```text
status
index
reindex
search
related
```

### VB-052 — Generic Docker deployment docs — P0 ✅

**Status:** Completed on 2026-08-25.

`README.md` now provides a linear source-build Docker Compose workflow for a normal host: prerequisites,
safe API-key creation, host vault and UID/GID mapping, generic semantic-data persistence, asynchronous
first-start behavior, public health probes, an authenticated `/api/v1` smoke test, structured logs,
updates, stopping/removal semantics, offline index maintenance, and loopback-only exposure guidance.
The generic `/vault/.obsidian-chatgpt-data` layout remains separate from the TrueNAS `/data` layout.
Runtime, Compose, Dockerfile, API, index, and TrueNAS compatibility behavior are unchanged.

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
→ VB-011 ✓
→ VB-012 ✓
→ VB-013 ✓
→ VB-015 ✓
→ VB-020  ✓
→ VB-021  ✓
→ VB-022  ✓
→ VB-024  ✓
→ VB-040  ✓
→ VB-041  ✓
→ VB-044  ✓
→ VB-045  ✓
→ VB-050  ✓
→ VB-042  NEXT
```

The next incomplete P0 task is VB-053 — TrueNAS deployment docs. Do not infer scope from priority;
read its exact task definition before implementation.

Do not infer scope from sequence alone. Always read the exact task definition before implementation.
