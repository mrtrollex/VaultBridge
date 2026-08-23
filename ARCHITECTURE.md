# VaultBridge Architecture

## Context

VaultBridge exposes a deliberately small API over an Obsidian Markdown vault. It supports ordinary note operations and local semantic retrieval without turning the vault into a proprietary database.

## Current architecture

```text
Client (ChatGPT / curl / future integrations)
                    |
                    | HTTPS + Bearer token
                    v
               FastAPI app
              /           \
             /             \
    Vault operations     Semantic search
           |                  |
           v                  v
    Markdown files       FastEmbed / ONNX
                              |
                              v
                         SQLite index
```

### Source of truth

The `.md` files are authoritative. The SQLite semantic index is disposable derived data and must be rebuildable.

### Current implementation modules

```text
app/main.py          application construction, dependency wiring, router registration
app/api/             health, note and search routers plus HTTP dependencies
app/core/config.py   typed environment configuration
app/services/vault.py safe path resolution, Markdown note operations and contained note counting
app/services/semantic_search.py embedding, incremental indexing, hybrid ranking and semantic health state
app/services/indexer.py one in-process full/targeted synchronization worker and deduplicating path queue
app/repositories/semantic.py SQLite schema, semantic index persistence and read-only status statistics
app/semantic.py      compatibility facade for the pre-VB-005 internal API
```

FastAPI lifespan submits semantic synchronization to one in-process background worker, so startup
does not wait for a complete vault scan. The synchronization operation remains synchronous inside
that worker and commits configurable note-count batches, preserving durable completed batches after
interruption. Lifespan shutdown signals cooperative cancellation, lets the active batch commit or
roll back normally, and skips remaining batches. Shutdown must still wait for an already-running
model download, ONNX inference call, or filesystem operation because those calls are not forcibly
interruptible.

Successful API create/append mutations enqueue their vault-relative path in the same worker. Pending
paths are stored in a process-local set, so repeated writes before processing coalesce. A full
synchronization takes priority and absorbs paths already queued when it starts; writes arriving while
that scan is running remain queued for one follow-up evaluation after either success or failure. New
work arriving during any active job records one follow-up request; the request is consumed when that
attempt starts, so another failure without newer work retains the paths/debt without hot-looping. The
single worker and service sync lock prevent full and targeted jobs from running concurrently. The
set has no configured capacity;
it holds at most one entry per distinct outstanding path and drains as work is claimed. An executor
submission failure leaves already-added paths pending for a later enqueue or startup recovery.

The worker records process-local full-synchronization recovery debt from the moment a full job is
claimed until that job succeeds. A failed full job keeps that debt, and the next write-triggered job
runs a full retry before any targeted refresh can transition lifecycle state back to `ready`. This
includes a write that arrived while the failed full job was still running.

Targeted refresh uses the same per-note indexing logic, lifecycle transitions, embedder lock and
configurable VB-011 batch transactions as full synchronization, but it does not scan or remove
unrelated notes. A failed targeted batch rolls back without replacing that batch's prior valid note
index. Missing, inaccessible, path-escaping, oversized, and non-UTF-8 targeted notes fail strictly;
failed paths remain queued until a later enqueue or explicit/full retry. Full-vault synchronization
retains its existing tolerant file-discovery/read behavior, but resolves every discovered candidate
and rejects paths outside the resolved vault root before reading or indexing them. Shutdown discards
unprocessed in-memory paths after requesting cooperative cancellation. Markdown remains durable, so
the next startup full synchronization recovers any work not processed before exit.

Enqueueing happens only after the Markdown mutation commits. Enqueue/submission failure cannot turn
that durable mutation into an HTTP failure or repeat it; Markdown remains authoritative and startup
full synchronization provides the recovery boundary.

Before the first successful synchronization, semantic searches return no results rather than
waiting for indexing. During a later refresh, the previously ready committed SQLite index remains
searchable. SQLite WAL mode permits those reads while synchronization commits replacement batches;
uncommitted batch data is never exposed.

Lifecycle state and search availability are separate: `indexing` can coexist with an older valid
searchable index, and `error` remains searchable only when the process already established that a
compatible completed index existed. Failed initial builds, including their durable partial batches,
are not searchable and semantic requests return HTTP `503`.

`/health` composes facts from their existing owners without starting semantic work. The semantic
service derives lifecycle and search availability, the repository supplies metadata plus note/chunk
counts through one read-only SQLite connection, the background indexer supplies process-local running
and full-sync-required state, and the vault service counts Markdown files accepted by the same
containment, exclusion and size policy as full semantic synchronization without reading note
contents. The SQLite status connection uses one explicit short read transaction so lifecycle
metadata and counts come from one committed WAL snapshot. `last_successful_sync` is stored in the
existing metadata table only after a successful full synchronization; targeted refresh does not
update it.

Search and synchronization share one embedder instance. A narrow execution lock serializes only
calls into that embedder; vault scanning, SQLite access, ranking, and response construction remain
concurrent.

Semantic index lifecycle state is persisted in the SQLite `meta` table as
`uninitialized`, `indexing`, `ready`, or `error`. `SemanticSearchService` owns
the transitions; `SemanticRepository` only stores the value and index data.
Schema availability and search readiness are separate conditions.

---

## Target architecture

```text
                         +--------------------+
                         |   API clients      |
                         | ChatGPT / CLI / UI |
                         +----------+---------+
                                    |
                                    v
                         +--------------------+
                         | FastAPI / api/v1   |
                         +----+----------+----+
                              |          |
                    +---------+          +----------+
                    v                               v
             +-------------+                +---------------+
             | VaultService |                | SearchService |
             +------+------+                +-------+-------+
                    |                               |
                    v                               v
             Obsidian Markdown               SemanticIndexer
                                                    |
                                      +-------------+-------------+
                                      v                           v
                                Embedder (ONNX)             SQLite repo
```

## Proposed module boundaries

### `core/config.py`

Typed environment configuration. No HTTP or vault logic.

### `core/security.py`

Bearer token verification and future key rotation logic.

### `services/vault.py`

- safe path resolution
- Markdown read/write/append
- note enumeration
- content size validation

### `services/indexer.py`

- one in-process background synchronization worker
- duplicate full-job prevention and coalesced vault-relative targeted paths within one application process
- background failure capture and explicit retry entry point
- cooperative cancellation at synchronization batch boundaries
- shutdown waiting for any already-running uninterruptible third-party call

Batching, lifecycle-state transitions and index contents remain owned by
`SemanticSearchService` and `SemanticRepository`. Filesystem watching remains planned work.

### `services/semantic_search.py`

- query embedding
- candidate scoring
- hybrid reranking
- result aggregation
- full-vault and targeted-path synchronization orchestration

### `repositories/semantic.py`

All SQLite persistence for semantic metadata/chunks/index state.

### `schemas/`

Pydantic HTTP request/response models only.

### `api/`

FastAPI routers and dependencies. Routes should orchestrate services rather than implement domain logic.

---

## Data model

The semantic store currently needs these concepts:

### Note

- path
- modified metadata
- content hash
- indexed timestamp

### Chunk

- note path
- chunk order
- heading context
- content
- normalized embedding
- dimensions/model/index signature

### Index metadata

- schema/index version
- embedding model
- chunking configuration
- persisted lifecycle state (`uninitialized`, `indexing`, `ready`, `error`)
- explicit per-sync progress such as current note, percentage, batch and ETA (planned)
- last successful full synchronization

The index is **derived data**. Migrations should be used when cheap; otherwise a safe automatic rebuild is acceptable.

---

## Request flows

### Literal search

```text
request → auth → validate folder → scan/search Markdown → response
```

A future optimization may add a lexical index, but only if measured vault sizes justify it.

### Semantic search — target

```text
request
  ↓
auth
  ↓
embed query
  ↓
read current valid semantic index
  ↓
semantic candidates
  ↓
hybrid reranking
  ↓
best result per note
  ↓
response

Background index maintenance happens independently.
```

### Note write

```text
request → auth → safe path → validate size → write Markdown → enqueue note for re-index
```

Writes should not synchronously rebuild unrelated notes.

---

## Security boundaries

VaultBridge is allowed to access one configured vault root and its own data directory.

It must not expose:

- arbitrary absolute paths
- shell commands
- the Docker socket
- general directory download
- unauthenticated writes

The public reverse proxy/tunnel terminates HTTPS. VaultBridge still performs application authentication.

---

## Scaling assumptions

Default target: personal knowledge bases with thousands to tens of thousands of chunks on CPU-only hardware.

SQLite + brute-force vector comparison is acceptable until benchmarks show otherwise. If scale becomes a problem, optimize in this order:

1. reduce unnecessary synchronization work,
2. batch embeddings,
3. optimize SQLite reads/memory layout,
4. add an in-process ANN/vector extension,
5. only then consider an external vector service.
