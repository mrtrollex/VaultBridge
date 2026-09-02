# VaultBridge Architecture

## Context

VaultBridge exposes a deliberately small API over an Obsidian Markdown vault. It supports ordinary note operations and local semantic retrieval without turning the vault into a proprietary database.

## Current architecture

```text
Clients (Web Dashboard / ChatGPT / curl / CLI / integrations)
                    |
                    | HTTPS + Bearer token
                    v
               FastAPI app
       (`/ui/` + `/api/v1` + legacy aliases)
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

### Authentication

All protected legacy and `/api/v1` routes share one Bearer-token dependency. `API_KEY` is the
required current credential. An optional secret-safe `API_KEY_PREVIOUS` is accepted only during an
operator-controlled rotation window; removing it and restarting/redeploying ends that window. Both
configured candidates are checked with standard-library constant-time comparison. Authentication
does not log or return either credential, and public health endpoints remain outside this dependency.

### Rate limiting

Every protected legacy and `/api/v1` note/search route runs one shared rate-limit dependency before
the VB-042 authentication dependency. The standard-library fixed-window limiter uses monotonic time
and the direct ASGI peer host as its identity; it never reads bearer credentials or forwarded
client-address headers. Repeated invalid authentication attempts therefore eventually receive HTTP
`429`, while a missing required `API_KEY` retains the existing configuration error.

State is thread-safe, process-local, non-persistent, and bounded. Expired windows are reclaimed on
request handling; when the configured client cap remains full, the least-recently-used peer window
is evicted deterministically. There is no background cleanup thread, Redis, database counter, new
service, or distributed-quota claim. Restarts clear state and multiple processes do not coordinate.
Behind a reverse proxy, the ASGI peer may be the proxy, so external clients can share a bucket;
VB-043 deliberately does not infer trust from `X-Forwarded-For`, `X-Real-IP`, or `Forwarded`.

`GET /health`, `GET /health/live`, `GET /health/ready`, and the public schema-hidden `/privacy`
route do not carry the dependency. Request observability remains the outer ASGI layer, so handled
429 responses retain `X-Request-ID` and the normal safe lifecycle event with status `429`.

### Container distribution

The repository root `Dockerfile` remains the single production image definition. Normal Docker
Compose and the existing TrueNAS Custom App build it from source. Published GitHub Releases also
build that same file and distribute a versioned image as
`ghcr.io/<lowercase-repository-owner>/vaultbridge`; this adds a delivery artifact without changing
runtime configuration, mounts, API behavior, or semantic-index compatibility. VB-054 publishes only
the GitHub-hosted runner's normal Linux architecture. Multi-architecture manifests remain VB-055.

Docker sets Hugging Face's dedicated `HF_HOME` inside the existing writable semantic-data location:
`/vault/.obsidian-chatgpt-data/huggingface` for generic Compose and `/data/huggingface` for TrueNAS.
The image default is `/data/huggingface`, prepared for UID/GID 568. Compose retains its existing
non-root runtime identities and overrides the path where the generic deployment uses arbitrary
`PUID:PGID`. The cache is derived model data and never shares a note-content path.

### Current implementation modules

```text
app/main.py          application construction, dependency wiring, router registration
app/cli.py           read-only semantic integrity check and explicit offline rebuild entry point
app/api/             health, note/search routers, versioned alias registration and HTTP dependencies
app/ui/              explicit dashboard routes and bundled HTML/CSS/vanilla JavaScript assets
app/core/config.py   typed environment configuration
app/core/logging.py  structured VaultBridge application logging
app/core/observability.py request correlation and HTTP lifecycle timing
app/services/vault.py safe path resolution, Markdown note operations and contained note counting
app/services/duplicate_candidates.py live-title and verified semantic candidate composition
app/services/semantic_search.py embedding, incremental indexing, hybrid ranking and semantic health state
app/services/indexer.py one in-process full/targeted synchronization worker and deduplicating path queue
app/services/filesystem_watcher.py optional recursive Markdown event interpretation and debounce
app/services/rate_limiter.py bounded process-local fixed-window request allowance
app/repositories/semantic.py SQLite schema, semantic index persistence and read-only status statistics
app/semantic.py      compatibility facade for the pre-VB-005 internal API
```

### HTTP API versioning

The public application API is namespaced under `/api/v1`. The original unversioned note/search
paths remain compatibility aliases during the migration window, especially for the checked-in
ChatGPT Action schema. Each legacy/v1 pair is registered from one endpoint function with identical
dependencies, request/response models, service calls, exception handling, and content types; one
request therefore executes one domain operation.

Legacy operation IDs remain `createNote`, `appendNote`, `readNote`, `searchNotes`,
`findRelatedNotes`, `findDuplicateCandidates`, and `listNotes`. Their v1 counterparts use the
explicit stable `V1` suffix.
New integrations should use `/api/v1`; removal of compatibility paths requires a separate project
decision.

`/health`, `/health/live`, and `/health/ready` are unversioned operational interfaces. The public,
schema-hidden `/privacy` text endpoint is also unversioned. FastAPI's runtime `/docs`, `/redoc`, and
`/openapi.json` endpoints remain disabled.

VaultBridge-owned operational events use the standard Python logging pipeline and an idempotent
handler on the `vaultbridge` logger namespace. The handler writes one compact UTF-8 JSON object per
line to stderr for Docker/TrueNAS collection. Core fields are stable; contextual fields are
allowlisted and emitted only when relevant. Full and targeted synchronization share service-level
start/completion/failure instrumentation, while the background indexer reports scheduling and
shutdown ownership. Committed create/append operations log only their vault-relative paths.

Exception records keep the exception type and basename-only stack frames, omitting exception text to
avoid leaking note content, queries, credentials, or absolute host paths. Application logging calls
are isolated so formatter/handler failures cannot alter lifecycle transitions, indexing, committed
writes, or HTTP behavior. Uvicorn/FastAPI server and access logs remain separately managed by
Uvicorn.

An outer standard-library ASGI middleware gives every HTTP request one internally generated UUID hex
identifier and returns it as `X-Request-ID`. A `ContextVar` makes that identifier available to
VaultBridge log formatting across async request work and FastAPI's sync endpoint thread handoff.
Lifecycle events are `request_started` plus exactly one `request_completed` for handled responses or
`request_failed` for an exception escaping FastAPI. Terminal events use the matched route template,
any status actually observed from ASGI `http.response.start`, and non-negative millisecond duration
measured with `time.perf_counter()`; aborted requests without a response omit `status_code`. Raw
URLs, query strings, headers, and bodies are never logged. Incoming `X-Request-ID` values are
deliberately ignored and replaced rather than trusted or reflected.

Context is restored when the ASGI call ends, which isolates concurrent/later requests. Synchronous
application events, including targeted queue scheduling, inherit the active identifier. Work later
executed by the indexer's `ThreadPoolExecutor` does not inherit request context; VB-041 does not add
causal tracing fields to the queue. Uvicorn access/server records remain separate and are not
rewritten by this application middleware.

FastAPI lifespan submits semantic synchronization to one in-process background worker, so startup
does not wait for a complete vault scan. The synchronization operation remains synchronous inside
that worker and commits configurable note-count batches, preserving durable completed batches after
interruption. Lifespan shutdown signals cooperative cancellation, lets the active batch commit or
roll back normally, and skips remaining batches. Shutdown must still wait for an already-running
model download, ONNX inference call, or filesystem operation because those calls are not forcibly
interruptible.

When `SEMANTIC_WATCH_ENABLED=true`, lifespan starts one recursive `watchdog` observer after the
semantic indexer exists. Filesystem callbacks only interpret safe Markdown paths and submit them to
one monotonic debounce dispatcher; they never embed, write SQLite, or call the semantic service.
The dispatcher coalesces repeated paths and feeds the existing thread-safe atomic batch-enqueue
boundary, so both sides of a rename reach one targeted job even if the worker is idle.
Shutdown stops the observer, flushes accepted debounced paths, and only then shuts down the semantic
indexer. With the default `false`, no observer or debounce thread starts.

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
unrelated notes. A safely contained targeted path that no longer exists removes only its derived
note/chunk rows; this lets watcher delete and rename events avoid full-vault scans. A failed targeted
batch rolls back without replacing that batch's prior valid live-note index. Inaccessible,
path-escaping, oversized, excluded, broken-symlink, and non-UTF-8 targets still fail strictly; failed
paths remain queued until a later enqueue or explicit/full retry. Full-vault synchronization
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

`/health/live` is a dependency-free process liveness probe. `/health/ready` combines a non-iterating
check that the vault is an inspectable directory with authoritative semantic search availability. Its
semantic predicate uses a separate read-only SQLite snapshot limited to schema, index signature/state
and chunk existence, so orchestration polling does not scan the vault or count index rows. The same
predicate recognizes compatible legacy indexes with chunks and no persisted lifecycle state, without
mutating them. Runtime availability remains authoritative during an active or failed compatible
refresh, but the storage snapshot prevents a stale process-local flag from masking removed, corrupt or
incompatible storage. Expected filesystem/SQLite availability errors yield not-ready; programmer errors
retain the normal server-error boundary. Neither probe initializes state, starts the indexer, loads the
model, embeds content, performs search, or emits probe-specific log events.

Semantic chunking uses ATX Markdown headings outside fenced code as preferred section boundaries.
Each stored chunk carries the available heading hierarchy, while its content remains bounded by the
configured character limit. Oversized sections split through monotonic source indexes at useful line,
word or character boundaries, preserving every source character outside explicit overlap. Adjacent
undersized sections coalesce into useful bounded chunks; range metadata names their first and last
heading contexts. Hierarchy bounds retain the leaf before adding nearest ancestors. Configured
overlap is used only for split oversized prose; it does not bridge semantic sections, lists or fenced
code. A fence that fits within one chunk is kept intact. Oversized fenced code is split only when the
configured bound makes that unavoidable.

Chunk persistence and embedding representation are separate. SQLite stores the exact chunk content
produced by the heading-aware chunker. The text sent to the embedder is built in linear time as
`title\ncontent` for headingless chunks or `title\nheading hierarchy\ncontent` when canonical heading
metadata adds context. If the same plain or ATX heading is already the chunk's first line, it is not
prepended again. Full synchronization and targeted refresh both pass through this builder; query
text continues to be embedded directly, and the ranking pipeline is unchanged.

Search and synchronization share one embedder instance. A narrow execution lock serializes only
calls into that embedder; vault scanning, SQLite access, ranking, and response construction remain
concurrent.

Retrieval quality is guarded by a test-only deterministic evaluation under `tests/eval/`. It builds
a temporary vault from sanitized Markdown, injects a local concept-vector embedder, and otherwise
uses the production chunking, embedding-input, repository, scoring, filtering, aggregation and
ordering pipeline. Structured cases record path/heading relevance and accepted top-k ranks; Hit@1,
Hit@3 and mean reciprocal rank provide a repeatable before/after baseline. A checked JSON baseline,
subgroup calculations, reversed-repository-order run, material-tie diagnostics and controlled
semantic/lexical/heading/multilingual ablations validate the fixture itself. This adds no runtime
service, schema or production dependency.

Hybrid retrieval filters each chunk by the requested semantic cosine threshold, then calculates a
bounded lexical score from title (`0.40`), path (`0.25`), heading (`0.10`) and content (`0.20`)
query-term coverage plus the existing exact-title (`0.10`) or exact-content (`0.05`) bonus. The final
score is `(semantic_score + 0.70 * lexical_score) / 1.70`. Dividing by the total weight preserves the
established semantic-to-lexical ratio and non-saturated ordering while keeping distinct high-score
candidates distinct. The highest-ranked chunk represents each note; relevance ties within a note
prefer semantic score, lexical score and then lower chunk index. Notes sort by final score, semantic
score, lexical score and canonical path. The existing `78%` relative floor is applied after note
aggregation. Ranking remains linear apart from the final `O(n log n)` candidate sort and does not
require rebuilding embeddings.

Semantic index lifecycle state is persisted in the SQLite `meta` table as
`uninitialized`, `indexing`, `ready`, or `error`. `SemanticSearchService` owns
the transitions; `SemanticRepository` only stores the value and index data.
Schema availability and search readiness are separate conditions.

### Operational index maintenance

`python -m app.cli` is a standard-library local interface over existing domain services. `search`
calls `VaultService.search_notes()` and never constructs an embedder. `related` queries a compatible
persisted index without synchronizing it, preserves production ranking, and passes every candidate
through `VaultService.verify_existing_markdown_path()` before displaying a vault-relative path.
Both commands accept only validated vault-relative folders. `status` shares the VB-045 immutable
persisted-index inspection and formatting described below.

`index` runs the production `SemanticSearchService.sync()` path without first resetting a compatible
index. `reindex` is a top-level alias for the existing `index rebuild` clean-rebuild path. Both are
offline derived-index writes; Markdown remains authoritative and unchanged.

`python -m app.cli index check` is a stopped-service, persisted-storage administrative view. It uses an
immutable SQLite connection and refuses inspection when WAL/SHM sidecars exist, so the complete
semantic storage remains filesystem-unchanged. It checks only vault inspectability, SQLite
schema/metadata and inexpensive physical counts. A check neither constructs FastEmbed nor creates or
changes semantic storage, and a compatible legacy index with chunks remains standalone-searchable
without persisting a missing lifecycle state. It does not infer live process availability; `/health`
and `/health/ready` remain authoritative for that purpose.

`python -m app.cli index rebuild` is an explicit offline operation. The CLI validates the vault
before the repository atomically removes derived notes, chunks, and prior signature/state
metadata. `SemanticSearchService` then runs its normal full synchronization, preserving the current
signature, heading-aware chunking, heading-context embedding input, note-count batching and lifecycle
transitions. The new successful-sync timestamp and `ready` state commit in one transaction. Markdown
is never changed. A failed rebuild leaves the state at `error`; already committed new batches remain
durable, and the previous `last_successful_sync` is preserved unless finalization succeeds.
If SQLite explicitly reports the derived database as corrupt or not a database, the repository closes
it, removes only the configured database and its SQLite sidecars, and recreates current storage before
full synchronization. Other database errors are not treated as corruption.

VaultBridge has no cross-process synchronization lock, so the server must be stopped before
`status`/`index check`, `index`, `reindex`, or `index rebuild`. The CLI does not claim or introduce
multi-process coordination. Literal and semantic query commands do not intentionally mutate the
vault or index, but they are local filesystem/SQLite readers rather than a remote concurrency layer.

---

## Web Dashboard and planned platform packaging

VB-071 implements the dashboard shell and authenticated session boundary accepted in
[ADR 0003](docs/adr/0003-web-dashboard-architecture-and-security.md), VB-072 implements the public
health-backed Overview, VB-073 implements protected literal and semantic Search, and VB-074
completes usability, accessibility, privacy, browser, and normal-image hardening. VB-080 defines the
version-neutral [TrueNAS Community App packaging design](docs/TRUENAS_COMMUNITY_APP_DESIGN.md).
Publishing and verifying a dashboard-capable image (VB-075), implementing the definition (VB-081),
and live/upstream validation remain later distribution tasks.

### Current dashboard relationship

VaultBridge remains one universal application in one core repository. The Web Dashboard is a small
first-party browser client bundled with that application, not a separate product and not a
TrueNAS-specific UI.

```text
Browser
   |
   v
VaultBridge Web Dashboard (`/ui/`)
   |
   | existing/stable application boundaries
   v
VaultBridge services / API
   |
   +-- Markdown vault (authoritative)
   +-- local semantic index (derived)

ChatGPT / curl / scripts / integrations
   |
   +----------------------------------> VaultBridge API
```

The current shell contains Overview, Search, API / Integration, and About areas. Overview
automatically reads the existing public `GET /health` contract. Search uses the existing protected
`POST /api/v1/notes/search` and `POST /api/v1/notes/related` contracts. Browser code formats and
presents returned facts but does not duplicate health, lifecycle, counting, ranking, filtering,
containment, thresholding, or live-note verification ownership. The API and CLI remain first-class
and independently usable.

The dashboard is served by the existing FastAPI application from the same repository and origin.
Public `GET`/`HEAD /ui` redirects temporarily to canonical
`/ui/`; the entry document is at `/ui/`, bundled assets use the explicit `/ui/assets/` namespace,
and unknown UI paths return `404` rather than an SPA fallback. It uses relative URLs, same-origin API
calls, and lightweight static HTML/CSS/vanilla JavaScript without a mandatory Node/npm pipeline,
React/Vue/Svelte dependency, second service, second container, or new frontend dependency. The root
Dockerfile's existing `COPY app ./app` instruction includes these assets without a Dockerfile change.
The small browser module boundary keeps shell/session/navigation behavior and the authenticated
fetch helper in `app.js`, public health fetching/validation/rendering in `overview.js`, and protected
search mode/request/lifecycle/result rendering in `search.js`.

The public shell contains no configured credential. Unlock and reload revalidation call
`GET /api/v1/notes/list?limit=1`; a successful response allows the submitted key to be stored under
`vaultbridge.ui.apiKey` in `sessionStorage` and used by one authenticated fetch helper. Logout and
`401` clear it, while `429`, `503`, and network failures do not misclassify an already stored key.
The UI receives the ADR 0003 CSP, `nosniff`, and no-referrer headers, loads no third-party resources,
and renders dynamic strings through text-only DOM APIs. No account, cookie, OAuth, secret-return, or
dashboard-specific authentication endpoint exists.

The Overview displays only facts already exposed by `/health`: application/vault status, semantic
lifecycle/readiness/search availability, vault/index/chunk counts, last successful full sync,
process-local indexer running state, and full-sync recovery debt. Its display-only overall status is
Ready when the vault and a ready searchable index are healthy, Indexing when the lifecycle state is
`indexing`, Unavailable when health cannot be fetched or the vault is missing, and Degraded for the
remaining responding states. Lifecycle and search availability remain separate factual rows.

The same-origin health request is deliberately public and never receives the stored dashboard
Bearer credential. Initial load and manual refresh provide explicit loading, malformed-response,
and unavailable states without polling, WebSocket, SSE, or changes to authentication state. Counts
and timestamps are presentation-formatted only; no inferred percentage or synchronization progress
is calculated. Watcher enabled/running state is not exposed by the current HTTP contract and is
omitted. The UI exposes no live rebuild/mutation action. The stopped-service CLI and existing
single-process index ownership remain authoritative. Any future HTTP/UI index mutation requires a
separate design proving it is safe while the serving process runs.

Literal Search sends only `query`, `folder`, and `limit`; Semantic Search sends only `text`,
`folder`, `limit`, and `min_score`. Returned order is rendered unchanged. Semantic rank is the array
position, score fields are presentation-formatted without normalization, and null optional fields
receive text-only fallbacks. Mode changes and newer searches abort/invalidate older controlled
requests. Search terms and results remain memory-only: they are not stored, placed in URLs, logged
for UI analytics, or restored on reload. Logout and `401` clear protected form/results; `429`, safe
validation errors, connectivity errors, and semantic `503` do not invalidate an authenticated
session. The Search area performs no note reads or mutations and exposes no duplicate or index
operation.

### Planned deployment relationship

The normal VaultBridge image is the one distributable application across platforms:

```text
                 one VaultBridge image
                          |
             +------------+-------------+
             |                          |
       Docker / Compose       TrueNAS catalog adapter
             |                          |
             +------------+-------------+
                          |
                     VaultBridge
                     /api/v1 + /ui/
```

TrueNAS Community App packaging is a configuration/distribution adapter around a published,
dashboard-capable VaultBridge image. It may define metadata, storage, ports, environment/secrets,
health checks, resources, and a Web Portal target, but it does not own API, CLI, vault, semantic,
authentication, or other domain behavior. TrueNAS-specific code must not enter core services, and
VaultBridge must remain deployable without TrueNAS.

Repository ownership is deliberately separated:

- `mrtrollex/VaultBridge` owns the application runtime, API, semantic behavior, CLI, bundled
  dashboard, Dockerfile, GHCR image, and generic deployment documentation;
- `truenas/apps` should own the accepted upstream TrueNAS Community App definition;
- `ghcr.io/mrtrollex/vaultbridge:<released-version>` is the interface between those repositories.

Temporary packaging/test fixtures may live in the core repository when useful for reproducibility,
but there must be no permanent `VaultBridge-TrueNAS` runtime fork or two authoritative copies of the
upstream catalog definition. Current TrueNAS support remains the documented source-built Custom App
path until the VB-080 design is implemented by VB-081, validated on real TrueNAS by VB-082, and
accepted upstream through VB-083.

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

Potential home for Bearer token verification if security logic grows beyond the current small shared
API dependency.

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
`SemanticSearchService` and `SemanticRepository`.

### `services/filesystem_watcher.py`

- disabled-by-default recursive `watchdog` observer
- safe create/modify/delete/move interpretation through vault containment rules
- one monotonic debounce dispatcher feeding the existing indexer queue
- watcher-first shutdown with accepted-path flush before indexer shutdown

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

FastAPI routers, versioned route registration and dependencies. Routes should orchestrate services
rather than implement domain logic. Legacy and current version paths must converge on the same
endpoint function and service path.

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
- chunker/embedding-input format version (`v3-heading-context`)
- persisted lifecycle state (`uninitialized`, `indexing`, `ready`, `error`)
- explicit per-sync progress such as current note, percentage, batch and ETA (planned)
- last successful full synchronization

The index is **derived data**. Migrations should be used when cheap; otherwise a safe automatic
rebuild is acceptable. A chunker-format signature change clears incompatible notes/chunks and
rebuilds them from Markdown without a SQLite schema migration. A targeted refresh that discovers an
older signature performs the required full rebuild before it can restore the index to `ready`, so
embedding generations are not mixed.

---

## Request flows

### Literal search

```text
request → rate limit → auth → validate folder → scan/search Markdown → response
```

A future optimization may add a lexical index, but only if measured vault sizes justify it.

### Semantic search — target

```text
request
  ↓
rate limit
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
bounded candidate overfetch
  ↓
VaultService live Markdown containment verification
  ↓
caller-visible limit
  ↓
response

Background index maintenance happens independently.
```

Related-note responses treat semantic-index paths as derived input. The shared legacy and `/api/v1`
endpoint asks for a bounded three-times candidate window capped at `50`, then `VaultService` resolves
each path through the live vault containment policy. Only regular `.md` targets inside the configured
vault and optional folder survive; safe internal aliases become canonical vault-relative paths and
titles. Filtering is read-only and preserves surviving rank order. Semantic score, lexical score,
combined score, heading, and snippet still reflect the indexed snapshot, so an externally edited
live note can retain stale semantic fields until normal synchronization runs.

### Duplicate-candidate discovery

```text
prospective title + optional text
  ↓
conservative live filename equivalence ───────┐
  ↓                                           │
one existing semantic search                  │
  ↓                                           │
VB-031 live Markdown path verification        │
  └───────────────────────────────────────────┘
  ↓
canonical-path merge: exact title first, then semantic order
  ↓
caller-visible limit → advisory response only
```

The service scans contained live user-note filenames without reading their bodies, excluding the
same internal service directories used by semantic indexing. Title equivalence uses NFKC, trim,
Unicode casefolding, and collapsed whitespace; it does not use fuzzy or substring matching. One
bounded semantic query uses the existing ranking unchanged, and every derived semantic path is
verified against the live vault before merging. An overlap remains `exact_title` while retaining its
semantic scores/snippet. Semantic-only evidence indicates relatedness, not a duplicate verdict.

If semantic search is unavailable, live exact-title candidates can still be returned; with no exact
evidence the existing semantic-unavailable HTTP response is preserved. The operation is read-only:
it does not merge, create, append, rename, delete, add backlinks, enqueue indexing, or mutate SQLite.

### Note write

```text
request → rate limit → auth → safe path → validate size → write Markdown → enqueue note for re-index
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
