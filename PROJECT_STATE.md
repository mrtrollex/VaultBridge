# Current Project State

This document is the current factual snapshot for future Codex sessions. It should describe what exists **now**, not future plans.

## Baseline date

2026-08-24

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
- VB-020 — Markdown heading-aware chunker
- VB-021 — Embed title + heading hierarchy + chunk
- VB-022 — Retrieval evaluation fixture
- VB-024 — Tune hybrid ranking from evaluation data
- VB-040 — Structured JSON logging
- VB-041 — Request IDs and latency logging
- VB-044 — Liveness and readiness endpoints
- VB-045 — Index integrity/rebuild CLI

Next recommended task:

- **VB-042 — API key rotation**

Current milestone:

- **Milestone 5 — Operational maturity and security (active)**

## Working production characteristics

- FastAPI application
- Python 3.12 container
- FastAPI routes, vault operations, semantic orchestration and SQLite persistence have separate modules
- typed runtime settings via `app/core/config.py`
- standard-library JSON application logging via `app/core/logging.py`
- context-local HTTP request correlation and latency events via `app/core/observability.py`
- local semantic model through FastEmbed / ONNX Runtime
- default model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- SQLite semantic index using WAL
- heading-aware chunk configuration defaults to 600 chars / 100 prose-only overlap
- chunk embedding input deterministically carries note title and canonical heading context where needed
- semantic search uses normalized cosine-plus-lexical reranking with deterministic tie-breaking
- semantic index lifecycle state is persisted separately from schema availability
- semantic index states: `uninitialized`, `indexing`, `ready`, `error`
- semantic index mutations commit in configurable note-count batches (default `25`)
- `SemanticSearchService` owns state transitions
- `SemanticRepository` persists semantic data and lifecycle state
- `/health` reports semantic lifecycle and search availability separately without starting semantic work
- `/health/live` provides dependency-free liveness and `/health/ready` reports usable-vault plus semantic-search availability
- standard-library `index check` and offline `index rebuild` administrative CLI commands
- successful full synchronization persists `last_successful_sync`; targeted refresh does not change it
- TrueNAS container commonly runs as UID/GID 568
- existing production deployment uses port 8765 → 8000
- separate `/vault` and `/data` mounts are used by the TrueNAS compose file

## Current implementation boundaries

```text
app/main.py
    application construction, dependency wiring, router registration and lifespan management

app/cli.py
    read-only semantic integrity inspection and explicit offline rebuild orchestration

app/api/
    HTTP routers and API dependencies

app/core/config.py
    typed runtime configuration

app/core/logging.py
    safe JSON formatter and idempotent VaultBridge logger configuration

app/core/observability.py
    outer ASGI request-ID propagation, response correlation and monotonic lifecycle timing

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

## Application logging

VaultBridge-owned application logs are emitted to stderr as one UTF-8 JSON object per line. Every
record has `timestamp`, `level`, `logger`, `event`, and `message`; optional allowlisted context is
included only when meaningful. UTC timestamps use millisecond ISO-8601 `Z` notation. Stable events
cover application startup/shutdown, full semantic synchronization, targeted reindex scheduling and
outcomes, and committed note create/append operations.

Successful full-sync scheduling and targeted queue events are emitted from their lock-protected
state snapshot before the worker can enter the corresponding observable synchronization path.

Logging call sites pass only safe metadata. Vault-relative note paths are normalized to `/`; absolute
and traversal paths are omitted. Exception details contain the exception type and basename-only stack
frames, not exception messages. Note content, embedding/query text, credentials, headers, environment
secrets, and absolute host paths are not logged. Logging infrastructure failures are isolated from
application behavior.

Every HTTP request receives an internally generated UUID represented as 32 lowercase hexadecimal
characters. The same value is returned in `X-Request-ID` and automatically enriches VaultBridge logs
emitted while FastAPI handles that request, including synchronous endpoint work run in a worker
thread. Incoming `X-Request-ID` values are ignored and replaced, so caller data is neither trusted
nor reflected.

`request_started` is followed by exactly one terminal `request_completed` for handled responses,
including authentication, validation, and application HTTP errors, or `request_failed` when an
exception escapes FastAPI. Terminal records contain only the HTTP method, matched route template,
an ASGI-observed response status when one exists, and non-negative `duration_ms` measured with
`time.perf_counter()`. Cancellation or another abort before `http.response.start` omits
`status_code`. Raw request paths, query strings, headers, bodies and responses are not logged.

The request `ContextVar` is restored at ASGI exit, isolating concurrent and later requests.
Synchronous note-write and targeted-queue events inherit the request ID. Later semantic execution on
the indexer's executor does not inherit it; VB-041 does not redesign queue payloads for causal
tracing. Uvicorn access/server logs retain their existing framework format and remain outside the
VaultBridge JSON event contract.

## Semantic index lifecycle

Persisted states:

```text
uninitialized
indexing
ready
error
```

Administrative behavior:

- `python -m app.cli index check` reads vault availability plus a filesystem-immutable SQLite status
  snapshot without constructing FastEmbed or mutating any semantic-storage file; it is a stopped-service
  persisted view and refuses WAL/SHM sidecars;
- it distinguishes missing storage, unreadable/corrupt storage, unusable schema, incompatible or
  missing signature, uninitialized/indexing/error states, and compatible legacy ready indexes;
- `python -m app.cli index rebuild` validates the vault, clears only derived semantic state in one
  transaction, and runs the production full synchronization with current chunking, embedding input,
  signature and batch durability;
- full-sync success commits the new `last_successful_sync` and `ready` state atomically; failure leaves
  Markdown untouched, persists `error` when storage permits, retains completed new batches, and
  preserves the previous successful-sync timestamp;
- exit `0` means healthy/success, `1` means an integrity/readiness or operational rebuild problem,
  and `2` means CLI/configuration/programming failure;
- check reports persisted standalone searchability and physical stored counts; `/health` and
  `/health/ready` remain authoritative for live availability. Both CLI commands require the server to
  be stopped because no cross-process index lock exists.

Deterministic behavior:

- missing database/schema → `uninitialized`
- synchronization starts → `indexing`
- successful synchronization, including empty vault → `ready`
- synchronization failure → `error`
- later successful retry → `ready`
- compatible legacy index with chunks → inferred/persisted `ready`
- compatible legacy index without chunks → `uninitialized`
- signature mismatch → existing invalidation, then `uninitialized`
- the `v3-heading-context` signature invalidates VB-020 embeddings and automatically rebuilds all
  derived notes/chunks from Markdown without a schema migration
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

`GET /health/live` is a constant, public liveness check. It returns HTTP `200` and `{"ok": true}`
without consulting runtime dependencies. `GET /health/ready` is a public orchestration readiness
check: it returns HTTP `200` with `{"ready": true}` only when the vault is an inspectable directory
and semantic search is available, otherwise HTTP `503` with `{"ready": false}`. Vault readiness does
not scan directory contents. The semantic snapshot reads only SQLite schema, index
signature/state metadata, and whether any chunk exists; it does not count index rows,
initialize storage, load/embed/search, trigger synchronization, or mutate lifecycle state. A compatible
legacy index with chunks is available without persisting missing state. Previous indexes remain ready
through active or failed refreshes. Expected vault/storage filesystem and SQLite availability failures
return HTTP `503`; missing, corrupt, incompatible, uninitialized, initial-indexing, and initial-error
states remain unavailable.

Chunk generation prefers ATX heading boundaries outside fenced code and stores the available heading
hierarchy on every resulting chunk. Sections remain within the configured character bound. Long
sections split by monotonic source indexes at line/word/character boundaries without normalizing
Markdown characters, with exact source overlap only for oversized prose. Adjacent undersized
sections coalesce until bounded useful chunks are formed; their metadata identifies the first and
last contained hierarchies. Single-section hierarchy metadata prioritizes the leaf and nearest
ancestors within 200 characters. Lists and fenced code are not overlapped; a complete fence remains
intact whenever it fits in one chunk. Full synchronization and targeted refresh use the same chunker.

Embedding input remains separate from persisted chunk content. Headingless chunks use the stable
`title\ncontent` representation. When canonical VB-020 heading metadata is available and is not
already the chunk's first plain/ATX heading line, the representation is
`title\nheading hierarchy\ncontent`. This preserves Unicode and coalesced range metadata without
reparsing Markdown. Full synchronization and targeted refresh share the builder. User query text is
still embedded unchanged.

Production retrieval first filters chunks by semantic cosine similarity. Lexical coverage remains
weighted as title `0.40`, path `0.25`, heading `0.10`, content `0.20`, plus the existing exact-title
`0.10` or exact-content `0.05` bonus. The hybrid score is now
`(semantic_score + 0.70 * lexical_score) / 1.70`; this retains the previous signal ratio without the
old `1.0` saturation. Each note contributes its strongest chunk. Exact within-note relevance ties
select the lower source chunk index. Notes sort by hybrid score, semantic score, lexical score, then
canonical path. The semantic minimum threshold and `78%` relative result floor are unchanged.

## Deterministic retrieval evaluation

`tests/eval/` contains nine sanitized Markdown notes and thirteen structured EN/SK/cross-language
cases. Its deterministic concept embedder replaces only FastEmbed; evaluation still uses production
chunking, VB-021 embedding input, SQLite persistence, semantic and lexical scoring, filtering,
per-note aggregation and result ordering.

The checked baseline covers all, English, Slovak, cross-language and heading-context groups. Tests
reject material score ties, verify unchanged paths and chunks under reversed repository iteration,
and use controlled ablations to prove semantic, lexical, VB-021 hierarchy-context and EN/SK concept
equivalence sensitivity.

Measured baseline before and after VB-024:

```text
All:              13 cases, Hit@1 100%, Hit@3 100%, MRR 100%
English:           8 cases, Hit@1 100%, Hit@3 100%, MRR 100%
Slovak:            4 cases, Hit@1 100%,   Hit@3 100%, MRR 100%
Cross-language:    1 case,  Hit@1 100%,   Hit@3 100%, MRR 100%
Heading context:   2 cases, Hit@1 100%,   Hit@3 100%, MRR 100%
```

All thirteen expected path/heading pairs remain rank 1. Normalization changes the public final-score
scale but preserves every fixture rank and every non-saturated production ordering. The smallest
fixture top-1 margin remains the Slovak NAS recovery case; it changes only by the constant `1/1.70`
normalization, from `0.0240` to `0.0141`. High-score saturation and repository-order-dependent exact
ties now have focused production regressions.

## Known limitations observed during real use

1. External filesystem changes are not watched; they are picked up by startup/full synchronization.
2. The deterministic fixture does not measure real-model quality or latency on a production vault.
3. Multiple application processes sharing one index are not coordinated.
4. GPT/AI clients can invent wikilinks unless client instructions require verified existing notes.
5. Graceful shutdown cannot interrupt a model download, ONNX inference call, or filesystem operation already in progress.

## Verified baseline after VB-045

Native Windows:

```text
265 passed, 1 failed, 4 skipped
```

The one failure remains the known pre-existing Windows path-separator assertion around
`VaultService` response paths. The four skips are privilege-dependent symlink tests. Linux/CI was
not available with project dependencies in the current environment, so no new Linux result is claimed.

Additional checks:

```text
Ruff: passed
Python compileall: passed
git diff --check: passed
all 9 endpoint paths and operation IDs: unchanged
```

Focused VB-045 CLI run:

```text
30 passed
```

Focused health/readiness run:

```text
30 passed
```

Focused structured logging/request observability run:

```text
43 passed
```

Focused semantic/repository/indexer/ranking run:

```text
107 passed, 2 skipped
```

Protected API/health run:

```text
47 passed, 1 known baseline failure
```

Deterministic retrieval evaluation:

```text
9 passed
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
