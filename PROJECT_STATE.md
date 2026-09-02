# Current Project State

This document is the current factual snapshot for future Codex sessions. It should describe what exists **now**, not future plans.

## Baseline date

2026-09-02

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
- VB-014 — Optional filesystem watcher
- VB-015 — Rich health/readiness output
- VB-020 — Markdown heading-aware chunker
- VB-021 — Embed title + heading hierarchy + chunk
- VB-022 — Retrieval evaluation fixture
- VB-024 — Tune hybrid ranking from evaluation data
- VB-030 — Duplicate candidate service
- VB-031 — Verified related-note suggestions
- VB-040 — Structured JSON logging
- VB-041 — Request IDs and latency logging
- VB-042 — API key rotation
- VB-043 — Lightweight rate limiting
- VB-044 — Liveness and readiness endpoints
- VB-045 — Index integrity/rebuild CLI
- VB-050 — Introduce `/api/v1`
- VB-051 — Add VaultBridge CLI
- VB-052 — Generic Docker deployment docs
- VB-053 — TrueNAS deployment docs
- VB-054 — Publish GHCR image workflow
- VB-056 — GitHub v1.0 release checklist
- VB-057 — Enforce symlink containment in vault enumeration
- VB-058 — Fix cross-platform path assertion
- VB-059 — Align v1.0 version metadata
- VB-060 — Public repository exposure audit
- VB-070 — Web Dashboard architecture and security design
- VB-071 — Dashboard shell and authenticated session
- VB-072 — Dashboard overview and health visibility
- VB-073 — Dashboard search interface
- VB-074 — Dashboard usability, accessibility and release hardening
- VB-080 — TrueNAS Community App packaging design

Post-v1 development position:

- stable `v1.0.0` is released and all v1.0 release gates are complete
- optional and future backlog work continues independently of the completed v1.0 release

Current release task:

- **VB-075 — Prepare, publish, and verify the dashboard-capable VaultBridge `v1.1.0` image**

Current post-v1 planning position:

- ADR 0003 is accepted and VB-071 is complete; a bundled schema-hidden dashboard shell is served at
  canonical `/ui/` with explicit local assets and no SPA catch-all
- the shell has accessible Overview, Search, API / Integration, and About navigation; Overview loads
  the existing public `/health` facts automatically and Search calls the existing protected
  `/api/v1/notes/search` and `/api/v1/notes/related` routes
- Overall status, Vault, Semantic index, and Background indexing cards distinguish lifecycle state
  from search availability, format counts/timestamps safely, and provide manual refresh plus explicit
  loading, unavailable, and malformed-response states without polling or maintenance controls
- public Overview requests never send the dashboard Bearer credential and remain independent of
  locked, unlocked, invalid-key, and logout session states
- unlock and reload revalidation call the existing protected `GET /api/v1/notes/list?limit=1` route;
  a validated key is stored only as `vaultbridge.ui.apiKey` in `sessionStorage`, while logout and
  `401` clear it
- strict UI-only CSP, `nosniff`, no-referrer headers, same-origin document-relative requests, and
  text-only dynamic rendering provide the initial browser security boundary
- Literal and Semantic modes preserve backend order and existing result fields; Search adds no
  frontend ranking/filtering, duplicate workflow, or index mutation
- search results can open the complete note through the existing protected `/api/v1/notes/read`
  route in a safe read-only text viewer; stale note reads are suppressed, focus returns to the
  originating result, and logout or `401` clears protected note content
- queries, folders, results, snippets, paths, and scores are not persisted or placed in URLs;
  logout and `401` clear form/results, while `429` and semantic `503` retain the validated session
- VB-074 hardens keyboard/focus behavior, error associations, score labels, stale-request handling,
  responsive reflow, long-content wrapping, contrast coverage, and reduced-motion behavior without
  adding editing/index mutation or TrueNAS-specific UI behavior
- local automated checks and a real Chrome audit passed with disposable synthetic data; connected
  Chrome screenshot capture remained unavailable, so no screenshot artifact is claimed
- the normal Dockerfile image passed the disposable TrueNAS Phase B gate for health, bundled assets
  and headers, authenticated literal/semantic retrieval, CLI, safe logs, restart persistence, clean
  stop, and complete cleanup without touching production data or services
- existing API contracts, authentication, rate limiting, CLI behavior, dependencies, Dockerfile,
  and Compose definitions are unchanged by dashboard work through VB-074
- Milestone 8 is complete; `v1.1.0` is published and its exact OCI index is anonymously pullable
- VB-080 is complete as a version-neutral packaging design in
  `docs/TRUENAS_COMMUNITY_APP_DESIGN.md`, based on current `truenas/apps` contributor conventions;
  it did not implement a catalog definition, select a release, or perform publication
- VB-081 source is production-image-capable: `ix-dev/community/vaultbridge/` pins
  `ghcr.io/mrtrollex/vaultbridge:1.1.0`, uses matching `app_version`, and has basic/watcher/host-path-
  data fixtures that inherit the released image map with synthetic credentials and paths
- current upstream `truenas/apps` commit `354e87006de2f49a8a2cd655bce857984a7bd247` still uses library
  `2.3.11`, repository-plus-tag images, and initial package `version: 1.0.0`
- official Docker-backed rendering/deployment, generated library/hash/catalog artifacts, the final
  TrueNAS CDN icon URL, and live TrueNAS lifecycle evidence remain open
- no upstream TrueNAS Community App exists yet; no submission, merge, or Discover availability is claimed
- current TrueNAS support remains the documented Docker/source-built Custom App deployment
- VB-023 remains open P1 retrieval work with unchanged scope, but it is no longer the current next task
- VB-032 and VB-033 remain deferred optional future work
- VB-055 remains optional and is not a prerequisite for the planned dashboard

Current `v1.1.0` release status:

- `v1.1.0` is published from source commit `e39ed91db75f912f390c7ec915dea73369bb9252`
- GitHub Actions publish run `33641163374` generated aliases `1.1.0`, `1.1`, `1`, and `latest` in
  `ghcr.io/mrtrollex/vaultbridge`
- OCI index `sha256:753e613617d221c3dac311600a36cab3f2727b09f630321664eaa7b7ad6eb48c`,
  `linux/amd64` manifest `sha256:62ddad69cf3e4af632d40c3bcdb8f9e601fa7c04009616350f9ab706a4171e92`,
  and attestation `sha256:744fe630075b91cc576a18b37012c4d37d0b551b17a74cab8088a1448e5fc4a3`
  are recorded
- anonymous exact-index pull with an empty temporary Docker config passed; platform and required OCI
  source/revision/version/license labels matched the supplied evidence
- VB-075 remains incomplete because separate exact-source CI job evidence and its full exact-image
  dashboard/API/CLI/semantic/persistence/safe-log/cleanup runtime gate were not supplied
- VB-082 has not started; no real TrueNAS Community App lifecycle validation has run
- VB-083 remains blocked on successful VB-082; no upstream pull request, acceptance, or Discover
  availability exists

Current v1.0 release status:

- stable `v1.0.0` was published from source commit
  `1a430996c9db331f448339d233e940d7aa7b3b6d`; exact-main CI run `32932765995` passed both `python`
  and `docker`
- Git tag `v1.0.0` identifies the stable, non-prerelease GitHub Release
  [VaultBridge v1.0.0](https://github.com/mrtrollex/VaultBridge/releases/tag/v1.0.0)
- GHCR publish workflow run `32932955416` passed `Verify release source` and `Build and publish`;
  `ghcr.io/mrtrollex/vaultbridge` is public and linked to `mrtrollex/VaultBridge`
- stable aliases `1.0.0`, `1.0`, `1`, and `latest` all resolve to OCI index
  `sha256:0b6734d485df83326cac0ea394b17aaa86fde0be4f7ad7dca9ca49cb531ead71`
- the verified `linux/amd64` runtime manifest is
  `sha256:577522d36484888de6620cd168d9129c7c9ecbb44d1be35260956ca54ecc6791`; OCI revision, version,
  source, and MIT license labels matched the stable release
- anonymous exact-digest TrueNAS smoke testing passed startup, liveness, readiness, authenticated
  `/api/v1/notes/list`, safe-log, and disposable-cleanup checks
- all `v1.0.0` release gates are complete
- the public repository history rewrite remains part of the release history; the final stable source
  above is the authoritative release commit
- the historical `v1.0.0-rc.1` candidate was retired after that rewrite; its GitHub Release, Git
  tag, GHCR package, old commit, workflow, digest, and runtime verification are not current release
  evidence
- `v1.0.0-rc.2` remains verified historical prerelease evidence, distinct from the final stable
  release evidence

Current milestones:

- **Milestone 5 — Operational maturity and security (complete)**
- **Milestone 6 — Public API and developer experience (complete)**
- **Milestone 7 — Distribution and `v1.0.0` (complete)**
- **Milestone 8 — Web Dashboard / operator experience (complete)**
- **Milestone 9 — TrueNAS Community App distribution (in progress; production image pinned, official package/live gates open)**

## Working production characteristics

- FastAPI application
- package and FastAPI application metadata aligned to released `v1.1.0`
- tracked source and reachable remote branch history passed the VB-060 public-exposure audit
- public GitHub source was anonymously cloned and clean-built on TrueNAS SCALE / Linux amd64 with
  Docker Engine 28.3.1 using a disposable empty vault and isolated port `8876`
- the clean-install smoke test returned HTTP 200 from `/health/live` and `/health/ready`, and
  authenticated `GET /api/v1/notes/list?limit=5` returned an empty note list
- Python 3.12 container
- FastAPI routes, vault operations, semantic orchestration and SQLite persistence have separate modules
- typed runtime settings via `app/core/config.py`
- standard-library JSON application logging via `app/core/logging.py`
- context-local HTTP request correlation and latency events via `app/core/observability.py`
- bundled same-origin `/ui/` dashboard with local HTML/CSS/vanilla JavaScript, a public health-backed
  read-only Overview, protected literal/semantic retrieval, strict browser security headers, and
  operator-supplied Bearer-key validation through the existing protected API
- required current `API_KEY` plus one optional secret-safe `API_KEY_PREVIOUS` allow a controlled
  two-key rotation window through the shared constant-time legacy/v1 Bearer verifier
- protected legacy and `/api/v1` traffic has a thread-safe, process-local fixed-window limiter before
  authentication, using direct ASGI peer identity, monotonic timing, stale cleanup, and a hard
  client cap with defaults of `120` requests per `60` seconds across at most `1024` peers
- public health routes and schema-hidden `/privacy` are exempt; forwarded client-address headers are
  ignored, so reverse proxies can aggregate external clients into one bucket
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
- protected note/search operations are available under `/api/v1` with stable `*V1` operation IDs
- existing unversioned note/search paths and operation IDs remain compatibility aliases
- legacy and `/api/v1` related-note results are filtered through live `VaultService` containment and
  regular-Markdown verification, return canonical paths/titles, and boundedly backfill stale entries
- protected legacy and `/api/v1` duplicate-candidate routes compose conservative live title evidence
  with one verified semantic search, return exact-title candidates before semantic-only candidates,
  and remain advisory and read-only with no automatic merge or index mutation
- literal search, note listing, and semantic full synchronization enumerate only Markdown targets
  whose resolved paths remain under the resolved vault root, then stat/read those validated paths
- safe internal file symlinks return one canonical vault-relative path; external and broken symlinks
  are skipped without per-file logging
- standard-library local CLI with read-only `status`, literal `search`, existing-index semantic
  `related`, offline incremental `index`, and clean `reindex`; the VB-045 `index check` and
  `index rebuild` administrative commands remain supported
- successful full synchronization persists `last_successful_sync`; targeted refresh does not change it
- TrueNAS container commonly runs as UID/GID 568
- existing production deployment uses port 8765 → 8000
- separate `/vault` and `/data` mounts are used by the TrueNAS compose file
- TrueNAS Custom App deployment uses `truenas-install.yml` to include the source-tree
  `compose.truenas.yml`; TrueNAS owns the serving app lifecycle, while shell Compose is documented as
  a separate alternative that must not run alongside it
- the supported no-Git TrueNAS update path uses the existing `make-bundle.ps1` `git archive` bundle,
  preserves `.env`, and leaves the external vault and semantic-data datasets outside source extraction
- generic Docker Compose bind-mounts the host `OBSIDIAN_VAULT_PATH` at `/vault`, stores derived
  semantic data under `/vault/.obsidian-chatgpt-data`, and publishes container port `8000` only on
  host loopback at `API_PORT` (default `8765`)
- generic Docker Compose runs as configurable `PUID:PGID` (defaults `1000:1000`); the mounted vault
  must grant that identity the read/write access required by enabled note operations and indexing
- published GitHub Releases build the existing root Dockerfile and use `GITHUB_TOKEN` to publish
  `ghcr.io/<lowercase-repository-owner>/vaultbridge` after tests, compilation, and Compose validation
- exact semantic-version image tags omit the release tag's leading `v`; stable releases also update
  `major.minor`, `major`, and `latest`, while prereleases update only their exact version tag
- GHCR publication is normal Linux runner architecture only; source-build Docker and TrueNAS
  deployments remain supported and multi-architecture manifests remain VB-055
- published runtime platform remains `linux/amd64`; multi-architecture manifests remain VB-055
- historical prerelease `v1.0.0-rc.1` was retired after the public-history rewrite; its GitHub
  Release, Git tag, and GHCR package were removed and its old SHA/workflow/digest are not current
  release evidence
- historical verified prerelease `v1.0.0-rc.2` was built from release source commit
  `f1131ceec2c003d94af7f9c0a67a802ed067902d`; exact-source main CI run `32880752547` passed both
  `python` and `docker`
- GitHub prerelease `v1.0.0-rc.2` is published; GHCR publish workflow run `32881004757` passed both
  `Verify release source` and `Build and publish`
- `ghcr.io/mrtrollex/vaultbridge` is public and linked to `mrtrollex/VaultBridge`; the published
  prerelease tag is `1.0.0-rc.2`, while stable aliases `1.0.0`, `1.0`, `1`, and `latest` were not
  created
- the RC2 OCI index digest is
  `sha256:ce5c9c75e8757389fca06e3706379f4801e03312b3852d1338d82624af0a45b2`; its `linux/amd64`
  runtime manifest is `sha256:dbc23ff9b921ce3e0502e2f4d3713c359c190e16ca289e21261888a6566d3d20`
- the RC2 BuildKit provenance attestation is
  `sha256:81a78834c34d26563ad0d52497e1cef6e19fb247805c7035bee9c064ca7ab0f2`
- TrueNAS RC2 verification anonymously pulled the image by tag and exact OCI digest using disposable
  vault/data; the tag resolved to the expected digest, the platform and OCI revision/version labels
  matched, liveness, readiness, and authenticated `/api/v1/notes/list` passed, logs exposed neither
  the API key nor disposable host path, and the disposable container/data were removed
- stable `v1.0.0` source commit is `1a430996c9db331f448339d233e940d7aa7b3b6d`; exact-main CI run
  `32932765995` passed `python` and `docker`
- stable Git tag `v1.0.0` and non-prerelease GitHub Release
  `https://github.com/mrtrollex/VaultBridge/releases/tag/v1.0.0` are published
- stable GHCR workflow run `32932955416` passed `Verify release source` and `Build and publish`;
  package `ghcr.io/mrtrollex/vaultbridge` is public and linked to `mrtrollex/VaultBridge`
- stable aliases `1.0.0`, `1.0`, `1`, and `latest` all resolve to OCI index
  `sha256:0b6734d485df83326cac0ea394b17aaa86fde0be4f7ad7dca9ca49cb531ead71`
- the stable `linux/amd64` runtime manifest is
  `sha256:577522d36484888de6620cd168d9129c7c9ecbb44d1be35260956ca54ecc6791`; the BuildKit provenance
  attestation is `sha256:1b1c0596963f5c1ab56e628b9faecf39b8943d304ce46263ed47ff2e5ffe8aec`
- stable OCI metadata verified revision `1a430996c9db331f448339d233e940d7aa7b3b6d`, version `v1.0.0`,
  source `https://github.com/mrtrollex/VaultBridge`, license `MIT`, and platform `linux/amd64`
- final TrueNAS stable verification anonymously pulled the public image by exact OCI digest using
  disposable vault/data; the pull, platform, revision/version, startup, `/health/live`,
  `/health/ready`, authenticated `/api/v1/notes/list`, safe-log, and disposable-cleanup checks passed
- all stable `v1.0.0` release gates are complete
- stable `v1.1.0` release source, workflow, aliases, OCI/runtime/attestation digests, required labels,
  platform, and anonymous exact-digest pull are recorded above; VB-075's remaining functional
  release gate is explicitly separate

## Current implementation boundaries

```text
app/main.py
    application construction, dependency wiring, router registration and lifespan management

app/cli.py
    read-only semantic integrity inspection and explicit offline rebuild orchestration

app/api/
    HTTP routers, legacy/v1 route registration, and API dependencies

app/ui/
    explicit dashboard document/asset routes and bundled HTML, CSS and vanilla JavaScript shell
    with `app.js` session/navigation ownership, `overview.js` public health presentation, and
    `search.js` protected literal/semantic retrieval presentation

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

app/services/filesystem_watcher.py
    optional recursive Markdown event watching and monotonic debounce dispatch

app/services/rate_limiter.py
    bounded process-local fixed-window request allowance with injectable monotonic time

app/repositories/semantic.py
    SQLite semantic persistence, lifecycle-state storage and read-only status statistics

app/semantic.py
    compatibility facade retained for pre-VB-005 internal API compatibility
```

Docker deployments set Hugging Face's `HF_HOME` to persistent derived storage rather than a home
directory: `/vault/.obsidian-chatgpt-data/huggingface` in generic Compose and `/data/huggingface` in
TrueNAS. Existing non-root `PUID:PGID` and `568:568` runtime identities remain unchanged.

## Application logging

VaultBridge-owned application logs are emitted to stderr as one UTF-8 JSON object per line. Every
record has `timestamp`, `level`, `logger`, `event`, and `message`; optional allowlisted context is
included only when meaningful. UTC timestamps use millisecond ISO-8601 `Z` notation. Stable events
cover application startup/shutdown, optional watcher startup/shutdown/failure, full semantic
synchronization, targeted reindex scheduling and outcomes, and committed note create/append
operations.

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
Legacy and versioned requests record distinct matched templates such as `/notes` and
`/api/v1/notes` through the same middleware, without logging raw request paths.

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
- the optional watcher is disabled by default; when enabled, safe external Markdown
  create/modify/delete/move events are debounced and atomically batched into the same targeted queue
- unchanged/idempotent/failed writes do not enqueue semantic work
- enqueue/submission failure after a committed write does not fail or repeat the Markdown mutation
- duplicate pending paths are coalesced; a write during active processing requests one follow-up evaluation after success or failure
- full synchronization and targeted refresh share the VB-012 worker and never run concurrently
- paths queued before a full synchronization are covered by that scan; writes during it run as a targeted follow-up
- failed/cancelled full synchronization retains process-local recovery debt; a write queued before or during that failure schedules one prioritized full retry before `ready` can be restored
- a failed follow-up with no newer work retains paths/debt for later recovery without an immediate retry loop
- targeted refresh commits in the same configurable note-count batches as full synchronization
- safely contained missing targeted paths remove their derived note/chunk rows without a full scan
- failed targeted batches, including inaccessible/unreadable/non-UTF-8 paths, keep the previous committed index, persist `error`, and retain their paths for retry
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

The optional watcher does not change `/health`, `/health/live`, or `/health/ready` response fields or
semantics. Disabled watching never makes semantic search unavailable.

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

1. External filesystem changes are watched only when the disabled-by-default optional watcher is
   enabled. Without it, startup/full synchronization remains the reconciliation path. With it,
   debounce still makes index freshness eventual rather than instantaneous.
2. The deterministic fixture does not measure real-model quality or latency on a production vault.
3. Multiple application processes sharing one index are not coordinated.
4. GPT/AI clients can invent wikilinks unless client instructions require verified existing notes.
5. Graceful shutdown cannot interrupt a model download, ONNX inference call, or filesystem operation already in progress.
6. Rate-limit state is not shared across application processes, and reverse-proxy peers can aggregate
   multiple external clients because forwarded client-address headers are not trusted.
## Verified baseline after VB-014

Native Windows:

```text
398 passed, 15 skipped, 0 failed
```

The fifteen skips are privilege-dependent Windows symlink tests. Ruff, Python compileall,
`git diff --check`, static Compose YAML parsing, all 17 protected/system endpoint operation IDs, and
a real native `watchdog` create-event smoke test passed. Docker and Docker Compose are unavailable in
the current environment, so Compose rendering and image build were not run locally.

## Verified baseline after VB-058

Native Windows:

```text
291 passed, 10 skipped, 0 failed
```

VB-058 replaces the single hard-coded POSIX-separator assertion with an exact platform-neutral
`Path` comparison. The ten skips are privilege-dependent Windows symlink tests; focused WSL/Linux
tests execute the real symlink cases and pass without skips.

Additional checks:

```text
Ruff: passed
Python compileall: passed
git diff --check: passed
all 15 published endpoint paths and operation IDs: verified unique and stable
generic Docker contract: 14 static assertions passed
all 10 Compose substitutions covered by .env.example
TrueNAS runbook/Compose/Dockerfile/settings contract: 48 static assertions passed
GHCR workflow: YAML parsed and checksum-verified actionlint 1.7.12 passed
GHCR security/publication contract: 7 action references pinned to full commit SHAs; trigger,
permissions, credentials, tags, OCI labels, build inputs, architecture scope, and manifest inspection verified
Docker / Docker Compose: unavailable in the current environment; config/build not run locally
GitHub CI: run 32857711831 passed `python` and `docker` on main commit 71f69141
GitHub repository: private; no GitHub Releases existed, so no release-triggered GHCR publication is claimed
secret audit: only example env files tracked; targeted HEAD/history token and private-key scans found no hits
```

Focused VB-057 containment audit:

```text
WSL/Linux real-symlink selection: 6 passed, 114 deselected, 0 skipped
direct_read_symlink_escape=blocked
literal_search_external_symlink_results=0
list_external_symlink_results=0
Windows security/compatibility selection: passed; privilege-dependent symlink cases skipped
full Windows suite: 290 passed, 1 known portability failure, 10 symlink skips
```

The WSL run used actual file, directory, internal, external, and broken symlinks. External targets
are absent from literal search/list and semantic full synchronization; an unsafe note previously in
the semantic index is removed by the next full synchronization. This does not claim protection
against a hostile concurrent filesystem mutation between path resolution and file access.

Focused VB-058 portability audit:

```text
formerly failing Windows end-to-end API test: passed
Windows path/auth/legacy-v1 selection: passed; 7 privilege-dependent symlink skips
full native-Windows suite: 291 passed, 10 privilege-dependent symlink skips, 0 failed
WSL/Linux path/auth/legacy-v1/VB-057 selection: 52 passed, 0 skipped
WSL/Linux full suite on Python 3.14: 298 passed, 3 unrelated ordering/mtime-sensitive failures
```

The WSL full-suite failures are outside VB-058: two rely on immediate file-change detection and one
relies on discovery/batch order. The release CI contract remains Ubuntu with Python 3.12; its exact
release-commit run is still required by the checklist.

Focused VB-053 configuration, legacy/v1 route, health/readiness, maintenance CLI, logging, and
request-observability run:

```text
141 passed
```

Focused VB-052 configuration, legacy/v1 route, health/readiness, and maintenance CLI run:

```text
98 passed
```

Focused VB-050 legacy/v1 contract and authentication run:

```text
24 passed
```

Focused CLI run:

```text
30 passed
```

Focused VB-051 CLI run:

```text
47 passed, 1 privilege-dependent symlink test skipped
```

Full native-Windows suite after VB-051:

```text
369 passed, 14 privilege-dependent symlink tests skipped
```

Focused health/readiness run:

```text
30 passed
```

Focused request observability run:

```text
17 passed
```

Focused semantic/repository/indexer/ranking run:

```text
107 passed, 2 skipped
```

Legacy protected API run:

```text
17 passed, 1 known baseline failure
```

Deterministic retrieval evaluation:

```text
9 passed
```

Docker/TrueNAS runtime verification was unavailable. The TrueNAS include, Compose, Dockerfile,
settings, environment template, documented commands, identifiers, mounts, and ports were verified
statically; no live app installation, image build, model download, ACL check, or health request is
claimed.

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
