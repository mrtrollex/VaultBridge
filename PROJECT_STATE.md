# Current Project State

This document is the current factual snapshot for future Codex sessions. It should describe what exists **now**, not future plans.

## Baseline date

2026-08-23

## Current development position

Completed:

- VB-001 — Project identity
- VB-002 — Typed configuration
- VB-004 — VaultService
- VB-003 — FastAPI routers
- VB-005 — Semantic service/repository split
- VB-010 — Semantic index state model
- VB-011 — Batch index commits
- VB-012 — Background startup indexing
- VB-013 — Enqueue reindex after note writes
- VB-015 — Rich health/readiness output

Next recommended task:

- **VB-020 — Markdown heading-aware chunker**

Current milestone:

- **Milestone 3 — Retrieval quality and evaluation**

## Working production characteristics

- FastAPI application
- Python 3.12 container
- FastAPI routes, vault operations, semantic orchestration and SQLite persistence have separate modules
- typed runtime settings via `app/core/config.py`
- local semantic model through FastEmbed / ONNX Runtime
- default model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- SQLite semantic index using WAL
- chunk configuration currently defaults to 600 chars / 100 overlap
- semantic search uses cosine similarity plus lexical/title/path reranking
- semantic index lifecycle state is persisted separately from schema availability
- semantic index states: `uninitialized`, `indexing`, `ready`, `error`
- semantic index mutations commit in configurable note-count batches (default `25`)
- `SemanticSearchService` owns state transitions
- `SemanticRepository` persists semantic data and lifecycle state
- `/health` reports semantic lifecycle and search availability separately without starting semantic work
- successful full synchronization persists `last_successful_sync`; targeted refresh does not change it
- TrueNAS container commonly runs as UID/GID 568
- existing production deployment uses port 8765 → 8000
- separate `/vault` and `/data` mounts are used by the TrueNAS compose file

## Current implementation boundaries

```text
app/main.py
    application construction, dependency wiring, router registration and lifespan management

app/api/
    HTTP routers and API dependencies

app/core/config.py
    typed runtime configuration

app/services/vault.py
    safe vault-relative path resolution, Markdown operations and contained note counting

app/services/semantic_search.py
    embedding, batched synchronization orchestration, ranking, lifecycle transitions and health state

app/services/indexer.py
    single-process background synchronization ownership and cooperative shutdown

app/repositories/semantic.py
    SQLite semantic persistence, lifecycle-state storage and read-only status statistics

app/semantic.py
    compatibility facade retained for pre-VB-005 internal API compatibility
```

## Semantic index lifecycle

Persisted states:

```text
uninitialized
indexing
ready
error
```

Deterministic behavior:

- missing database/schema → `uninitialized`
- synchronization starts → `indexing`
- successful synchronization, including empty vault → `ready`
- synchronization failure → `error`
- later successful retry → `ready`
- compatible legacy index with chunks → inferred/persisted `ready`
- compatible legacy index without chunks → `uninitialized`
- signature mismatch → existing invalidation, then `uninitialized`
- persisted `indexing` after restart → converted to `error`
- each completed synchronization batch is durable; interruption rolls back at most the active batch
- retry reuses already committed batches through incremental change detection
- application startup schedules synchronization in the background without waiting for completion
- one application process runs at most one synchronization job at a time
- successful API note creates/appends enqueue their affected vault-relative path for targeted refresh
- unchanged/idempotent/failed writes do not enqueue semantic work
- enqueue/submission failure after a committed write does not fail or repeat the Markdown mutation
- duplicate pending paths are coalesced; a write during active processing requests one follow-up evaluation after success or failure
- full synchronization and targeted refresh share the VB-012 worker and never run concurrently
- paths queued before a full synchronization are covered by that scan; writes during it run as a targeted follow-up
- failed/cancelled full synchronization retains process-local recovery debt; a write queued before or during that failure schedules one prioritized full retry before `ready` can be restored
- a failed follow-up with no newer work retains paths/debt for later recovery without an immediate retry loop
- targeted refresh commits in the same configurable note-count batches as full synchronization
- failed targeted batches, including unavailable/unreadable/non-UTF-8 paths, keep the previous committed index, persist `error`, and retain their paths for retry
- full synchronization resolves discovered Markdown candidates and never indexes a symlink target outside the resolved vault root
- first-time semantic search returns no results until the index reaches `ready`
- failed initial indexing with no valid index makes semantic search unavailable with HTTP `503`
- a previously ready committed index remains searchable while a background refresh is `indexing`
- a previously ready committed index remains searchable after a failed compatible refresh
- synchronization failure persists `error`; the next startup or an explicit manager retry can try again
- calls into one embedder instance are serialized while the surrounding pipelines remain concurrent
- shutdown requests cooperative cancellation and stops between batches after the active transaction finishes
- shutdown drops unprocessed in-memory paths; durable Markdown is recovered by the next startup full synchronization
- shutdown still waits if execution is blocked inside model download/inference or filesystem I/O

`semantic_index_ready` remains derived from `state == ready`. `/health` also reports
`semantic_search_available` separately, so refresh-time `indexing`/`error` can remain searchable
when a compatible completed index exists. It exposes process-local indexer activity and full-sync
required/recovery debt, SQLite indexed-note/chunk counts, a semantic-eligible vault-note count,
and the time of the last successful full synchronization. Health reads do not initialize the
embedder, trigger synchronization/search, or mutate lifecycle state.

`vault_notes` means Markdown notes eligible for full semantic synchronization under the same
containment, internal-directory exclusion and maximum-size policy. Health metadata and SQLite counts
come from one short read snapshot. These fields provide inferred completeness, not explicit per-sync
current-note, percentage, batch or ETA counters.

## Known limitations observed during real use

1. External filesystem changes are not watched; they are picked up by startup/full synchronization.
2. Current chunking is still primarily character-based rather than fully Markdown-aware.
3. Default ranking thresholds require evaluation rather than ad-hoc tuning.
4. Multiple application processes sharing one index are not coordinated.
5. GPT/AI clients can invent wikilinks unless client instructions require verified existing notes.
6. Graceful shutdown cannot interrupt a model download, ONNX inference call, or filesystem operation already in progress.

## Verified baseline after VB-015

Native Windows:

```text
105 passed, 1 failed, 4 skipped
```

The one failure remains the known pre-existing Windows path-separator assertion around
`VaultService` response paths. The four skips are privilege-dependent symlink tests. The latest
previous Linux compatibility baseline remains `89 passed` after VB-013; the current WSL environment
does not have the project dependencies installed, so VB-015 was not re-run there.

Additional checks:

```text
Ruff: passed
Python compileall: passed
git diff --check: passed
all 7 endpoint paths and operation IDs: unchanged
```

Focused health/indexer/semantic/repository/vault/API/config run:

```text
105 passed, 4 skipped, 1 known baseline test deselected
```

Docker checks were not required because no Docker-related files changed.

## Compatibility contract

Do not rename or silently change:

- existing REST endpoint paths,
- existing operation IDs,
- authentication behavior,
- environment variable names,
- TrueNAS production paths,
- legacy Docker/container identifiers,

unless the relevant backlog task explicitly introduces and documents a migration.

Existing compatible `semantic-index.sqlite3` databases must remain usable unless a task explicitly requires safe invalidation/rebuild.

## Source of truth

Markdown files in the configured Obsidian vault are authoritative.

The SQLite semantic index is derived/disposable data and must always be rebuildable from the vault.
