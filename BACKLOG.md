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

VB-014 later extends safely contained missing targets into targeted derived-row removal for external
delete/rename handling; the other strict failure behavior remains unchanged.

### VB-014 — Optional filesystem watcher — P1 ✅

**Status:** Completed on 2026-08-26.

**Depends on:** VB-012, VB-013

**Implemented behavior**

- disabled by default through typed settings; disabled applications start no observer/dispatcher,
- `watchdog` supplies recursive native cross-platform events without polling full-vault scans,
- safe Markdown create/modify/delete/move paths pass through existing vault containment semantics,
- one monotonic dispatcher coalesces path bursts before one atomic call to the existing thread-safe
  indexer queue,
- targeted missing paths remove only their derived semantic rows; rename queues old and new paths,
- watcher shutdown precedes dispatcher flush and semantic-indexer shutdown,
- startup full synchronization remains the downtime reconciliation authority.

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

### VB-030 — Duplicate candidate service — P1 ✅

**Status:** Completed on 2026-08-26.

Added one advisory, read-only operation under shared legacy and `/api/v1` routes:

- live filename titles use NFKC normalization, trim, Unicode casefolding, and collapsed whitespace;
  fuzzy and substring-only matches are not exact-title evidence;
- one existing semantic search call supplies conceptual candidates without changing ranking, model,
  chunking, index signature, or persistence;
- every semantic path is verified against the live contained Markdown vault through the VB-031
  boundary before response serialization;
- exact-title candidates lead, semantic-only candidates retain semantic order, canonical paths are
  deduplicated, semantic evidence enriches overlapping exact matches, and bounded overfetch precedes
  the caller-visible limit;
- semantic unavailability falls back to exact-title evidence when present and otherwise retains the
  semantic-search `503`; unexpected programming failures are not converted into that expected error;
- results are candidates, not duplicate verdicts. No note or semantic-index write, merge, append,
  rename, delete, backlink insertion, or reindex is performed.

### VB-031 — Verified related-note suggestions — P1 ✅

**Status:** Completed on 2026-08-26.

Hardened the existing related-note contract without adding backlink writes or another endpoint:

- every semantic candidate crosses the existing `VaultService` containment boundary before response
  serialization and must resolve to a live regular Markdown file inside the vault;
- safe internal symlinks return their canonical vault-relative target, while missing, directory,
  non-Markdown, traversal, absolute, broken, external-symlink, and expected filesystem-error
  candidates are filtered;
- related search requests use a bounded three-times candidate window capped at `50`, then preserve
  surviving rank order and the caller-visible limit so small stale prefixes can be backfilled;
- verified paths determine the returned title; score, semantic score, lexical score, snippet, and
  heading remain the existing semantic-result values;
- legacy and `/api/v1` routes remain one shared endpoint with unchanged schemas, operation IDs,
  authentication, ranking, model, chunking, index signature, and semantic persistence;
- verification is read-only. Externally edited live note content can still leave scores, snippets,
  and headings stale until normal synchronization updates the derived index.

### VB-032 — Section-level update design/ADR — P1

**Status:** Deferred / optional future work. Do not treat as the next task.

Design before implementation if section-level mutation becomes a demonstrated workflow need.

### VB-033 — `updateNoteSection` endpoint — P1

**Depends on:** VB-032

**Status:** Deferred / optional future work. Do not implement unless VB-032 is resumed first.

Must include conflict detection/content hash if resumed.

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

### VB-042 — API key rotation — P1 ✅

**Status:** Completed on 2026-08-26.

Added a deliberately bounded operator-controlled rotation window:

- `API_KEY` remains the required current credential and preserves the existing missing-configuration
  error;
- optional secret-safe `API_KEY_PREVIOUS` defaults to empty and, when configured, is accepted by the
  shared legacy and `/api/v1` authentication dependency;
- exact Bearer credentials are compared with the standard-library constant-time primitive, while
  missing, malformed, and unknown credentials retain the generic HTTP `401` response;
- public health routes, endpoint paths, operation IDs, request/response schemas, and semantic behavior
  remain unchanged;
- deployment examples and operator documentation cover adding the previous key only during client
  migration, then removing it and restarting/redeploying to end the window.

### VB-043 — Lightweight rate limiting — P1 ✅

**Status:** Completed on 2026-08-26.

Added deliberately small in-process protection without Redis or another dependency:

- protected legacy and `/api/v1` note, literal/semantic search, related, duplicate-candidate,
  read/list, create, and append traffic shares a direct-ASGI-peer fixed-window allowance;
- the limiter executes before the existing authentication dependency, so repeated invalid
  credentials eventually receive HTTP `429`, while current/previous keys and the missing-current-key
  configuration error retain VB-042 behavior;
- timing uses `time.monotonic`; state is lock-protected, non-persistent, reclaimed when stale, and
  hard-capped with deterministic least-recently-used eviction and no background thread;
- `GET /health`, `GET /health/live`, `GET /health/ready`, and schema-hidden `/privacy` are exempt;
- forwarded client-address headers are ignored. A reverse proxy can therefore aggregate external
  clients into one peer bucket, and multiple VaultBridge processes do not share limiter state;
- operators can disable or tune the `true`, `120` requests, `60` seconds, and `1024` clients defaults
  through typed environment settings.

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

### VB-051 — Add VaultBridge CLI — P1 ✅

**Status:** Completed on 2026-08-26.

Commands:

```text
status
index
reindex
search
related
```

The standard-library local CLI now reuses the production vault and semantic services. `status`
shares VB-045's immutable persisted inspection; `index` runs an offline incremental/full sync without
discarding a compatible index; `reindex` is the friendly alias for the existing clean rebuild;
`search` uses literal title/content search without embeddings; and `related` queries an existing
compatible semantic index without synchronizing it. Search folders use the vault-relative
containment boundary, and semantic paths are live-verified before display. The VB-045 `index check`
and `index rebuild` commands and exit-code contract remain supported.

### VB-052 — Generic Docker deployment docs — P0 ✅

**Status:** Completed on 2026-08-25.

`README.md` now provides a linear source-build Docker Compose workflow for a normal host: prerequisites,
safe API-key creation, host vault and UID/GID mapping, generic semantic-data persistence, asynchronous
first-start behavior, public health probes, an authenticated `/api/v1` smoke test, structured logs,
updates, stopping/removal semantics, offline index maintenance, and loopback-only exposure guidance.
The generic `/vault/.obsidian-chatgpt-data` layout remains separate from the TrueNAS `/data` layout.
Runtime, Compose, Dockerfile, API, index, and TrueNAS compatibility behavior are unchanged.

### VB-053 — TrueNAS deployment docs — P0 ✅

**Status:** Completed on 2026-08-25.

`README_TRUENAS.md` is now a self-contained operational runbook for the existing TrueNAS SCALE
Custom App deployment. It preserves the legacy source/dataset/service/container identifiers,
documents the `truenas-install.yml` include model, UID/GID `568:568` ACL principle, asynchronous
first startup, public health probes, an authenticated `/api/v1` smoke test, safe log correlation,
managed and shell-only lifecycle boundaries, bundle/no-Git updates, backup/rollback guidance, and
stopped-service semantic maintenance. Compose, Dockerfile, runtime, and API behavior are unchanged.

### VB-054 — Publish GHCR image workflow — P0 ✅

**Status:** Completed on 2026-08-25.

Published GitHub Releases with validated `v`-prefixed semantic-version tags now build the existing
root Dockerfile and push `ghcr.io/<lowercase-repository-owner>/vaultbridge` with an exact version
tag. Stable releases also update `major.minor`, `major`, and `latest`; prereleases update only their
exact tag. The release workflow repeats tests, compilation, and Compose validation before a
minimal-permission publish job can use `GITHUB_TOKEN`, pins every action to a verified commit SHA,
adds OCI source/revision/version/license labels, and inspects the published digest. Source-build
Docker/TrueNAS deployment, runtime behavior, and single-architecture scope remain unchanged. The
existing BuildKit path emits minimal provenance without external signing credentials; stronger
GitHub attestations or signing require a separate hardening decision.

### VB-055 — Multi-arch image — P1

### VB-056 — GitHub v1.0 release checklist — P0 ✅

**Status:** Completed on 2026-08-25.

`docs/RELEASE_CHECKLIST.md` turns every ROADMAP `v1.0.0` criterion into an evidence-backed gate and
documents reusable source/CI/clean-install checks, supported platforms, artifact/version identity,
RC and stable publication procedures, post-publication GHCR verification, draft release notes, and
non-blocking P1 follow-ups. VB-056 completion means the audit/checklist exists; it does not mean
`v1.0.0` was released. Clean-install and image-publication gates remain unmet.

### VB-057 — Enforce symlink containment in vault enumeration — P0 ✅

**Status:** Completed on 2026-08-25.

Literal search, note listing, and semantic full synchronization now share contained Markdown
enumeration: each discovered candidate is resolved, checked against the resolved vault root,
deduplicated by its contained canonical path, and only then read or inspected through that validated
path. External file and directory symlinks and broken links are skipped without exposing content,
metadata, or host paths. Internal file symlinks resolve to the canonical vault-relative note without
duplicates. Linux/WSL service, legacy/v1 route, direct-read, and semantic synchronization regressions
execute with real symlinks. No API, schema, index-signature, ranking, lifecycle, CLI, deployment, or
dependency change was required.

### VB-058 — Fix cross-platform path assertion — P0 ✅

**Status:** Completed on 2026-08-25.

The end-to-end create/read/search/append test now compares the returned vault-relative note path as
a native `Path` instead of comparing it with a hard-coded POSIX-separator string. Repository review
confirmed that the response model and Action schema promise a vault-relative Markdown path but do
not define separator serialization, while `VaultService._relative_path()` deliberately returns the
native filesystem representation. Runtime and API behavior are unchanged. Native Windows now has
zero test failures, and focused WSL/Linux path, authentication, legacy/v1, and real-symlink tests pass.

### VB-059 — Align v1.0 version metadata — P0 ✅

**Status:** Completed on 2026-08-25.

The existing `pyproject.toml` package version and `app.main.APP_VERSION` application metadata are
aligned from `0.1.0` to the stable target `1.0.0`. FastAPI uses the latter only for application and
internal OpenAPI metadata; routes, operation IDs, schemas, authentication, runtime behavior, API
namespace, semantic index signature, dependencies, deployment files, and the GHCR workflow are
unchanged. The Git tag/GitHub Release and GHCR digest remain the authoritative release and immutable
deployment identities. A prerelease derives only its exact prerelease image tag from its GitHub Release tag and does not update stable aliases.

### VB-060 — Public repository exposure audit — P0 ✅

**Status:** Completed on 2026-08-25.

The complete tracked tree and reachable Git history were audited before public exposure. A verified
external Git bundle was created before rewriting only the repository owner's historical personal
author/committer email to the configured GitHub noreply identity. All 11 existing remote branches
were updated atomically with explicit force-with-lease protection; deleted stale branches were not
recreated, no tags existed, and the removed identity has zero reachable occurrences after fetch and
prune. The generated `dist/` bundle is no longer tracked and future bundles are ignored.

`SECURITY.md` now accurately states that no confirmed dedicated private external vulnerability
reporting channel exists. Repeated HEAD/history secret, personal/internal-data, artifact, workflow,
test, lint, compilation, and diff checks passed. The repository remains private; VB-060 completion
does not satisfy the separate public anonymous clean-install or release-publication gates.

---

## Web Dashboard / operator experience

### VB-070 — Web Dashboard architecture and security design — P1 ▶

**Status:** Planned; next recommended task. Documentation/design only.

**Goal:** define the deliberately small dashboard boundary, routing model, authentication handling,
first-release information architecture, and reuse of the existing API/domain capabilities before UI
implementation begins.

**Depends on:** the completed v1.0 application/API/container baseline.

**Acceptance criteria**

- the dashboard remains in the `mrtrollex/VaultBridge` repository and is served by the existing
  application in the same production container;
- the design is platform-neutral and has no TrueNAS runtime dependency;
- the API and CLI remain independently usable, first-class interfaces when the UI is unused;
- first-release areas and boundaries are agreed for Overview, Search, API / Integration, and About;
- the design identifies which existing health, vault, semantic-index, literal-search, and semantic
  retrieval capabilities are reused, with no duplicate frontend business logic;
- routing and same-origin behavior are documented without prematurely fixing internal filenames;
- an authentication threat model covers browser credential entry/retention, protected requests,
  same-origin behavior, logout/session clearing, injection, XSS, browser history, storage, logs, and
  error rendering;
- no configured API key is injected into generated HTML or JavaScript source, put in a URL, returned
  by an endpoint, logged, or persisted server-side merely for the dashboard;
- any considered operator-supplied key model, including session memory or `sessionStorage`, remains
  a design outcome rather than an assumed final decision until the threats are reviewed;
- accessibility, responsive behavior, privacy-safe rendering, and reduced-motion expectations are
  documented for later implementation;
- index state may be displayed, but note/index mutation is excluded unless separately designed;
- the design explicitly prefers lightweight bundled assets with no mandatory Node/npm pipeline,
  frontend framework, second service, or second container unless a demonstrated requirement is
  documented;
- no runtime implementation is added by this task.

**Out of scope**

- HTML, CSS, JavaScript, UI routes, bundled assets, screenshots, or browser automation;
- note creation/editing, WYSIWYG editing, graph visualization, file management, account management,
  multi-user administration, general NAS administration, or a general Obsidian replacement;
- username/password accounts, OAuth, a user database, or another authentication subsystem;
- an HTTP/live UI action for semantic-index rebuild or any conversion of stopped-service CLI
  maintenance into a serving-process mutation;
- TrueNAS catalog packaging or a release/version decision.

**Safety/privacy constraints**

- preserve the current Bearer-auth, rate-limit, safe-logging, request-observability, vault
  containment, and offline-index-ownership boundaries;
- design with credentials, queries, note-derived text, snippets, paths, and error data treated as
  sensitive/untrusted browser inputs; do not create analytics or persistence for them by default.

**Validation expectations**

- review the design against current FastAPI routing, Bearer authentication, health/search services,
  CLI index ownership, Dockerfile, Compose, and TrueNAS Custom App documentation;
- verify that the design changes no endpoint, operation ID, request/response contract, dependency,
  runtime file, container behavior, or release artifact.

### VB-071 — Dashboard shell and authenticated session — P1

**Status:** Planned.

**Goal:** implement the approved lightweight first-party browser entry point and safe operator
credential/session flow.

**Depends on:** VB-070.

**Acceptance criteria**

- `/ui` or the equivalent route approved by VB-070 serves a responsive dashboard shell from the
  existing VaultBridge application and production container;
- Overview, Search, API / Integration, and About navigation follows the agreed first-release scope;
- credential/session handling implements the VB-070 threat model, including explicit logout/session
  clearing and safe authentication/error states;
- protected data requests retain the existing Bearer-auth boundary and no secret is embedded in
  assets, HTML, URLs, logs, server responses, or server-side dashboard persistence;
- bundled assets are lightweight and require no frontend framework/build chain unless VB-070
  explicitly documented and justified that need;
- the API/CLI operate unchanged when the dashboard is disabled or unused;
- focused tests cover routing, authentication failure, secret non-disclosure, safe rendering, and
  compatibility of existing API routes/operation IDs.

**Out of scope**

- overview/search feature completion beyond the minimum shell wiring;
- note/index mutation, editing, accounts, OAuth, or TrueNAS-specific runtime behavior.

**Safety/privacy constraints**

- never place credentials in URLs or persistent application storage contrary to VB-070;
- render server/user-controlled strings as text unless an explicitly reviewed sanitizer boundary exists;
- do not log browser credentials, queries, note content, headers, bodies, or raw paths.

**Validation expectations**

- focused route/auth/security tests, existing API/OpenAPI compatibility checks, browser smoke tests,
  and normal Python/compile checks;
- verify the production container serves the bundled shell without a second service.

### VB-072 — Dashboard overview and health visibility — P1

**Status:** Planned.

**Goal:** provide a concise operator overview using facts already owned by VaultBridge.

**Depends on:** VB-071.

**Acceptance criteria**

- the overview presents applicable application health, vault availability, semantic lifecycle,
  semantic-search availability, indexed-note/chunk/vault-note counts, last successful full sync, and
  existing watcher/indexer state;
- backend/domain owners remain authoritative; frontend-specific code does not recalculate health,
  readiness, index state, counts, or synchronization status;
- public versus protected visibility follows VB-070 and does not weaken authentication;
- unknown/unavailable/stale states are represented honestly rather than inferred as healthy;
- the page is read-only and exposes no index rebuild or other maintenance mutation;
- loading, empty, unavailable, and refresh/error states are covered by focused tests.

**Out of scope**

- new detailed progress/ETA promises, historical telemetry, log streaming, or NAS administration;
- index, note, configuration, or process mutation.

**Safety/privacy constraints**

- expose only allowlisted operational facts; never display credentials, absolute host paths, note
  content, queries, headers, or raw exception details.

**Validation expectations**

- service/API reuse tests, browser rendering/smoke tests, accessibility checks, and container-path verification.

### VB-073 — Dashboard search interface — P1

**Status:** Planned.

**Goal:** provide browser access to existing literal and semantic retrieval behavior without a
second search implementation.

**Depends on:** VB-071.

**Acceptance criteria**

- the interface supports the existing literal search and semantic related-note search capabilities;
- requests reuse stable VaultBridge APIs/domain services and preserve production ranking, thresholds,
  ordering, folder/path containment, live-note verification, result limits, and error semantics;
- score/debug fields are shown only where useful and already available, without inventing new
  relevance meaning;
- query, folder, result, empty, unavailable, and validation states are usable and accessible;
- note content/snippets and paths are rendered safely as untrusted data;
- search remains read-only and creates no alternative index, cache, or ranking pipeline.

**Out of scope**

- note creation/editing, automatic backlinks, duplicate merging, graph search, arbitrary filesystem
  browsing, or index mutation;
- model, chunking, ranking, schema, or API-contract changes unless separately approved.

**Safety/privacy constraints**

- do not log or persist queries, note content, snippets, credentials, or raw paths for UI analytics;
- preserve the existing authentication, rate-limit, containment, and safe-error boundaries.

**Validation expectations**

- focused API/domain-reuse and browser tests covering literal/semantic results, auth, empty/error
  states, safe rendering, ordering, and path filtering.

### VB-074 — Dashboard usability, accessibility and release hardening — P1

**Status:** Planned.

**Goal:** harden the completed dashboard for supported browsers and the normal production image
without declaring a release version in advance.

**Depends on:** VB-071, VB-072, VB-073.

**Acceptance criteria**

- keyboard navigation, focus visibility/order, labels, landmarks, contrast, zoom/reflow, responsive
  layout, and reduced-motion behavior meet the agreed accessibility baseline;
- credential, loading, empty, validation, unavailable, retry, and unexpected-error states are clear
  and privacy-safe;
- note-derived text, snippets, headings, and paths cannot inject executable markup;
- browser smoke tests cover the chosen lightweight implementation and supported responsive states;
- documentation and sanitized screenshots accurately distinguish the dashboard from the API and do
  not claim unsupported editing/index/NAS functionality;
- the normal Dockerfile/image serves API, CLI, and dashboard together, with startup, health,
  authenticated UI data access, and existing API behavior verified;
- release-readiness evidence is recorded, but no release/version is automatically assigned.

**Out of scope**

- scope expansion into editing, accounts, graph/file/NAS management, a frontend framework migration,
  or TrueNAS Community App packaging.

**Safety/privacy constraints**

- sanitized test data/evidence only; no API keys, vault contents, queries, absolute host paths, or
  private environment details in screenshots, logs, fixtures, or documentation.

**Validation expectations**

- accessibility review, responsive/browser smoke suite, reduced-motion checks, Python/API regression
  checks, container build/startup/runtime checks, and documentation consistency review.

---

## TrueNAS Community App distribution

### VB-080 — TrueNAS Community App packaging design — P1

**Status:** Planned.

**Goal:** define the upstream TrueNAS catalog packaging, supported configuration, and upgrade
contract around the normal published VaultBridge image.

**Depends on:** VB-074 plus a published and verified dashboard-capable VaultBridge image.

**Acceptance criteria**

- current `truenas/apps` contribution conventions and ownership are documented from authoritative
  upstream sources at implementation time;
- the design defines image/tag/digest policy, application metadata, storage mappings, API-key input,
  port exposure, health/readiness probes, resource settings, and the Web Portal target;
- UID/GID/user behavior follows then-current TrueNAS conventions without moving platform behavior
  into VaultBridge domain services;
- authoritative Markdown storage and persistent derived semantic data remain distinct, and external
  vault ownership is preserved across upgrade/uninstall;
- upgrade and rollback expectations, supported versions, configuration migration, and evidence gates
  are documented;
- the catalog definition pulls `ghcr.io/mrtrollex/vaultbridge:<released-version>` and does not build,
  fork, or reimplement VaultBridge;
- `mrtrollex/VaultBridge` and `truenas/apps` ownership boundaries are explicit, with the published
  image as their interface and no permanent `VaultBridge-TrueNAS` runtime repository/fork;
- no catalog implementation is added in this design task.

**Out of scope**

- core runtime/domain/API changes, an alternative container image, upstream submission, or claiming
  TrueNAS catalog availability.

**Safety/privacy constraints**

- secret fields must not appear in generated output, logs, portal URLs, support bundles, or screenshots;
- packaging must not gain arbitrary filesystem, Docker socket, host administration, or vault-deletion behavior.

**Validation expectations**

- compare the design with current upstream TrueNAS documentation/schema and the verified VaultBridge
  image/runtime contract; record any live or upstream-version gates explicitly.

### VB-081 — Implement TrueNAS Community App definition — P1

**Status:** Planned.

**Goal:** create the catalog packaging approved by VB-080 using current TrueNAS Community App conventions.

**Depends on:** VB-080.

**Acceptance criteria**

- the definition consumes the published VaultBridge GHCR release image and does not rebuild runtime code;
- configuration, metadata, storage, secret, user/identity, port, health/readiness, resource, and Web
  Portal fields implement the VB-080 contract;
- the portal opens the bundled VaultBridge dashboard rather than a TrueNAS-specific UI fork;
- catalog validation/lint/schema checks pass under the required upstream toolchain;
- local fixtures are kept only where useful for reproducibility and do not become a second
  authoritative copy after upstream acceptance;
- no TrueNAS-specific code enters VaultBridge application/domain services.

**Out of scope**

- changing VaultBridge API, semantic behavior, CLI, Dockerfile, or image contents;
- upstream acceptance claims or production-data testing.

**Safety/privacy constraints**

- use placeholders/sanitized fixtures only; do not commit API keys, vault content, private host paths,
  resolved environment output, or generated semantic data.

**Validation expectations**

- upstream schema/lint/render checks and a review proving the image reference, mounts, secret handling,
  health probes, and Web Portal match VB-080.

### VB-082 — TrueNAS install/upgrade/portal validation — P1

**Status:** Planned.

**Goal:** validate the Community App lifecycle on a real disposable TrueNAS installation and capture
sanitized evidence.

**Depends on:** VB-081.

**Acceptance criteria**

- fresh install and startup succeed with disposable vault and semantic-data storage;
- vault mount, semantic-data persistence, liveness/readiness, dashboard Web Portal, and authenticated
  `/api/v1` access are verified;
- restart and supported configuration edits preserve intended data and behavior;
- upgrade is verified from an explicitly supported prior package/image state;
- rollback is verified where current TrueNAS/App conventions support it, or accurately documented as
  unsupported/blocked rather than claimed;
- uninstall does not delete externally owned vault data; semantic-data behavior matches the approved
  storage contract;
- logs, UI evidence, commands, paths, and screenshots are sanitized of secrets and private content;
- failures are classified as PASS, FAIL, or REQUIRES LIVE/UPSTREAM VERIFICATION with concrete evidence.

**Out of scope**

- production vaults, destructive tests against user-owned data, runtime forks, or upstream submission.

**Safety/privacy constraints**

- disposable data and scoped paths only; resolve storage targets before cleanup and preserve all
  externally owned Markdown by default.

**Validation expectations**

- real TrueNAS install/start/health/portal/API/restart/configuration/upgrade/rollback/uninstall runbook
  with sanitized artifact, image tag/digest, platform, and result evidence.

### VB-083 — Submit VaultBridge to upstream TrueNAS Apps catalog — P1

**Status:** Planned.

**Goal:** prepare and submit the verified VaultBridge Community App contribution to `truenas/apps`.

**Depends on:** successful VB-082.

**Acceptance criteria**

- the contribution follows current upstream repository, metadata, review, and testing requirements;
- submission uses the VB-082-verified published VaultBridge image and approved packaging contract;
- release notes/operator documentation state prerequisites and current limitations accurately;
- review feedback is resolved without introducing a VaultBridge runtime fork or duplicating domain logic;
- submission, review, and merge states are recorded separately;
- catalog availability/acceptance is claimed only after the upstream pull request is merged and the
  accepted app is visible through the applicable catalog delivery path.

**Out of scope**

- changing core runtime behavior solely to bypass upstream review, maintaining a permanent parallel
  catalog fork, or claiming acceptance from an open pull request.

**Safety/privacy constraints**

- no secrets, private vault data, host paths, account details, or unsanitized validation artifacts in
  commits, pull-request text, screenshots, or review logs.

**Validation expectations**

- upstream-required checks pass; record pull-request URL/state and, after merge, independently verify
  the accepted catalog entry before marking the task complete.

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
→ VB-042  ✓
→ VB-044  ✓
→ VB-045  ✓
→ VB-050  ✓
→ VB-051  ✓
→ VB-056  ✓
→ VB-057  ✓
→ VB-058  ✓
→ VB-059  ✓
→ VB-060  ✓
→ v1.0.0 ✓
→ VB-070 NEXT
→ VB-071
→ VB-072 + VB-073
→ VB-074
→ published/verified dashboard-capable VaultBridge image
→ VB-080
→ VB-081
→ VB-082
→ VB-083
```

VB-057 through VB-060 close the confirmed containment, native-Windows test-portability,
release-version alignment, and repository-exposure-safety blockers. Stable `v1.0.0` and its final
distribution gates are complete; immutable evidence remains recorded in `docs/RELEASE_CHECKLIST.md`.

VB-070 is the next recommended task and is documentation/design only. VB-023 remains open P1
retrieval work with unchanged scope, but it is no longer NEXT. VB-032 and VB-033 remain deferred
optional work. VB-055 remains optional and is not a dashboard prerequisite. Milestone 9 begins only
after Milestone 8 produces a published and verified dashboard-capable VaultBridge image.

Do not infer scope from sequence alone. Always read the exact task definition before implementation.
