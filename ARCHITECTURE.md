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
 (`/ui/` + `/api/v1` + legacy aliases + opt-in `/mcp`)
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

The `.md` files are authoritative. The SQLite semantic index and every metadata, relationship,
cache, or query projection are disposable derived data and must be rebuildable.

### Authentication

All protected legacy and `/api/v1` routes share one authentication dependency. Bearer integrations
use current/previous keys; same-origin Dashboard requests can instead use the signed session cookie
with `X-VaultBridge-UI-Request: 1` when no Authorization header is present. `API_KEY` is the
required current credential. An optional secret-safe `API_KEY_PREVIOUS` is accepted only during an
operator-controlled rotation window; removing it and restarting/redeploying ends that window. Both
configured candidates are checked with standard-library constant-time comparison. Authentication
does not log or return either credential, and public health endpoints remain outside this dependency.

### Rate limiting

Every protected legacy and `/api/v1` note/search route runs one shared rate-limit dependency before
the VB-042 authentication dependency. The standard-library fixed-window limiter uses monotonic time
and the direct ASGI peer host as its identity; it never reads bearer credentials or forwarded
client-address headers. Repeated invalid authentication attempts therefore eventually receive HTTP
`429`, while a missing required `API_KEY` retains the existing configuration error. The opt-in MCP
Streamable HTTP transport calls the same Bearer verifier and limiter instance once at its ASGI
boundary before MCP dispatch. Its stdio operation budget remains separate and is never applied to
HTTP operations.

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
app/services/wikilinks.py deterministic wikilink parsing and exact resolution through VaultService
app/services/markdown_links.py bounded inline Markdown note-link parsing and source-relative resolution
app/services/duplicate_candidates.py live-title and verified semantic candidate composition
app/services/knowledge_query.py bounded immutable live Knowledge Query domain orchestration
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

When MCP HTTP is enabled, the same parent lifespan also enters the SDK session manager after the
indexer and optional watcher start. Shutdown closes MCP sessions before stopping the watcher and
indexer. Mounted sub-application lifespan is deliberately not relied on.

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

VB-151 E2 adds an internal `SharedEmbeddingResource` for trusted injection into independent
semantic owners. It owns one lazy FastEmbedder using global settings and the default data
directory's `models` cache; cache failures have no fallback. A cancellable lease serializes
backend initialization, inference, fingerprint/evidence resolution, validation and release.
Separate generation pins let owner pipelines run concurrently while preventing model replacement
during scoring or destructive preparation. Invalidation drains pinned operations, releases the
old backend model, then advances the generation; owners discard cached compatibility evidence
and reject prepared searches from older generations. Shutdown rejects new leases and drains
active operations before release. The process composer owns shutdown, not individual services.
Repositories, source/index bindings, synchronization locks and lifecycle state remain per owner.
This resource has no production named-space startup wiring; E6 remains separate work.

VB-151 E3 adds owner-local resumable synchronization through internal `begin_sync`, `sync_step`,
`cancel_sync` and `close_synchronization` methods. An opaque identity handle reserves exactly one
owner until completion, cancellation or failure. `SyncStep` returns cumulative durable counts and
an explicit `incomplete` or `complete` state; exceptions terminalize the handle as `cancelled` or
`failed`. Foreign, forged, terminal and concurrently executing handles are rejected. The legacy
`sync`, `sync_paths` and `rebuild` methods drive these turns to genuine completion, retaining their
count dictionaries and failure behavior. `BackgroundSemanticIndexer` retains its synchronous job
contract and recovery queue behavior; no named runtime is activated.
Legacy synchronous entry points serialize whole operations with an owner-local mutex outside
per-turn locks and embedding generation admission. Queued shared-resource callers can cancel
without acquiring a pin or backend lease. Their lifecycle admissions let close fence and drain
waiters as well as the active operation, without running the remaining synchronization work.

VB-151 E4 adds the private `SharedSemanticScheduler` execution primitive for up to eight
registry-authorized enabled owners injected with the same E2 resource. Construction starts no
worker or model. Explicit `start()` creates one owned single-thread executor and a dispatcher
that sleeps on a condition when idle. `start(startup=True)` requests one full sync per enabled
binding before dispatch. Each sorted-SpaceId round-robin turn executes at most one bounded E3
step; yielded continuations retain independent progress, cancellation and errors. The internal
continuation exposes its actual full/targeted mode, including E3's recovery promotion.

Qualified targeted admission validates the original registry token, owner, root and contained
Markdown path. One lock enforces 2,000 distinct pending identities per owner and 10,000 globally,
including in-flight targeted identities; submissions admit one path at a time. Overflow retains
one full-sync-needed flag per binding. Full sync takes priority, absorbs earlier queued work,
and preserves concurrent writes for follow-up; overflow during a full sync requires another full
reconciliation. Full failure/cancellation retains recovery debt and pauses that owner until a
new submission or explicit retry, without a hot retry loop. Healthy owners continue independently.
Shutdown rejects submissions and signals every owner before discarding queues, draining only the
active safe boundary, closing E3 continuations/workspaces and stopping the executor. The process
composer closes the E2 resource afterward. Legacy `BackgroundSemanticIndexer` also accepts a
process-owned executor without shutting it down; its default behavior still owns its own worker.
The shared scheduler does not wrap eight legacy whole-sync loops. Disabled owners receive no
scheduler work, and the existing deferred registry lifecycle remains unchanged. E5 supplies the
private shared watcher described below; E6 composition/activation remains deferred.

Discovery uses a private temporary SQLite workspace for canonical paths and its directory frontier,
with a fixed page cache and at most one open `scandir` cursor. Each discovery/admission turn consumes
at most 256 directory operations/entries or targeted input paths by default (internal override
1..4096). Rejected and non-Markdown entries count too. The cursor's directory identity and containment
are rechecked before consumption. After discovery/admission completes, paths are processed in
`(path.casefold(), path)` order, with exact canonical deduplication. A processing turn commits at most
`index_batch_size` notes; note metadata uses primary-key lookups. Completed discovery is followed by
primary-key metadata reconciliation in turns of at most `index_batch_size` rows. Stale deletion
cannot run against an unfinished scan. Targeted missing-path deletion and replacements in one batch
share a transaction, so a failed active batch retains all its prior indexed data.
Discovery inspection errors fail the scan rather than authorizing stale deletion from an incomplete
inventory. A raced ordinary deletion may be skipped only after safe absence is confirmed. Transient
failures require a new scan; prior indexed data and durable completed batches remain intact.
Missing-target admission evidence is provisional: processing revalidates the current path through
the strict targeted policy before deleting rows, and indexes an eligible reappeared note in that
same batch. Unsafe or ambiguous current evidence fails the batch without deleting prior rows.
Missing-target checks inspect every relevant ancestor, including contained directory alias targets,
and recheck ancestor facts plus the first missing component before treating absence as confirmed.
Dangling, escaping or inaccessible ancestors cannot authorize a deletion.

Each turn revalidates its source/root identity, repository/database identity, immutable binding,
configuration and embedding generation/signature. Canonical notes use the existing bounded, pinned
`VaultService` reader with per-path inode facts before embedding, without sibling enumeration.
Yield releases owner locks, generation pins, backend leases,
SQLite connections and transactions; only the scan cursor and bounded control state remain in memory.
Cancellation is checked during discovery, targeted admission, metadata work, between batches and
while shared locks/leases are awaited. Shutdown signals active work and waits for its current safe
boundary, then discards remaining scan work. Failed/cancelled handles cannot resume; a new operation
rediscovers against durable completed batches. This is process-local continuation, not persisted job
recovery or cross-process coordination. Temporary path storage grows with the scan on disk, and a
changing vault remains a cooperating-filesystem scan rather than an atomic filesystem snapshot.
Preparation is admitted before acquiring a generation pin or invoking backend validation. Close
atomically fences new admission, signals admitted preparation/turns, and waits for their resource
contexts to exit before discarding a continuation. Cancellation checks and destructive preparation
share that fence; an already admitted non-interruptible SQLite boundary may finish before close
returns. Backend calls and drain waits never hold the admission fence.
Idempotent reconfiguration still validates the source binding, but changes the continuation revision
only when effective settings change. Preparation rechecks that revision under the configuration lock
before reset/initialization and continuation creation; resumed turns validate under the same lock.

Only completed full reconciliation atomically publishes `ready` and `last_successful_sync` and
clears owner-local full-sync recovery debt. Incomplete steps remain `indexing`; cancellation/failure
records `error` when the original storage/root can still be validated, otherwise leaves storage
untouched and the handle terminal. A full retry is required after interrupted full work, including
when a targeted request arrives first. Previous compatible search availability remains independent
of this lifecycle. Initial binding/signature preparation and authorized rebuild resets retain their
existing atomic SQLite/backend validation boundaries; turn budgets bound discovery and note/metadata
counts, not the wall time of a non-interruptible filesystem, backend or SQLite operation.

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

Compatibility uses a deterministic `semantic-index-v2:` signature whose canonical JSON payload
contains schema `2`, the `v3-heading-context` content contract, model name, effective chunk size and
overlap, and an `embedding-v1:<sha256>` fingerprint. The embedding manifest hashes the exact resolved
ONNX model, `config.json`, `tokenizer.json`, `tokenizer_config.json`, and
`special_tokens_map.json`, plus the application-owned FastEmbed dense-text, attention-mask mean
pooling, float32 flattening and VaultBridge L2-normalization contract. Cache roots, timestamps,
embedding/index batch sizes, CPU memory-arena configuration, provider selection, ONNX Runtime
version, watcher settings and ranking configuration are deliberately excluded.

The startup synchronization resolves FastEmbed with lazy ONNX loading and fingerprints its exposed
resolved model directory before repository preparation. Until that succeeds, persisted embeddings
are not runtime-searchable. Resolution failure records an error without replacing the stored
signature or deleting recoverable rows. Once the identity is known, a mismatch—including every
legacy v3 signature or missing signature with existing rows—atomically invalidates derived rows and
starts one full rebuild. A matching signature retains normal incremental synchronization.

### Operational index maintenance

`python -m app.cli` is a standard-library local interface over existing domain services. `search`
calls `VaultService.search_notes()` and never constructs an embedder. `related` queries a compatible
persisted index without synchronizing it, preserves production ranking, and passes every candidate
through `VaultService.verify_existing_markdown_path()` before displaying a vault-relative path.
Both commands accept only validated vault-relative folders. `query` is the read-only CLI adapter for
`KnowledgeQueryService`; it maps only semantic text, literal text, folder, exact paths, required tags,
and limit into the existing domain request, and displays only verified canonical paths, semantic
score evidence, ordering, and compatible-index basis. `status` shares the VB-045 immutable
persisted-index inspection and formatting described below.

`index` runs the production `SemanticSearchService.sync()` path without first resetting a compatible
index. `reindex` is a top-level alias for the existing `index rebuild` clean-rebuild path. Both are
offline derived-index writes; Markdown remains authoritative and unchanged.

`python -m app.cli index check` is a stopped-service, persisted-storage administrative view. It uses an
immutable SQLite connection and refuses inspection when WAL/SHM sidecars exist, so the complete
semantic storage remains filesystem-unchanged. It checks only vault inspectability, SQLite
schema/metadata, the recorded signature shape/configuration and inexpensive physical counts. A check
neither constructs FastEmbed nor creates or changes semantic storage; it therefore does not re-hash
the current local artifacts. Runtime synchronization and semantic query startup perform the exact
artifact comparison before exposing stored vectors. A compatible fingerprinted index with chunks but
no lifecycle state remains structurally standalone-searchable without persisting that state. The
check does not infer live process availability; `/health` and `/health/ready` remain authoritative.

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

## Web Dashboard and platform packaging

VB-071 implements the dashboard shell and authenticated session boundary accepted in
[ADR 0003](docs/adr/0003-web-dashboard-architecture-and-security.md), VB-072 implements the public
health-backed Overview, VB-073 implements protected literal and semantic Search, and VB-074
completes usability, accessibility, privacy, browser, and normal-image hardening. VB-080 defines the
version-neutral [TrueNAS Community App packaging design](docs/TRUENAS_COMMUNITY_APP_DESIGN.md).
VaultBridge `v1.1.0` provides the published dashboard-capable image. VB-081 validated its catalog
adapter, and VB-083 completed upstream review/merge through `truenas/apps` PR #5805. The accepted
source and generated catalog entry exist upstream, and Discover Apps availability is confirmed.
VB-082 remains the separate partial lifecycle-validation gate.

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

The current shell contains Overview, Search, Hygiene, API / Integration, and About areas. Overview
automatically reads the existing public `GET /health` contract. Search uses the existing protected
`POST /api/v1/notes/search` and `POST /api/v1/notes/related` contracts. After a selected note is read,
its bounded Relationships section uses protected `GET /api/v1/notes/links` and
`GET /api/v1/notes/backlinks`. Browser code formats and presents returned facts but does not duplicate
health, lifecycle, counting, ranking, filtering, containment, relationship resolution, thresholding,
or live-note verification ownership. The API and CLI remain first-class and independently usable.

The dashboard is served by the existing FastAPI application from the same repository and origin.
Public `GET`/`HEAD /ui` redirects temporarily to canonical
`/ui/`; the entry document is at `/ui/`, bundled assets use the explicit `/ui/assets/` namespace,
and unknown UI paths return `404` rather than an SPA fallback. It uses relative URLs, same-origin API
calls, and lightweight static HTML/CSS/vanilla JavaScript without a mandatory Node/npm pipeline,
React/Vue/Svelte dependency, second service, second container, or new frontend dependency. The root
Dockerfile's existing `COPY app ./app` instruction includes these assets without a Dockerfile change.
The small browser module boundary keeps shell/session/navigation behavior and the authenticated
fetch helper in `app.js`, public health fetching/validation/rendering in `overview.js`, and protected
  search mode/request/lifecycle/result rendering plus selected-note relationship presentation in
  `search.js`. Relationship requests share the note-reader logout/abort/generation lifecycle, while
  each group independently reports loading, empty, failure, and ready states. Rendering preserves
  server order and duplicates, caps each group at 20 visible items, uses text-only DOM APIs, and adds
  no browser persistence, relationship parsing, graph ownership, or mutation.

Hygiene uses a focused `hygiene.js` presentation module and the existing protected
`POST /api/v1/knowledge/hygiene/scan` contract. Only Run scan / Refresh scan initiates a scan.
The panel renders every returned finding (up to 500) with safe typed evidence and canonical paths,
groups returned counts by kind, and displays scan, candidate and derived-index coverage separately.
No diagnostic rules, repairs, mutation, persistence or automatic polling are added.
Navigation away cancels pending scan presentation; generation checks suppress late results/failures.

The public shell contains no configured credential. Unlock, restore and logout use the existing
`/ui/session` boundary and signed HttpOnly cookie; JavaScript does not store the API key. Unlock posts it as JSON, never a URL parameter.
The cookie has path `/`, `HttpOnly`, `SameSite=Strict`, a seven-day refreshed lifetime, and `Secure`
when the request scheme is HTTPS. Sessions are signed using the current key; rotating that key
invalidates them even during previous-key Bearer overlap. Logout deletes the browser cookie without
a server-side revocation list; a copied token remains valid until expiry or current-key rotation.
The authenticated fetch helper uses same-origin credentials and `X-VaultBridge-UI-Request: 1`.
Logout and `401` invalidate requests and clear protected Hygiene data immediately; `429`, `503`
and network failures retain the session.
The UI receives the ADR 0003 CSP, `nosniff`, and no-referrer headers, loads no third-party resources,
and renders dynamic strings through text-only DOM APIs. No account, OAuth or secret-return endpoint
is introduced by Hygiene.

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
- `truenas/apps` owns the accepted upstream TrueNAS Community App definition;
- `ghcr.io/mrtrollex/vaultbridge:<released-version>` is the interface between those repositories.

Temporary packaging/test fixtures may live in the core repository when useful for reproducibility,
but there must be no permanent `VaultBridge-TrueNAS` runtime fork or two authoritative copies of the
upstream catalog definition. The accepted Community App is the preferred ordinary TrueNAS path; the
documented source-built Custom App remains an advanced/manual compatibility path. VB-082 continues
to own the unresolved post-merge lifecycle evidence.

### MCP transports

[ADR 0004](docs/adr/0004-mcp-integration.md) accepts MCP as another thin client protocol over the
same services. VB-091 implements its read-only stdio phase as an explicit alternate process entry.
VB-092 adds an opt-in Streamable HTTP adapter inside the existing FastAPI process and port.

VB-091 is intentionally read-only and stdio-first:

```text
local MCP client
       |
       | launches `python -m app.mcp_server`
       v
MCP stdio adapter
       |
       +--------------------+
       |                    |
       v                    v
 VaultService      SemanticSearchService
       |                    |
   Markdown          compatible ready
                    persisted index
```

With `MCP_WRITE_ENABLED=false`, the stdio entry point constructs only existing read services. It
does not start FastAPI, `BackgroundSemanticIndexer`, `SemanticFilesystemWatcher`, synchronization,
rebuild, or any semantic storage writer. Immutable SQLite status/chunk reads avoid creating schema objects or WAL/SHM
sidecars. This preserves the current no-cross-process-index-writer boundary while allowing
local MCP clients to list, read, search, find related notes, and request duplicate candidates without
the HTTP application. Semantic tools are available only when the existing read-only persisted-index
inspection confirms a compatible ready index.

The five VB-091 tools are `list_notes`, `read_note`, `search_notes`, `related_notes`, and
`duplicate_candidates`; VB-103 adds `note_links` and `note_backlinks` over `RelationshipService`.
`related_notes` is the single semantic-retrieval name; there is no duplicate
`semantic_search` alias. Note content will also be readable as `text/markdown` through the contained
`vaultbridge://note/{percent-encoded-vault-relative-path}` Resource template. The URI never exposes
an absolute host path and is decoded through `VaultService`. VB-106 conditionally adds `create_note`
and `append_note` when `MCP_WRITE_ENABLED=true`; no Prompts are included.

Streamable HTTP is mounted through the official MCP SDK at fixed `/mcp` only when
`MCP_HTTP_ENABLED=true`. The SDK owns protocol framing and DNS-rebinding protection through typed
Host/Origin allowlists. VaultBridge applies its current/previous Bearer verification and the running
application's direct-peer limiter before SDK dispatch. The adapter reuses the exact `VaultService`,
`SemanticSearchService`, `DuplicateCandidateService`, `RelationshipService`, and
`BackgroundSemanticIndexer` objects wired by
`create_app()`, so it sees the live index lifecycle and relationship behavior without creating
another owner. The route remains outside REST OpenAPI.

HTTP uses stateless JSON responses and the same default eight read-only tools and contained Resource. It has
no MCP operation limiter inside the adapter, preventing double counting with the HTTP boundary. The
deprecated standalone HTTP+SSE transport, a custom transport, a second MCP service/container, and a
new port are not implemented.

When writes are enabled, both transports reuse `VaultService` and add only `create_note` and
`append_note`. HTTP queues committed paths through the application-owned indexer. Stdio owns an
indexer only in write mode, never starts a full sync merely to enable writes, and shuts it down with
the server. Unchanged creates and deduplicated appends enqueue nothing; queue failure never rolls
back authoritative Markdown. MCP cannot overwrite, delete, update sections, insert backlinks,
manage arbitrary files, or bypass deferred VB-032/VB-033/VB-034 boundaries.

---

## Accepted Knowledge Spaces design (VB-150; no current multi-space support)

[ADR 0009: Knowledge Spaces and Scope Policies](docs/adr/0009-knowledge-spaces-and-scope-policies.md)
is Accepted; VB-150 is complete as design-only work. Current support remains one contained Markdown root.
The accepted design separates stable logical `space_id` from the private root binding and extends future
note identity to `(space_id, canonical_relative_path)`. Existing path-only calls stay default-space
calls; legacy `VAULT_PATH` remains valid without mandatory migration.

One future domain/policy boundary validates finite selected scopes and read/write/index/capability
policy before dispatch to existing per-space containment and domain owners. Reads may explicitly
select up to eight spaces; writes select one destination. Independent derived semantic indexes,
local-only relationship resolution, same-space promotion and one-space hygiene preserve current
ownership. BACKLOG.md now defines VB-151's authoritative configuration, execution-budget, precise
merge, lifecycle and provenance implementation contract. Runtime Slice A implements the private
immutable configuration/types/registry and `SpacePolicyResolver` in `services/knowledge_spaces.py`.
`KNOWLEDGE_SPACES_JSON` is parsed once by Settings; the internal registry factory performs
non-mutating named binding validation. Legacy implicit-default construction adds no filesystem
checks. Slice B's explicit `services/space_owners.py: compose_space_registry` validates all records
before composing one private `SpaceOwners` bundle per definition: VaultService, relationships,
query, duplicates, capture, promotion, hygiene, immutable semantic inspection and a deferred
enabled-index lifecycle handle (None when disabled). Every local dependency uses that same vault.
No model, writable repository, worker or bound enqueue callback is created. Immutable inspection
reuses the existing semantic owner's classifier and repository's immutable reader without loading
a fingerprint. Named semantic activation/resource scheduling waits for E; scoped
capture/promotion provenance and index callbacks wait for E/F.
`services/space_operations.py: SpaceOperations` authorizes through SpacePolicyResolver before
owner access, verifies the registry token/exact bundle and named root identity, then delegates
only one-space note reads, outgoing/backlinks, nonsemantic duplicate advice, hygiene or inspection.
Read identities are qualified only after VaultService canonical containment verification; other
local payloads inherit the outer authorized space ID. Slice C adds internal `scoped_list`,
`scoped_literal_search` and nonsemantic `scoped_query`. One request-local `ScopedBudget` fixes
per-space allocations before sequential ASCII-ID execution. `VaultService.scoped_session` streams
charged directory entries, retains canonical top-K, accounts native component/handle probes and
bounded verified bytes, and holds a read-only root handle until final root verification. Incomplete
discovery discards the space; retention overflow preserves canonical top-K with partial coverage.
`ScopedKnowledgeQuery` wrapper B rejects legacy bare predicates and projects qualified same-space
constraints into `KnowledgeQueryService.evaluate_supplied_nonsemantic`, reusing ADR 0006 validation
and filters. `RelationshipService.derive_normalized_bounded` coordinates incremental dialect spans
in source order, shares occurrence/parse credits, admits before materialization/resolution and
reuses supplied local resolution facts. Exhaustion discards that space's relationship matches.
Frozen qualified results carry finite authorized coverage; cancellation fails the whole request.
Legacy eager APIs remain unchanged. D3a adds internal `scoped_semantic_search` for trusted executable
semantic owners, without changing normal owner composition. It establishes bounded live Markdown
eligibility, prepares D2 snapshots for all executable contributions, compares immutable scoring
descriptors before any query embedding, and delegates precise scoring and local floors/windows to
D2. A single qualified full-precision merge is reverified once under retained root sessions before
global truncation; there is no refill or global relative floor. Index/work failures produce bounded
coverage, while descriptor mismatch and cancellation fail the whole request. D3b enables semantic
`scoped_query` through this same pipeline after whole-selection Query, semantic/indexing and applicable
relationship authorization. Shared `KnowledgeQueryService.evaluate_supplied_eligibility` evaluates
live filters on the supplied bounded snapshot before semantic preparation, reusing source reads and
relationship derivations. The nonsemantic supplied evaluator retains its contract. D1–D3b and E1 are
internal primitives only; E2 shared resources, E3 synchronization and E4 scheduling are also internal. E5–E6 and
F–G remain pending and VB-151 is incomplete.
Existing REST/MCP/CLI composition rejects named configuration before owner construction with
`invalid_configuration` (internal reason `named_serving_unsupported`). Named serving remains blocked
until VB-152 integrates every applicable entry point. No public schema, route, operation ID,
resource, storage format or legacy response changes.

## Target architecture

```text
                  REST / Web / CLI clients          MCP clients
                            |                       /          \
                            v                  stdio       HTTP
                  FastAPI / CLI adapters             \       /mcp
                            |                          v       /
                            +---------------------- MCP adapter
                                                    |
                          +-------------------------+-------------------+
                          |                                             |
                          v                                             v
                   +-------------+                              +---------------+
                   | VaultService |                              | SearchService |
                   +------+------+                              +-------+-------+
                          |                                             |
                          v                                             v
                   Obsidian Markdown                             SemanticIndexer
                                                                       |
                                                         +-------------+-------------+
                                                         v                           v
                                                   Embedder (ONNX)             SQLite repo
```

## Proposed module boundaries

### `core/config.py`

Typed environment configuration. No HTTP or vault logic.

### `core/http_security.py`

Shared current/previous Bearer verification and direct-peer fixed-window enforcement used by REST
dependencies and the MCP HTTP ASGI boundary.

### `services/vault.py`

- safe path resolution
- exact contained source-relative Markdown target verification
- Markdown read/write/append
- note enumeration
- content size validation

### `services/frontmatter.py`

- decoded-Markdown-only recognition of the ADR 0005 frontmatter envelope
- immutable absent, ordered bounded valid, or privacy-safe invalid domain results
- YAML 1.2 Core scalar resolution, duplicate detection, safe-feature rejection, and exact metadata
  bounds without filesystem access, adapter exposure, persistence, or note mutation
- immutable exact portable alias/tag projections from valid frontmatter, preserving source order,
  duplicates, field-local failure, and the accepted VB-112 bounds
- PyYAML is a direct bounded runtime dependency because the standard library has no YAML parser and
  a handwritten YAML grammar would be unsafe and costly to maintain; only its event parser is used,
  never object construction or default implicit resolution, and it adds no service or network
  dependency

### `services/wikilinks.py`

- immutable wikilink metadata in Markdown source order
- internal immutable source positions for cross-dialect composition without changing legacy
  equality or representation
- backtick/tilde fenced-code exclusion
- exact, ambiguity-safe live-note resolution through `VaultService`, with bounded normalized
  resolved/missing/ambiguous/unsafe outcomes
- immutable exact-name candidate snapshots reusable across one bounded live scan
- no note reads/writes, relationship persistence, semantic-index access, or protocol behavior

### `services/markdown_links.py`

- immutable written destination, fragment, label, and optional canonical resolution metadata
- internal immutable source positions for cross-dialect composition without changing legacy
  equality or representation
- bounded inline-link scanning with fenced-code, inline-code, image, external, and non-Markdown
  exclusions
- exact source-relative resolution through `VaultService`, without percent decoding, alias/title
  lookup, filesystem access in the parser, or full CommonMark claims
- no note reads/writes, relationship persistence, semantic-index access, or protocol behavior

### `services/relationships.py`

- verified source-note reads through `VaultService`
- immutable dialect-specific outgoing relationship occurrences in deterministic source order
- unchanged VB-100 wikilink target/heading/alias behavior plus additive VB-113 Markdown
  destination/fragment/label behavior
- target-verified live backlink scans over deterministic canonical `VaultService` enumeration
- exact dialect-specific backlink deduplication while preserving canonical source-path order and
  source relationship order
- additive immutable normalized occurrences combine both dialects from one source snapshot in true
  document order with explicit origin, note-link type, resolution reason, and minimal empty origin
  metadata; normalized backlinks require verified canonical target equality and preserve origin
- public REST/MCP relationship adapters continue to call only the existing wikilink-backed methods;
  the normalized domain view is not publicly exposed
- no note writes, persistence, semantic-index access, or protocol behavior

### `api/relationships.py`

- protected `GET /api/v1/notes/links` and `GET /api/v1/notes/backlinks` adapters only
- explicit safe response schemas over the injected `RelationshipService`
- no unversioned aliases, relationship derivation, writes, or persistence

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

### `services/shared_filesystem_watcher.py` (private VB-151 E5)

- inert construction; validates exact registry/owner/root bindings before starting
  one process-owned Observer with one handler per enabled root (at most eight)
- installs a nonblocking 256-event `EventQueue` before native emitters exist;
  adjacent duplicates retain watchdog semantics, while rejected events retain
  no alternate backlog. The separate qualified reservation limit remains 2,000
  per owner / 10,000 globally across debounce, pending and in-flight work
- the watchdog 6.0.0 adapter is private to E5 and supports the verified native
  Windows and Linux observers; other versions/platforms fail before monitoring.
  Windows reads have a fixed native byte-buffer size. Linux additionally bounds
  each native read batch, move-pairing queue and move-cookie cache to 256 entries;
  grouped batches contain at most 256 entries (each with at most two events).
  Recursive native event synthesis streams into the bounded batch. Native watch
  metadata and directory enumeration still scale with the watched filesystem,
  rather than the number of incoming events
- ingress loss uses the exact registered watch object to select its authorized
  binding, independent of either rename path. Unknown watch identity marks all
  potentially affected enabled bindings. Windows empty native reads and Linux
  `IN_Q_OVERFLOW` also create owner-local E4 full-sync debt. Repeated loss updates
  existing E4 flags without waking an already indebted paused failure or adding
  automatic retries; reconciliation still runs exclusively in E4/E3. Linux OS
  loss also refreshes native recursive watch registrations so a lost directory
  creation cannot silently remove future event coverage after reconciliation
- Windows FILE_ACTION_REMOVED has no former-object type and watchdog converts
  directory departures to FileDeletedEvent with is_directory=False. E5 records
  owner-local full debt before passing removals/moves to the unchanged Markdown
  interpreter. Only established excluded subtrees/semantic storage are filtered
  lexically; filename extensions, temporary-looking names and replacement path
  existence never prove an ambiguous removal irrelevant. Directory entries also
  reconcile descendants. Native OLD_NAME records retain one coalesced owner-local
  trigger per bounded batch even when watchdog cannot pair a later NEW_NAME.
  Existing E4 full_needed/full_again flags preserve departures during an older
  full without another event backlog, directory cache, crawler or retry worker
- Linux runtime reader/registration errors, invalid permission-denied watch
  descriptors and root loss latch an owner-local `monitoring_error_type` in the
  process-owned scheduler. Reader exit closes descriptors and the delayed queue;
  its dependent emitter wakes and stops, while independent owners keep watching.
  Failure notification is synchronous and bounded, with no notification queue
  or replacement monitoring thread. Repeated notifications do not retry syncs
- `SchedulerStatus.monitoring_failed` distinguishes failed monitoring from E3
  progress/error state. Effective `requires_full_sync` stays true while the
  monitoring latch is set, even after successful full indexing; readiness uses
  ordinary E4 work flags, so the latch cannot cause an automatic retry loop.
  Stop, cancellation and reconciliation do not clear it. Explicit recovery means
  stop/join the old watcher, construct/start a fresh watcher on the same bindings,
  verify all native producers are alive, then reconcile the unmonitored interval.
  Latch clearing uses a revision fence so a concurrent new failure is preserved;
  an existing watcher instance never restarts itself or allocates a replacement
- native callbacks carry the watcher's exact source token. Notifications from a
  detached generation cannot change a reconstructed watcher's failure or recovery
  state. Windows unexpected emitter/read exceptions latch failure once and release
  the native handle; intentional cancellation remains a normal stop. Handle
  retirement waits for the active read to acknowledge cancellation, repeating
  CancelIoEx across the read-entry race with a five-second checked boundary
- E5 Windows handle disposal uses checked CloseHandle, retaining the directory
  handle on failure rather than watchdog's exception-suppressing close helper.
  The handle lock serializes cancellation, completed I/O/event disposal and
  directory closure; only successful closure clears the owned reference. Failed
  cleanup retains generation ownership and can be explicitly retried, including
  a failed emitter removed by watchdog during startup. ERROR_INVALID_HANDLE makes
  ownership uncertain and forbids blind retry of a potentially reused HANDLE
- shared Windows emitters use FILE_FLAG_OVERLAPPED and one fixed 64,000-byte
  buffer/manual-reset event per native directory handle. Readiness is signaled
  only AFTER ReadDirectoryChangesW acknowledges native request submission, never
  before the call or from thread/handle liveness. Startup waits at most five
  seconds per required producer, with cancellation/shutdown/token checks, then
  revalidates roots and producer/dispatcher liveness before publishing restored
  coverage. Reconciliation for that revision starts only after this boundary.
  GetOverlappedResult consumes completion on the same emitter thread; cancellation
  drains native ownership before buffer/event/handle disposal. No probe file,
  additional worker or unbounded request collection is used
- Linux masks explicitly include `IN_MOVE_SELF` and `IN_UNMOUNT` alongside existing
  delete/ignored signals. Root invalidation terminates the affected producer, even
  if the path has been recreated or the original inode moved back. Native batches
  and recursive registration reuse E1/E3 directory identity and exact owner/root
  validation; a replacement path or symlink cannot authorize a new binding
- the existing shared dispatcher independently checks every enabled root once per
  second, even while ingress and debounce are idle. After verified native startup,
  the same check verifies the Observer dispatch, each exact watch's emitter and
  its required native reader/readiness. A dead shared Observer invalidates all
  served owners; a dead emitter/reader invalidates only its registered owner.
  Startup still arming and requested shutdown are not unexpected runtime failure.
  Checks run outside scheduler
  locks and reuse E1 ordinary ancestor-directory identity and E3 owner/root
  identity validation; no vault scan or extra worker is added. Detection latency
  includes filesystem and dispatcher scheduling time. Failed checks publish only
  against the still-current source token/monitoring revision, latch owner-local
  failure and invalidate coverage. That owner stops accepting targeted events;
  healthy owners continue. Full sync alone cannot clear the failure
- Linux recursive registration tolerates only a proven absent child beneath a
  revalidated authorized root. It retires any partial subtree watches and maps,
  requests coalesced index reconciliation and keeps the native reader running.
  Existing replacements, symlinks, permission/resource errors and root loss remain
  fatal. Native registration uses directory-only/no-follow flags; there are no
  registration retry loops or additional event buffers
- the shared Linux native mask explicitly retains MODIFY, ATTRIB, CLOSE_WRITE,
  CREATE, MOVED_FROM/TO, DELETE, DELETE_SELF, MOVE_SELF and UNMOUNT. Read-only OPEN
  and CLOSE_NOWRITE are excluded: recursive traversal must not overflow its own
  kernel queue. The same mutation mask applies to initial registration, new
  children and overflow restoration. Kernel OVERFLOW and IGNORED records remain
  handled independently of requested notification bits; the legacy watcher is
  unaffected
- incomplete Linux overflow restoration is a distinct coverage revision, exposed
  as `SchedulerStatus.monitoring_recovering`. Detection marks degradation BEFORE
  requesting E4 debt. Registration and root validation run outside the scheduler
  condition; only their successful completion marks that revision restored and
  requests its post-restoration full sync. A full turn captures the watcher token
  and restored coverage revision BEFORE E3 begin/discovery. Completion can publish
  reconciled coverage only if both still match, coverage remains restored, the
  current native generation is verified against its failure revision, and
  admission remains open. Earlier or unrelated
  syncs cannot clear a newer gap. Overlapping revisions and duplicate restoration
  notifications coalesce; a failed restoration stays visibly failed/degraded.
  Readiness still uses ordinary E4 work flags, so unresolved coverage cannot spin
  automatic retries or create another synchronization worker
- first native attachment records coverage debt before scheduling/starting native
  producers, even if E4 already finished its startup sync. Initial Linux recursive
  registration uses the same native FIFO fence as restoration, including queued
  subtree work. Verified readiness and matching post-coverage full reconciliation
  are both required; initial zero counters are not proof. Repeated readiness for
  an already verified generation is idempotent. Pre-verification callbacks retain
  coalesced debt rather than normal targeted admission
- reconstruction snapshots monitoring, fatal-failure and coverage revisions
  separately. Legitimate startup subtree gaps may advance monitoring/coverage
  without invalidating verified native startup. Native fence tickets also carry
  the fatal-failure revision, so a newer failure invalidates older proof. A fence
  may be recorded before startup verification, but cannot authorize healthy
  completion alone. Pending subtree work still needs its matching fence; no
  reconstruction call fabricates coverage for a newer gap. The historical failure
  remains latched until matching full reconciliation succeeds. Writes during that
  recovery retain coalesced debt; a new fatal failure or shutdown invalidates the
  verified-generation receipt. No new worker or automatic retry loop is added
- Linux restoration walks do not themselves publish coverage. The reader adds
  and immediately removes a private inotify watch on its existing kill-pipe inode
  through `/proc/self/fd`, producing an IN_IGNORED fence on the same ordered kernel
  stream. Earlier directory-create/move records must be processed first. Subtree
  registration advances a native registration revision; work after a fence was
  queued invalidates it and requires one replacement fence after that work. Only
  a matching fence, after the whole current batch, may publish restored coverage
  and admit its authoritative full reconciliation. Later subtree gaps start a new
  owner-local coverage revision, even after an earlier full sync completed
- native fence state is scalar and has at most one current marker, with no extra
  fd, thread or deferred registration queue. The reader never waits for its own
  marker while holding its read lock. Overflow restarts the fence revision;
  failure to establish a fence (including unavailable procfs/watch resources)
  fails monitoring visibly. An empty Python queue is never coverage evidence
- initial recursive registration and create synthesis share directory-symlink
  exclusion: only an explicitly observed, unregistered child link below validated
  ordinary parents is skipped. A previously watched ordinary directory replaced
  by a link, a substitution after exclusion checks, unsafe parents or root changes
  still fail closed. Exclusions never register or traverse an external target
- both native recursive registration paths share strict streaming scandir traversal;
  os.walk is not used because it hides DirEntry classification errors. Opening and
  advancing iterators, no-follow stat, is_symlink and is_dir errors propagate.
  Classified directory identity is checked again before descent; active iterators
  close on traversal or native-registration failure. Only one iterator per depth
  and singleton entry batches are retained, with no copied tree or work queue. EACCES,
  EPERM, EIO and uncertain coverage fail monitoring before a fence can publish
  restoration. Only ENOENT for an unregistered, proven absent child beneath the
  same validated parent may be skipped; no parent-identity evidence means failure.
  Explicit validated reconstruction and matching reconciliation are required
  after enumeration failure, just as after native registration failure
- Linux native initialization mirrors the version-checked watchdog 6.0.0 fd,
  kill-pipe, poll/select and move state, but replaces its unchecked isdir/file
  decision with strict no-follow root stat/identity validation and unconditional
  directory registration. Root metadata errors cannot select root-only coverage.
  Failed allocation/registration closes partial resources before startup rollback
- native parsing rechecks closed state while holding the same per-reader lock
  that owns descriptor closure. A buffered batch abandoned before parsing cannot
  register against closed descriptors; parsing already in progress retains that
  lock until registration completes. Expected close-related EBADF is ignored only
  after verified closure; active native errors remain fatal. Clean stop still
  records its coverage interruption before surrendering monitoring
- Linux overflow recovery retires every prior kernel watch before strict
  registration of the current authorized tree, including the root watch. A
  same-path lookup never certifies an old descriptor's filesystem identity.
  Retirement removes both map directions and discards retained events in the
  native batch. A scalar high-water check for newly allocated watches AND private
  fences prevents pending retired records from acquiring a new path/fence meaning;
  descriptor wrap/reuse fails closed and requires explicit reconstruction.
  One prior path map is retained only during the walk for ordinary-to-symlink
  exclusion checks, then released. Required registrations and retirements precede
  the ordered native fence and matching E4 full; no obsolete watch history grows
- Linux directory moves/deletions immediately retire old subtree watches and
  discard buffered events under those old names, then reuse strict root coverage
  restoration and the existing native fence/full protocol. This does not assume
  a MOVED_TO is in the same read buffer. Late/missing/interleaved cookies and
  internal/moved-in subtrees rebuild current authorized path mappings; watchdog's
  bounded delayed queue still pairs presentation events. No directory-cookie
  history or expected-IGNORED collection is retained. Retired unknown IGNORED
  records cannot complete the separately identified private fence. A proven
  concurrent departure under an unchanged parent may be retired during that known
  move/delete restoration; ordinary registration and overflow enumeration retain
  their fail-closed classification policy
- Linux cleanup tracks each inotify/kill-pipe descriptor's disposition separately.
  Complete means all owned resources are closed, verified absent, or verified
  displaced from their original slot. Independent descriptors are processed even
  after an error. One original inotify duplicate and at most two additional
  close-time guards preserve identity evidence across uncertain closes (at most
  six owned fd records per root). On verified x86-64 kernels, optional self-kcmp
  proves a safe retry's open-file-description identity; unavailable/uncertain
  proof retains ownership instead of guessing. Normal successful cleanup does
  not require kcmp. Partial native construction remains reachable even when
  watchdog removes the failed emitter. Native resources/threads must all resolve
  before generation ownership is released; retries cannot close reused slots
- reuses `FilesystemEventInterpreter`; create/modify/delete/move events preserve
  their exact authorized space, including both eligible rename paths
- one monotonic dispatcher, no semantic executor or eager model loading; disabled
  owners receive no watcher work; public named composition remains deferred to E6
- E4 owns debounce deadlines as reservations under its existing condition lock;
  debounce, queued and in-flight paths share the same 2,000-per-space and
  10,000-global distinct-identity caps, including concurrent direct submissions
- expiry transfers one reservation atomically into E4 pending work; duplicates
  take the maximum existing/candidate monotonic deadline under E4's condition
  and trigger recovery without consuming another slot. Validation crossing an
  owner reservation-removal revision falls back to full debt instead of reviving
  a removed target; this can conservatively reconcile an unrelated concurrent
  event in the same owner, without retaining per-path timestamp history;
  filesystem/root revalidation runs outside the coordination condition
- overflow retains no additional path and requests coalesced full-sync debt;
  delivery failures convert the affected accepted reservation into that owner's
  debt, with no automatic retry loop; full reconciliation absorbs prior pending
  and debounced work, retaining later writes and clearing debt only on success
- stop fences admission, stops/joins the observer, stops/joins the dispatcher and
  converts residual debounce reservations to full debt without flushing syncs;
  startup failure rolls back registrations and threads, and stop is idempotent
- admission fencing records a new coverage revision for every binding whose
  monitoring is surrendered, including a clean watcher-only stop with no queued
  events. This evidence does not itself schedule retries. A full sync while
  stopped cannot reconcile that revision; explicit reconstruction verifies both
  the dispatcher and native producers before requesting a post-restoration full
  sync. Reconstruction is one explicit wakeup for paused recovery work
- unexpected shared dispatcher termination latches monitoring failure for all
  bindings it serves before fencing admission and retiring reservations. It
  retains native generation ownership; ordinary reconciliation cannot clear the
  latch. No replacement thread or automatic restart is created
- shutdown requested and cleanup completed are separate boundaries. The generation
  token is released only after the Observer, dispatcher and original bounded set
  of native emitters/readers are joined and native resources closed. A timeout or
  cleanup error keeps the token and lifecycle references for explicit stop retry;
  replacement activation remains rejected until verified cleanup succeeds
- before stopping native producers, stop seals and drains ingress into exact
  owner debt. Producers never wait for queue capacity, including during shutdown;
  watchdog's stop sentinel can still wake an empty queue. Stop waits for active
  interpreter/native operations at their existing safe boundaries, with the
  existing five-second observer/dispatcher join checks; it cannot interrupt a
  blocked filesystem call or arbitrary handler, and does not claim a hard
  wall-clock deadline for such operations
- callbacks record bounded per-owner counters before interpreter or filesystem
  validation. Admission fencing marks active callbacks as owner-local debt;
  rejection/completion across the fence resolves debt before decrementing the
  counter. Effective recovery stays true for unresolved fenced callbacks even
  if E4 finishes a full sync meanwhile. A new watcher cannot attach while an old
  callback is unresolved. Queued events, active callbacks, debounce and E4
  transfers therefore retain owner-local accounting during watcher-only stop;
  process shutdown stays terminal and none of these notifications can reopen it
- process shutdown calls `SharedSemanticScheduler.begin_shutdown()` first to
  reject work and signal every owner, then watcher `stop()`, then scheduler
  `shutdown()` to drain only the active safe boundary; E6 owns production wiring

The legacy watcher, application construction/lifespan, public adapters, storage
schema and semantic scoring contracts are unchanged. Both watcher paths retain
the existing cooperating-filesystem guarantees; events are hints and never an
authorization to delete indexed rows without E3's final validation.

### `services/semantic_search.py`

- query embedding
- candidate scoring, including an additive caller-supplied eligible canonical-path set applied before
  chunk scoring, per-note aggregation, relative-floor selection, and truncation
- hybrid reranking
- result aggregation
- bounded compatible ready / previous-refresh / previous-error query-basis reporting
- full-vault and targeted-path synchronization orchestration

### `services/knowledge_query.py`

[ADR 0006](docs/adr/0006-bounded-knowledge-query-capability.md) is implemented by one domain-only
orchestration boundary over the existing owners. `VaultService` remains responsible for live
canonical paths and reads; the frontmatter/portable-field layer remains responsible for metadata
and tags; `RelationshipService` remains responsible for normalized relationship facts; and
`SemanticSearchService` remains responsible for semantic availability and the established hybrid
ranking.

The boundary accepts only a finite immutable request: optional semantic and exact literal text,
recursive folder and bounded exact-path scope, required exact portable tags, bounded top-level
metadata predicates, bounded resolved relationship predicates, and a visible result limit. All live
constraints establish an eligible canonical-path set before optional semantic selection. Results
are immutable verified canonical paths with deterministic canonical ordering or unchanged semantic
score ordering and a bounded semantic-index-basis fact.

The runtime starts from one immutable `VaultService` candidate snapshot, applies exact folder/path
scope, reuses one current candidate read for literal/frontmatter/tag/metadata/outgoing predicates,
and reuses one `RelationshipService` resolution snapshot. Incoming predicates derive each fixed
source once rather than scanning backlinks per candidate. Expected stale/unreadable candidates are
omitted conservatively and final paths are reverified without claiming an atomic vault snapshot.

The service is wired only into the read-only CLI `query` adapter for its bounded subset. It is not
wired into application construction, REST/OpenAPI, MCP, or the dashboard. It adds no public schema,
persistence, cache/query/relationship index, dependency, write, model/chunking/index-signature, or
ranking change.

### `repositories/semantic.py`

All SQLite persistence for semantic metadata/chunks/index state.

VB-151 E1 adds an optional immutable private expected binding to internal repository
construction. Non-default spaces use the existing `meta` table with exactly
`knowledge_space_binding_version='1'`, `knowledge_space_id=<configured SpaceId>` and
`knowledge_space_root_sha256=<SHA-256 hex of UTF-8 os.path.normcase(str(strict_resolved_root))>`.
The digest detects relocation; it is neither authorization, note identity nor scoring evidence,
and never appears in public results. Default and unbound legacy repositories retain their
existing storage/signature behavior without mandatory binding keys.
Bound semantic services accept only the exact native source-root representation assigned by
that immutable repository binding. Construction and reconfiguration reject different roots or
alternate spellings before model/storage work or configuration mutation; lifecycle, preparation
and embedding recheck the effective source root, including changes made outside reconfiguration.
Same-root configuration changes remain supported, and legacy unbound reconfiguration is unchanged.

Bound lifecycle operations inspect storage without writable preparation or model work first,
then revalidate under a writer transaction before binding/signature writes or derived resets.
Only missing storage or a recognized empty unbound schema may initialize; both notes and chunks
must be empty. All three keys commit atomically with initialization. Partial, malformed,
unknown-version, contradictory, populated unbound or unrecognized/corrupt storage fails closed;
there is no automatic adoption or destructive recovery. Authorized signature changes/reset keep
binding metadata. Binding keys cannot be changed through the metadata setter. Root, directory,
database and sidecar checks reject aliases and observed replacements, within the existing
cooperating-filesystem model rather than a promise of hostile-writer isolation.

Bound lifecycle preflight conservatively requires stopped storage without WAL/SHM/journal
sidecars, so immutable inspection cannot ignore newer binding metadata or modify coordination
files. During indexing, embedding validates binding on the already guarded, repository-issued
transaction, including database/root identity and current transaction metadata; its own rollback
journal does not cause an external preflight. Foreign, inactive or cross-thread sessions cannot
use this path. Failed first initialization closes SQLite before removing only its exclusively
created, identity/owner-checked empty database without sidecars. Uncertain ownership or residual
data leaves storage untouched and unavailable; existing databases are never cleanup candidates.
D1 instead validates binding inside its bounded read transaction before chunk payloads,
charging metadata length preflights/payload bytes and SQLite instructions to the shared budget.
Stopped storage first receives bounded immutable rejection-only screening to avoid creating
WAL/SHM for an invalid namespace/schema; that screening never authorizes returned data.
D1 independently revalidates under normal SQLite read locking, establishing one snapshot before
binding metadata and retaining it through chunk loading, with WAL-aware SHM coordination.
The required chunks path index must be full, not partial; pre-existing sidecar identities are checked
again before accepting a snapshot. Immutable hygiene never initializes or repairs binding.
Bound full-chunk, metadata and live-status reads likewise validate binding and materialize results
in one normally locked read-only transaction, supporting rollback-journal and WAL snapshots.
Immutable reads remain limited to rejection-only screening and explicit stopped-service inspection.
Schema preflight also requires the existing single-column primary keys (including the chunks integer
rowid key) and the chunks.path-to-notes.path foreign key with ON DELETE CASCADE. Incompatible storage,
including constraint-free CREATE TABLE AS SELECT replacements, is rejected without repair or migration.
Named owner composition remains inspection-only; E2-E6 resource/lifecycle/runtime activation
and operator migration/rebinding are not implemented by E1.

### `schemas/`

Pydantic HTTP request/response models only.

### `api/`

FastAPI routers, versioned route registration and dependencies. Routes should orchestrate services
rather than implement domain logic. Legacy and current version paths must converge on the same
endpoint function and service path.

### `mcp_server.py`

Shared MCP tool/Resource registration plus the explicit stdio composition root. Stdio injects
existing read services, applies a one-identity process-wide fixed-window operation budget, reserves
stdout for MCP traffic, emits allowlisted structured diagnostics to stderr, and starts no index
writer.

### `mcp_http.py`

Opt-in Streamable HTTP composition for the existing FastAPI process. It wraps the official SDK ASGI
app with the shared Bearer verifier and application limiter, passes typed Host/Origin allowlists to
`TransportSecuritySettings`, and injects the live application services without adding domain or
index ownership.

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
- embedding backend contract and effective ONNX/tokenizer artifact fingerprint
- persisted lifecycle state (`uninitialized`, `indexing`, `ready`, `error`)
- explicit per-sync progress such as current note, percentage, batch and ETA (planned)
- last successful full synchronization

The index is **derived data**. Migrations should be used when cheap; otherwise a safe automatic
rebuild is acceptable. A content-contract or embedding-fingerprint signature change clears
incompatible notes/chunks and rebuilds them from Markdown without a SQLite schema migration. A
targeted refresh that discovers an older signature performs the required full rebuild before it can
restore the index to `ready`, so embedding generations are not mixed. Missing signature metadata is
never adopted when derived rows already exist.

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

### Knowledge Query domain flow

```text
finite immutable request
  → bounded validation through existing owners
  → deterministic live canonical-path enumeration and verified reads
  → live literal / tag / metadata / normalized-relationship eligibility
  → optional unchanged SemanticSearchService ranking of eligible indexed notes
  → bounded final live-path verification
  → deterministic immutable result
```

Markdown facts are live and authoritative; compatible semantic data may be older. A compatible
previous index may remain searchable during refresh or after a failed refresh, while a semantic
request fails explicitly when no compatible searchable index exists. VaultBridge claims no atomic
vault-wide filesystem snapshot: changed, unreadable, missing, oversized, invalid-UTF-8, or unsafe
notes are omitted conservatively, and bounded candidate evaluation may return fewer than the
requested limit. Non-semantic results sort by `(path.casefold(), path)`; semantic results retain the
existing final-score, semantic-score, lexical-score, and canonical-path tie-break chain.

### Verified backlinks

```text
requested target path
  ↓
exact live Markdown verification through VaultService
  ↓
deterministic canonical live-note enumeration + one VB-100 resolution snapshot
  ↓
VB-101 outgoing derivation for each source note
  ↓
canonical resolved-target equality
  ↓
stable, exact-deduplicated backlink metadata
```

The scan reads every eligible live source through `VaultService` and performs no raw-text backlink
matching. Results are ordered by canonical source path and then source occurrence order. An exact
duplicate has the same canonical source path, written target, heading, and display alias; only that
duplicate collapses, so materially distinct link metadata remains visible. Expected source size or
encoding failures and unexpected read failures remain explicit rather than becoming partial or
empty results. The candidate snapshot is scan-local and immutable; no graph data is persisted.

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

Current VaultBridge is allowed to access one configured vault root and its own data directory.
Accepted ADR 0009 describes future separately contained space bindings; it grants no current
multi-root access or permission to accept caller-supplied host paths.

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
