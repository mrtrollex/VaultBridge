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

### VB-023 — Retrieval benchmark command — P1 ✅

**Status:** Completed on 2026-09-07.

The repository command `python -m tests.eval.benchmark` runs the sanitized VB-022 corpus through a
disposable production-model `SemanticSearchService` and reports stable Markdown or JSON. It records
per-query latency, expected-result rank, vault-relative paths, headings, and semantic/lexical/final
scores plus Hit@1, Hit@5, MRR, and latency aggregates. Synchronization/model loading is timed
separately, normal tests inject the deterministic embedder and fake timing, and `baseline.json`
remains the unchanged VB-022 regression authority.

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

### VB-025 — Fingerprint semantic embedding compatibility — P0 ✅

**Status:** Completed on 2026-09-19.

**Implemented behavior**

- the versioned semantic signature retains the `v3-heading-context` content contract, model and
  effective chunk settings and adds an embedding implementation fingerprint;
- the fingerprint combines an application-owned FastEmbed attention-mask mean-pooling,
  VaultBridge-float32/L2 contract with hashes of the exact resolved ONNX and tokenizer/config files;
- FastEmbed is pinned to `0.8.0`, while runtime memory/batch/provider and ranking settings remain
  outside compatibility;
- legacy v3 or missing signatures with existing derived rows trigger one safe full rebuild and are
  never exposed as compatible runtime search data;
- artifact-resolution failure leaves recoverable rows/signature intact, reports semantic error state
  and remains retryable.

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

**Status:** Planned / optional. Do not implement before the read-only relationship work.

**Depends on:** VB-102

**Goal:** add an explicitly requested backlink to an existing verified Markdown note only after the
relationship resolver is established.

This is the first task in the relationship track allowed to mutate Markdown. It must remain opt-in,
must never invent or create a target note, and must define conflict detection, idempotency, write
safety, and reindex behavior before implementation. Automatic backlink insertion remains out of
scope.

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

### VB-070 — Web Dashboard architecture and security design — P1 ✅

**Status:** Completed on 2026-08-29. Documentation/design only.

**Goal:** define the deliberately small dashboard boundary, routing model, authentication handling,
first-release information architecture, and reuse of the existing API/domain capabilities before UI
implementation begins.

**Depends on:** the completed v1.0 application/API/container baseline.

**Accepted decisions:** [ADR 0003](docs/adr/0003-web-dashboard-architecture-and-security.md)
defines a same-origin dashboard with canonical `/ui/` routing, explicit `/ui/assets/`, no SPA
catch-all, and an operator-supplied Bearer key retained in namespaced `sessionStorage` only after
successful protected API validation. Logout and `401` clear the browser session. The dashboard must
use strict CSP/security headers, render untrusted values as text, load no third-party resources, and
reuse the current health and search contracts without note/index mutation. The first implementation
is static HTML/CSS/vanilla JavaScript in the existing application/image, with no new frontend build
pipeline, framework, service, container, or dependency. Watcher state is not available through the
current HTTP API and must be omitted or identified as unavailable rather than inferred.

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
- the selected browser model stores an operator-supplied key in namespaced `sessionStorage` only
  after successful protected validation, sends it only as the existing Bearer header, and clears it
  on logout or `401`;
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

### VB-071 — Dashboard shell and authenticated session — P1 ✅

**Status:** Completed on 2026-08-29.

**Goal:** implement the approved lightweight first-party browser entry point and safe operator
credential/session flow.

**Depends on:** VB-070.

**Acceptance criteria**

- public `GET`/`HEAD /ui` returns a `307` redirect to canonical `/ui/`; the entry document and
  explicit `/ui/assets/` namespace are served by the existing application/container, while unknown
  UI paths return `404` without an SPA catch-all;
- Overview, Search, API / Integration, and About navigation follows the agreed first-release scope;
- credential/session handling implements ADR 0003, including validation through
  `GET /api/v1/notes/list?limit=1`, namespaced `sessionStorage`, reload revalidation, explicit
  logout/session clearing, and status-specific `401`/`429`/`503`/network behavior;
- protected data requests retain the existing Bearer-auth boundary and no secret is embedded in
  assets, HTML, URLs, logs, server responses, or server-side dashboard persistence;
- bundled assets are static HTML/CSS and small vanilla JavaScript modules, with no frontend
  framework, build chain, inline code, remote asset, or new dependency;
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

**Implemented behavior**

- explicit schema-hidden `GET`/`HEAD` routes serve the canonical `/ui/` document and two known
  `/ui/assets/` resources; `/ui` redirects with `307`, reverse-proxy `root_path` is preserved, and
  unknown UI paths return `404` without a catch-all;
- the bundled semantic HTML/CSS/vanilla JavaScript shell provides responsive Overview, Search,
  API / Integration, and About navigation with locked, checking, unlocked, and unavailable states;
- unlock and reload validation reuse `GET /api/v1/notes/list?limit=1`; only a successful response
  stores the operator-supplied credential under `vaultbridge.ui.apiKey` in `sessionStorage`;
- one same-origin authenticated fetch helper owns Bearer injection and controlled
  `401`/`429`/`Retry-After`/`503`/server/network handling; logout clears credentials and protected
  state, aborts controlled requests, and ignores stale responses;
- UI documents and assets receive the ADR 0003 CSP, `nosniff`, and no-referrer headers; dynamic
  browser text uses `textContent`, with no inline/third-party code, Markdown rendering, or remote
  resource;
- focused static and HTTP tests plus a real desktop/mobile browser smoke test cover the shell and
  session lifecycle. At VB-071 completion, Overview data and search behavior remained placeholders
  for VB-072/VB-073.

### VB-072 — Dashboard overview and health visibility — P1 ✅

**Status:** Completed on 2026-08-30.

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

**Implemented behavior**

- the Overview automatically reads the existing public same-origin `GET /health` contract without
  sending the dashboard Bearer credential and remains usable while locked, unlocked, or logged out;
- compact Overall status, Vault, Semantic index, and Background indexing cards expose only the
  existing health facts, with lifecycle state and semantic-search availability kept separate;
- the display-only overall label is deterministically Ready, Indexing, Degraded, or Unavailable,
  while every underlying health fact remains visible and no backend health calculation is replaced;
- counts use locale-aware non-negative integer formatting, last successful sync uses locale-aware
  date/time formatting with a semantic `datetime` value, and null or invalid timestamps remain safe;
- initial loading, manual refresh, network/HTTP unavailability, and malformed-response states are
  explicit, announced without focus stealing, and never render response bodies or raw exceptions;
- the responsive read-only UI adds no polling, watcher inference, maintenance control, external
  resource, frontend dependency, API/auth change, or TrueNAS-specific behavior.
- a small dependency-free ES-module split keeps session/auth/navigation ownership in `app.js` and
  public health fetching/rendering ownership in `overview.js` without a build step.

### VB-073 — Dashboard search interface — P1 ✅

**Status:** Completed on 2026-08-30.

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

**Implemented behavior**

- the protected Search area exposes accessible Literal and Semantic modes backed directly by
  `POST /api/v1/notes/search` and `POST /api/v1/notes/related`, with request fields and HTML input
  limits matching the current Pydantic contracts;
- result cards preserve server order, render only existing response fields, show semantic rank and
  score components without recalculation, and handle null heading/snippet/score values safely;
- Search reuses the VB-071 Bearer/session helper, clears protected form/results on logout or `401`,
  retains authentication for `429`, validation failures, network failures, and semantic `503`, and
  aborts/invalidate stale UI-controlled requests;
- query, folder, results, snippets, paths, and scores remain in memory only and never enter browser
  storage, URLs, history, analytics, or telemetry;
- `search.js` owns mode, request, lifecycle, and text-only result rendering while `app.js` retains
  session/auth/navigation ownership and `overview.js` retains public `/health` ownership;
- the interface remains retrieval-only and adds no note read/mutation, duplicate workflow, index
  mutation, backend search/ranking change, API endpoint, dependency, or TrueNAS-specific behavior.

### VB-074 — Dashboard usability, accessibility and release hardening — P1 ✅

**Status:** Completed on 2026-08-31.

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

**Implemented behavior and evidence**

- keyboard focus/session handling, error associations, explicit score labels, stale-request
  suppression, narrow/mobile reflow, long-content wrapping, contrast coverage, and reduced-motion
  behavior are hardened without changing API or runtime contracts;
- focused static/HTTP coverage and a real Chrome audit verify keyboard, responsive, privacy-safe,
  injection-safe, session/error, and latest-request behavior with disposable synthetic data;
- the normal Dockerfile image passed a disposable TrueNAS production-image gate covering liveness,
  rich health, dashboard assets and security headers, authenticated literal and semantic retrieval,
  CLI availability, safe logs, restart persistence, clean stop, and complete disposable cleanup;
- screenshot capture remained unavailable after repeated connected-Chrome timeouts, so no screenshot
  artifact is claimed or fabricated; rendered Chrome inspection still passed;
- no release version, tag, GitHub Release, or dashboard-capable GHCR image was created by VB-074.

### VB-075 — Publish and verify dashboard-capable VaultBridge image — P1 ✅

**Status:** Completed on 2026-09-07. Release `v1.1.0`, source commit
`e39ed91db75f912f390c7ec915dea73369bb9252`, exact-source CI run `33640580398`, GHCR workflow
`33641163374`, four stable aliases, OCI/runtime/attestation digests, required labels, anonymous
exact-digest pull, and the full disposable exact-image functional runtime gate are recorded.

**Goal:** publish and verify the normal VaultBridge image containing the completed bundled Web
Dashboard so downstream packaging can consume an immutable dashboard-capable artifact.

**Depends on:** VB-074.

**Acceptance criteria**

- make an explicit release/version decision from the completed source without preassigning a version
  in this backlog item;
- require successful source/CI release gates on the exact release commit before publication;
- publish through the existing release-triggered GHCR workflow without changing the normal
  Dockerfile, API, CLI, dashboard, semantic behavior, mounts, or single-image architecture;
- verify the public package, exact tag, immutable digest, OCI source/revision/version/license
  metadata, intended platform, and anonymous pull;
- run the exact published image against disposable vault/data and verify dashboard assets, health,
  authenticated API behavior, literal and semantic retrieval, CLI availability, safe logs, restart
  persistence, clean stop, and cleanup;
- record sanitized release, workflow, tag, digest, platform, and runtime evidence for VB-081/VB-082.

**Out of scope**

- TrueNAS Community App design/implementation, a runtime/UI fork, unrelated application behavior,
  multi-architecture expansion, or use of production vaults, credentials, ports, or derived data.

**Safety/privacy constraints**

- use disposable synthetic data and never expose API keys, resolved environments, private paths,
  vault content, queries, or credentials in logs or published evidence.

**Validation expectations**

- exact-source CI and release workflow PASS, GHCR tag/digest/metadata verification, anonymous pull,
  and a disposable exact-image TrueNAS runtime gate with sanitized recorded evidence.

### VB-076 — Persistent Web Dashboard session — P1 ✅

**Status:** Completed on 2026-09-26.

**Goal:** replace the dashboard's browser-held Bearer credential with a stateless, server-issued,
HttpOnly session cookie while preserving external REST and MCP Bearer authentication unchanged.

**Implemented behavior**

- `POST /ui/session` applies the shared protected-peer rate limit and current/previous API-key
  verification, then issues a seven-day, current-key-derived HMAC-SHA256 session containing only
  version and bounded timestamps;
- `GET /ui/session` validates and renews the same-origin session, while `DELETE /ui/session`
  idempotently clears it; session responses are non-cacheable;
- the cookie is `HttpOnly`, `SameSite=Strict`, `Path=/`, has explicit bounded expiry, no `Domain`,
  and is `Secure` when the effective request scheme is HTTPS;
- protected API routes accept the cookie only with `X-VaultBridge-UI-Request: 1`; cookie-only and
  marker-only requests remain unauthorized, existing Bearer requests remain unchanged, and the
  shared peer rate limit still runs first;
- the dashboard stores no raw credential, session token, or Bearer header in browser-readable
  storage and preserves its existing cancellation, stale-response, safe-rendering, and
  authentication-versus-transient-error behavior;
- rotation of the current API key invalidates existing UI sessions; an accepted previous key may
  unlock a new session, but that session is always signed from the current key.

**Out of scope:** accounts, OAuth/OIDC, a session database, Redis, cross-origin sharing, TrueNAS
packaging changes, REST/MCP Bearer changes, and VB-111.

---

## TrueNAS Community App distribution

### VB-080 — TrueNAS Community App packaging design — P1

**Status:** Completed on 2026-09-02. The accepted packaging contract is recorded in
[`docs/TRUENAS_COMMUNITY_APP_DESIGN.md`](docs/TRUENAS_COMMUNITY_APP_DESIGN.md); no catalog definition,
image publication, or upstream submission was performed.

**Goal:** define the upstream TrueNAS catalog packaging, supported configuration, and upgrade
contract around the normal published VaultBridge image.

**Design input:** VB-074. A published and verified dashboard-capable VaultBridge image was a
dependency for VB-081 production finalization and is now available as `v1.1.0`; it was not required
for the version-neutral packaging design itself.

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

- secret fields use current upstream UI masking and must not be deliberately emitted in app notes,
  portal URLs, logs, retained render output, support bundles, fixtures, or screenshots; the privileged
  TrueNAS/Docker control plane necessarily carries the values into the container environment and is
  not an encrypted secret boundary;
- packaging must not gain arbitrary filesystem, Docker socket, host administration, or vault-deletion behavior.

**Validation expectations**

- compare the design with current upstream TrueNAS documentation/schema and the VaultBridge runtime
  contract; keep publication, immutable-image, live TrueNAS, and upstream-version gates explicit.

### VB-081 — Implement TrueNAS Community App definition — P1

**Status:** Completed on 2026-09-08 against `truenas/apps` commit
`906a20a22ee885add8c620660eba3d6ed51289da`. The historical pre-submission definition pins
`ghcr.io/mrtrollex/vaultbridge:1.1.0`, uses matching `app_version`, library `2.3.11`, officially
generated hash/library/catalog artifacts, and the then-free Community App port `30486`. Upstream
schema, catalog-port, render, deploy/health/cleanup, hash/generator, and dev-catalog checks passed for
all three synthetic fixtures. The accepted upstream package later moved to default port `30491` and
the reviewer-supplied CDN icon under VB-083. Live lifecycle validation remains VB-082.

**Goal:** create the catalog packaging approved by VB-080 using current TrueNAS Community App conventions.

**Depends on:** VB-080. The production image and official deployable-definition validation are
complete.

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

**Status:** In progress / partial validation as of 2026-09-17. A fresh TrueNAS `25.10.6` custom-YAML
installation of `ghcr.io/mrtrollex/vaultbridge:1.1.0` at OCI index digest
`sha256:753e613617d221c3dac311600a36cab3f2727b09f630321664eaa7b7ad6eb48c` validated the core
runtime/API/UI path. Restart/persistence and watcher-disabled/watcher-enabled behavior are recorded
separately as **OPERATOR-CONFIRMED PASS** because raw command output was not retained. Sanitized
operator evidence now also records **PASS** for API-key overlap/removal, Web Port runtime behavior,
occupied-port rejection/recovery, permission-denied/recovery behavior, and preservation of the
external vault and host-path `/data` on uninstall. Upgrade is **UNSUPPORTED / NO VALID PRIOR PACKAGE
STATE** and rollback is **BLOCKED** until a prior catalog revision exists. After PR #5805 merged, a
real Community catalog install was **OPERATOR-CONFIRMED PASS** for Discover Apps availability,
installation, the usable install form, masked API-key inputs, `/ui/` Portal/dashboard access, Host
Path mounting of an existing vault, ixVolume configuration for derived data, healthy vault
visibility, and API-key rotation compatibility during migration. Edit-form persistence and ixVolume
uninstall retain/remove semantics remain unverified; acceptance and initial installation do not
close those gates.

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

**Recorded partial validation evidence**

- the retained session evidence, its operator-confirmed-only results, the deliberately deferred gates,
  and the exposed-disposable-key warning are recorded in
  [`docs/TRUENAS_COMMUNITY_APP_DESIGN.md`](docs/TRUENAS_COMMUNITY_APP_DESIGN.md#vb-082-partial-validation-record--2026-09-02);
- the remaining-gate classification and sanitized copy/paste operator plan are recorded in
  [`docs/VB_082_TRUENAS_LIFECYCLE_RUNBOOK.md`](docs/VB_082_TRUENAS_LIFECYCLE_RUNBOOK.md), together
  with the sanitized 2026-09-08 execution evidence and result ledger;
- custom-YAML installation does not expose the catalog-generated Web UI / Portal button, so that
  historical pre-upstream limitation is preserved rather than treated as catalog evidence;
- all currently executable pre-upstream VB-082 gates are complete as of 2026-09-08; the retained
  Web Port evidence proves the new UI route loaded and showed Ready / 2 indexed notes plus an
  authenticated API response, but does not prove the stricter authenticated UI unlock/note-read
  subcheck after that historical port change;
- post-merge operator evidence confirms the initial real-catalog install/form/masking/Portal,
  Host Path, ixVolume configuration, healthy vault visibility, and rotation-migration checks listed
  in the status above, but does not prove edit-form persistence or ixVolume uninstall behavior;
- this partial record does not satisfy or remove any acceptance criterion above and does not complete
  VB-082.

### VB-083 — Submit VaultBridge to upstream TrueNAS Apps catalog — P1

**Status:** Completed on 2026-09-17. Upstream PR
[#5805](https://github.com/truenas/apps/pull/5805), **Add VaultBridge to the community train**, was
reviewed and merged on 2026-09-16 at `2026-09-16T20:04:07Z`; merge commit
`fd185603de32444f9e36f872dbcd84af44509115`. The accepted source exists under
`ix-dev/community/vaultbridge/`, the generated entry exists under
`trains/community/vaultbridge/1.0.0/`, and Discover Apps availability is operator-confirmed.

**Goal:** prepare and submit the verified VaultBridge Community App contribution to `truenas/apps`.

**Depends on:** VB-081 and completion of all executable pre-upstream VB-082 gates for the
submission/review phase. Those prerequisites were satisfied before PR #5805; VB-082 continues as a
separate post-merge lifecycle-validation task.

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

## MCP integration

### VB-090 — MCP integration architecture / ADR — P1 ✅

**Status:** Completed on 2026-09-07. The accepted design is recorded in
[`docs/adr/0004-mcp-integration.md`](docs/adr/0004-mcp-integration.md). No MCP server, dependency,
runtime route, configuration, image change, or write capability was implemented.

**Goal:** define how MCP becomes an additive first-class VaultBridge integration without replacing
REST or duplicating vault, semantic, indexing, authentication, or deployment behavior.

**Accepted decision**

- VB-091 is a read-only stdio-first MVP with `list_notes`, `read_note`, `search_notes`,
  `related_notes`, and `duplicate_candidates`;
- note content will also be available through a contained `vaultbridge://note/...` Resource template;
- Prompts, writes, Streamable HTTP, new ports, a permanent second service/container, and legacy
  HTTP+SSE are outside VB-091;
- future network MCP uses current-spec Streamable HTTP at opt-in `/mcp` in the existing FastAPI
  process/port with explicit Origin validation, Bearer authentication, and rate limiting;
- the official Python `mcp` v2 SDK is the selected bounded normal runtime dependency for VB-091;
- stdio starts no background indexer or synchronization and performs semantic queries only against a
  compatible ready persisted index through the existing read-only service boundary.

**Acceptance criteria**

- current authoritative MCP specification and official Python SDK sources are dated and referenced;
- transport, process, authentication, tool/Resource/Prompt, error, observability, configuration,
  packaging, compatibility, and dependency decisions are explicit;
- service ownership and the no-cross-process-index-writer constraint are preserved;
- the ADR defines exact stable tool schemas and a bounded VB-091 implementation contract;
- VB-032/VB-033, VB-075, and VB-081 through VB-083 retain their prior truth and scope.

### VB-091 — MCP server implementation — P1 ✅

**Status:** Completed on 2026-09-07. PR #55 pull-request CI passes. Its Python job passes the full
test suite and compile check. Its Docker job passes Compose validation, the Linux image build, MCP
dependency import inside that built image, and the MCP stdio EOF smoke. VB-091 did not replace or
alter VB-075's release-evidence criteria, which were completed separately on 2026-09-07.

**Dependency review:** the only declared MCP contract is `mcp>=2,<3`; validation used `mcp==2.1.1`
without the optional `mcp[cli]` extra. Its incremental local closure uses MIT/MIT-0, BSD-3-Clause,
Apache-2.0, and PSF license families. The measured installed footprint was approximately 20.9 MB
excluding Windows-only `pywin32` (approximately 20.3 MB more on this host). The exact Linux
production-image delta remains unmeasured because the PR #55 build did not capture a before/after
size comparison.

**Depends on:** VB-090.

**Goal:** implement the ADR 0004 read-only stdio MCP adapter over existing VaultBridge services.

**Acceptance criteria**

- use the official Python `mcp` v2 SDK through one bounded normal runtime dependency;
- provide explicit `python -m app.mcp_server` stdio startup with stdout reserved for MCP messages and
  safe structured logs on stderr;
- register exactly `list_notes`, `read_note`, `search_notes`, `related_notes`, and
  `duplicate_candidates`, plus the canonical read-only note Resource template;
- inject/reuse `VaultService`, `SemanticSearchService`, and `DuplicateCandidateService` directly;
  do not call REST through loopback or move MCP concepts into those services;
- do not start FastAPI, a listener, `BackgroundSemanticIndexer`, filesystem watcher,
  synchronization, rebuild, or any semantic-index write path;
- allow semantic tools only when the existing read-only persisted-index boundary confirms a
  compatible ready index; otherwise return safe retryable MCP execution errors;
- preserve validation, containment, symlink protection, Markdown-only access, result order/scores,
  and no-secret/no-content logging;
- apply one process-wide stdio operation budget through the existing fixed-window limiter primitive
  without claiming ASGI peer protection;
- add focused fake-service, schema, error, Resource containment/canonicalization, rate-limit,
  logging/stdout, lifecycle, and no-persistence-write tests;
- run the full Python/compile and changed-image dependency checks required by ADR 0004, while proving
  REST paths/operation IDs and existing clients remain unchanged.

**Out of scope**

- Streamable HTTP, `/mcp`, OAuth, write tools, Prompts, Resource subscriptions/catalogue
  notifications, index maintenance, a second service/container/image, and any API/dashboard/CLI
  behavior change;
- `semantic_search` as a duplicate alias for `related_notes`;
- create, append, overwrite, update, delete, backlink, or arbitrary filesystem operations.

### VB-092 — Opt-in read-only MCP Streamable HTTP transport — P1

**Status:** Implemented on 2026-09-17 with local Python, Ruff, compile, official MCP v2.1.1 and
v2.2.0 client, in-process protocol coverage, and disposable loopback runtime coverage.
The implementation host did not have Docker, so the original local evidence did not include
Docker/Compose validation. VB-093 subsequently completed production-image/container validation on
2026-09-18; neither task makes a release, catalog, or production TrueNAS runtime claim.

**Depends on:** VB-090 and VB-091.

**Goal:** expose the existing read-only MCP surface at opt-in `/mcp` inside the running FastAPI
process and port without adding a service, index owner, write capability, or transport fork.

**Acceptance criteria**

- add typed `MCP_HTTP_ENABLED`, `MCP_HTTP_ALLOWED_HOSTS`, and `MCP_HTTP_ALLOWED_ORIGINS` settings;
  remain absent by default and require explicit external Host/Origin values;
- mount the official MCP v2 Streamable HTTP ASGI app at exact `/mcp`, with no `/mcp/mcp`, legacy
  HTTP+SSE endpoint, REST OpenAPI entry, second port, service, or container;
- have the parent FastAPI lifespan enter and exit the SDK session manager while preserving indexer
  and watcher startup/shutdown ownership;
- reuse the exact application-owned `VaultService`, `SemanticSearchService`,
  `DuplicateCandidateService`, and `FixedWindowRateLimiter` objects;
- require the existing current/previous Bearer-key rotation verifier for every MCP HTTP request and
  use the direct ASGI peer budget once, without the stdio operation limiter double counting it;
- delegate Host/Origin and protocol framing validation to SDK `TransportSecuritySettings` and the
  current Streamable HTTP implementation;
- retain exactly the five read-only tools and contained Markdown Resource, with accurate `stdio` or
  `streamable-http` safe operation logs;
- preserve stdio trust, operation limiting, immutable semantic reads, and all REST/API/dashboard
  behavior and operation IDs;
- verify disabled/enabled routing, authentication, rate limiting, transport security, modern
  official-client tool/Resource calls, lifecycle, logging privacy, and compatibility.

**Out of scope**

- write tools, OAuth, Prompts, VaultBridge subscription features, standalone HTTP+SSE, a second
  semantic store/indexer, TrueNAS catalog changes, release/version selection, or publication.

### VB-093 — Container-level validation for MCP Streamable HTTP — P1

**Status:** Completed on 2026-09-18. PR #62 merged as `f03f48b`, and follow-up GitHub Actions run
`35355236057` passed both the Python and Docker jobs. The Docker job passed Compose validation, the
`vaultbridge:ci` image build, MCP dependency verification, the stdio MCP smoke, fresh disabled and
enabled MCP HTTP container smokes, the official Streamable HTTP client round trip, current/previous
authentication and invalid Bearer/Host/Origin checks, and clean container shutdown. This is
container CI evidence, not production TrueNAS runtime validation.

A separate disposable TrueNAS smoke built main commit `8ae99d3` from source and passed liveness,
authenticated REST listing of synthetic `Smoke.md`, an official MCP Python client connection over
Streamable HTTP protocol `2026-07-28`, the exact five read-only tools, and MCP `list_notes`. The
production app on port `30491` remained healthy, and the disposable app, image, vault, and data were
removed afterward. This did not use or validate the production vault.

**Depends on:** VB-092.

**Goal:** exercise the built production image with MCP HTTP disabled and enabled, including the
official MCP client and container-level authentication and transport-security failures.

**Acceptance criteria**

- preserve Compose validation, the `vaultbridge:ci` image build, MCP dependency verification, and
  MCP stdio smoke;
- run fresh disabled and enabled containers from that exact image with disposable vault and semantic
  data, loopback-only dynamically allocated host ports, and clean shutdown/cleanup;
- prove liveness and authenticated REST in both modes and prove `/mcp` is absent when disabled;
- use the official Python MCP client over Streamable HTTP to initialize, list exactly the five
  read-only tools, and list the synthetic Markdown note;
- reject missing and invalid Bearer credentials, invalid Host, and invalid present Origin at the
  container boundary, and accept `API_KEY_PREVIOUS`;
- do not publish an image or claim production TrueNAS validation.

---

## Obsidian Knowledge Graph / Note Relationships

This track derives a read-first relationship view from live Obsidian Markdown. Markdown remains
authoritative; relationship data is derived and non-authoritative. VB-100 through VB-105 perform no
note mutation and initially use live Markdown inspection rather than a persistent graph or link
index. VB-034 is the separately controlled, opt-in write task.

### VB-100 — Parse and safely resolve Obsidian wikilinks — P1 ✅

**Status:** Completed on 2026-09-21.

**Goal:** introduce one application/domain-level wikilink parser and resolver that later
relationship features can reuse without creating a second filesystem-security implementation.

**Dependencies:** existing `VaultService` path containment, Markdown verification, and canonical
live-path behavior.

**Acceptance criteria**

- parse at minimum `[[Note]]`, `[[Folder/Note]]`, `[[Note|Alias]]`, `[[Note#Heading]]`, and
  `[[Note#Heading|Alias]]`, preserving target, heading, and display alias as separate metadata;
- ignore wikilink-looking text inside fenced code blocks and handle Unicode, folders, malformed or
  incomplete links, and repeated links deterministically;
- resolve only exact, unambiguous Markdown targets; do not use fuzzy matching or guess among
  ambiguous candidates;
- route every actual target through the existing `VaultService` containment and live-Markdown
  verification boundary, returning its canonical vault-relative path when resolved;
- reject absolute and traversal targets, non-Markdown targets, directories, external symlinks, and
  broken symlinks; safe internal filesystem aliases may resolve only through the existing canonical
  path behavior;
- represent missing, unsafe, or ambiguous targets as unresolved without creating files or mutating
  Markdown or semantic storage;
- keep output ordering and resolution deterministic across repeated runs;
- add focused tests for normal syntax, aliases, headings, Unicode, folders, malformed links, fenced
  code, unresolved and ambiguous notes, traversal and absolute-path attempts, safe internal aliases
  where the host supports them, external and broken symlinks, and deterministic behavior.

**Out of scope**

- a persistent relationship/graph index, Neo4j, Qdrant, Redis, another database, or another service;
- REST endpoints, MCP tools, CLI behavior, dashboard UI, graph visualization, retrieval/ranking
  changes, note creation, backlink insertion, or any other Markdown mutation.

### VB-101 — Verified outgoing note relationships — P1 ✅

**Status:** Completed on 2026-09-21.

**Depends on:** VB-100

**Goal:** expose domain/service-level outgoing wikilink relationships from one live, contained
Markdown note.

**Acceptance criteria**

- read the source note only through `VaultService` and reuse the single VB-100 parser/resolver;
- return resolved and unresolved relationships distinctly, with canonical resolved paths and target
  heading/display-alias metadata where present;
- preserve deterministic source order and define deterministic handling of duplicate links;
- read no arbitrary or non-Markdown file and perform no write, reindex, or persistent graph update;
- add focused service tests for contained source validation, resolved/unresolved output, metadata,
  duplicates, ordering, size/read failures, and inherited VB-100 containment behavior.

**Out of scope:** REST, MCP, CLI, dashboard presentation, backlinks, ranking changes, graph
visualization, and note mutation.

### VB-102 — Verified backlinks — P1 ✅

**Status:** Completed on 2026-09-22.

**Depends on:** VB-100 and VB-101

**Goal:** determine which live, contained Markdown notes have verified outgoing relationships to a
requested live note.

**Acceptance criteria**

- verify the requested target and enumerate only eligible contained Markdown notes through existing
  vault boundaries;
- reuse VB-100/VB-101 resolution and include a source only when its link resolves canonically to the
  requested target, never from raw-text matching alone;
- return deterministic, deduplicated results and preserve useful heading/display-alias metadata;
- start with a measured live scan suitable for expected personal-vault sizes; document benchmark
  evidence before proposing a persistent link index;
- perform no writes and add focused tests for valid backlinks, same-name/ambiguous notes, unresolved
  raw matches, containment and symlink failures, deterministic ordering, and an empty result.

**Out of scope:** persistent graph/link storage, REST, MCP, dashboard UI, ranking changes, graph
visualization, and note mutation.

### VB-103 — REST and MCP note relationships — P1

**Status:** Complete.

**Depends on:** VB-101 and VB-102

**Goal:** expose the implemented outgoing-link and backlink services through stable, read-only
client adapters without adding a second relationship implementation.

**Proposed contract to confirm before implementation**

- REST: `GET /api/v1/notes/links?path=...` with operation ID `listNoteLinksV1`, and
  `GET /api/v1/notes/backlinks?path=...` with operation ID `listNoteBacklinksV1`;
- MCP: `note_links` and `note_backlinks` tools over the same injected domain services.

**Acceptance criteria**

- add only `/api/v1` REST routes; do not add legacy unversioned compatibility aliases without a
  separate explicit justification and migration decision;
- keep REST and MCP as thin adapters over the same VB-101/VB-102 services and response semantics;
- preserve existing Bearer authentication, rate limiting, validation, safe logging, MCP transport,
  and error boundaries;
- keep both operations read-only and verify schemas, operation-ID uniqueness, official MCP client
  behavior, and unchanged existing REST/MCP contracts with focused tests.

**Out of scope:** relationship parsing/resolution in adapters, writes, graph storage, CLI or dashboard
changes, graph visualization, and ranking changes.

### VB-104 — Dashboard note relationships — P2

**Status:** Complete.

**Depends on:** VB-103

**Goal:** add a small read-only outgoing-links/backlinks section for a note already selected or read
in the dashboard, using the existing authenticated backend capabilities.

**Acceptance criteria**

- call the VB-103 `/api/v1` endpoints through the existing authenticated fetch/session boundary;
- render bounded outgoing and backlink facts with explicit loading, empty, failure, logout, and
  stale-request behavior while preserving text-only dynamic rendering and current privacy rules;
- add no client-side relationship parsing, resolution, ranking, filtering, persistence, or mutation;
- verify keyboard, focus, responsive layout, long Unicode paths/aliases/headings, reduced motion,
  browser security, and current dashboard regressions through focused tests and browser acceptance.

**Out of scope:** graph visualization, editing, file management, an Obsidian replacement, a second
relationship implementation, and any API or note mutation.

### VB-105 — Evaluate graph-aware retrieval signal — P1

**Status:** Completed on 2026-09-23. Evaluation evidence does not support a production ranking
change.

**Depends on:** VB-102 and the existing retrieval evaluation/benchmark infrastructure.

**Goal:** measure whether verified Obsidian relationships improve retrieval quality relative to the
accepted semantic/lexical baseline.

**Acceptance criteria**

- define sanitized relationship-aware cases, metrics, latency/cost observations, and before/after
  evidence using the existing deterministic evaluation and benchmark paths;
- preserve the current semantic/lexical baseline and compare any graph signal against it without
  changing production weights or thresholds during evaluation;
- reject a production graph signal when evidence does not demonstrate useful quality improvement;
- if evidence supports a later ranking change, document weighting, failure behavior, rebuild/index
  compatibility, and live-versus-derived-data implications as a separate implementation decision;
- use only verified resolved relationships and add no persistent graph database/index merely for
  the evaluation.

**Out of scope:** an implicit production ranking change, model/chunking changes, graph storage or
services, REST/MCP/dashboard changes, graph visualization, and note mutation.

**Evidence and decision**

- four sanitized relationship-intent cases compare the unchanged semantic/lexical baseline with an
  evaluation-only verified one-hop interleaving candidate;
- deterministic and real-model runs both moved every expected related note from absent in the top
  five to rank 2 (Hit@3 `0%` to `100%`, MRR `0` to `0.5`), while the accepted VB-024 baseline stays
  unchanged;
- the real-model Windows AMD64 run added `55.481 ms` mean live relationship cost to a `93.903 ms`
  mean baseline query on only 17 notes; the existing 1,000-note backlink measurement also warns
  against synchronous live-scan scaling;
- conclusion: **not supported for production ranking** because the narrow quality gain does not
  establish acceptable cost or representative general-query benefit. No production weight,
  threshold, API, storage, model, chunking, or index behavior changed.

### VB-106 — MCP write parity and first-class TrueNAS MCP configuration — P1

**Status:** Implemented on 2026-09-23 and published in `v1.3.0`. Current upstream TrueNAS package
`1.0.2` selects image `1.3.0`, but delivery of the first-class MCP form fields remains separate.

**Goal:** add default-off MCP create/append parity with the protected REST note API and expose all
supported MCP HTTP settings as first-class TrueNAS package fields.

**Acceptance criteria**

- keep the exact seven-tool MCP surface when `MCP_WRITE_ENABLED=false`, and add only `create_note`
  and `append_note` when it is `true`;
- reuse `VaultService` write behavior, safe MCP error mapping, and the existing post-commit targeted
  semantic reindex rule without rolling back authoritative Markdown on queue failure;
- use the live application indexer for Streamable HTTP and an owned, cleanly shut down targeted
  indexer for write-enabled stdio without an unnecessary full synchronization;
- expose safe default-off HTTP/write toggles and explicit non-wildcard Host/Origin allowlists in the
  checked-in TrueNAS form and map them to the existing container and Web Port;
- preserve Bearer rotation, rate limiting, Host/Origin enforcement, storage permissions, REST/API
  compatibility, and the external upstream catalog boundary.

**Out of scope:** delete, overwrite/edit-in-place, arbitrary file management, VB-034 backlink
insertion, graph ranking/storage, OAuth, unauthenticated MCP, a second port, Apps SDK UI, and
OpenAI-specific protocol behavior.

---

## Portable PKM model

### VB-110 — Define portable PKM document model / ADR — P1 ✅

**Status:** Complete. ADR 0005 is accepted, and VB-111 through VB-114 are implemented under their
authoritative contracts.

**Goal:** define a portable, bounded domain model for Markdown notes, metadata, headings, and
relationships without introducing a second authoritative store or changing current runtime
behavior.

**Acceptance criteria**

- accept `docs/adr/0005-portable-pkm-document-model.md` as the conceptual contract for VB-111
  through VB-114;
- retain the verified canonical vault-relative Markdown path as note identity and keep containment,
  symlink protection, note-size enforcement, and note reads owned by `VaultService`;
- define title precedence, ordered headings, aliases, tags, bounded portable frontmatter, and a
  normalized relationship occurrence that preserves source, written target, resolved/unresolved
  state, dialect, type, fragment, label, source order, and dialect-specific metadata;
- distinguish authoritative Markdown and metadata stored in it from live parsed domain values and
  rebuildable projections; do not require persistence or a public schema for every conceptual
  field;
- specify deterministic source ordering, occurrence-preserving duplicate behavior, explicit exact
  deduplication views, and conservative ambiguity handling that never guesses among paths, aliases,
  or targets;
- specify strict UTF-8 input and explicit document, frontmatter, scalar, container-depth, and item
  bounds for later parsing;
- define safe single-document YAML behavior for duplicate keys, malformed delimiters, anchors,
  aliases, custom tags, executable constructors, nested maps/lists, supported scalar types, and
  unknown safe keys;
- keep note content available when metadata is malformed or unsupported unless the existing
  document safety boundary rejects the note, and never alter authoritative Markdown on a parse
  failure;
- preserve current REST, MCP, CLI, dashboard, `VAULT_PATH`, resource URI, class-name, semantic
  index, and packaging contracts; do not cosmetically rename `VaultService` or introduce generic
  `DocumentService` / `KnowledgeSpace` runtime abstractions.

**Out of scope:** runtime Python or parser implementation; dependencies; REST, MCP, CLI, or
dashboard changes; database, semantic-index, or persistent-graph changes; production relationship
ranking; multiple knowledge spaces; VB-034 backlink insertion; TrueNAS packaging/lifecycle work;
release work; and authoritative task contracts or implementation for VB-111 through VB-114.

---

### VB-111 — Implement bounded YAML frontmatter parser — P1

**Status:** Completed on 2026-09-27. `FrontmatterParser` accepts only decoded Markdown, recognizes
the exact bounded envelope, and returns immutable `absent`, `valid`, or privacy-safe `invalid`
results. It uses PyYAML's event parser without object construction or implicit resolution, while
VaultBridge owns YAML 1.2 Core scalar resolution, feature rejection, duplicate detection, ordering,
and every ADR 0005 bound. No adapter, title, persistence, relationship, or write behavior changed.

**Goal:** add a small read-only domain parser for bounded, safe YAML frontmatter in Markdown content
already returned by `VaultService`, following ADR 0005 without changing any public behavior.

**Authoritative implementation contract**

- The parser accepts decoded Markdown content only. It never accepts, resolves, or reads a path;
  containment, symlink protection, Markdown validation, `max_note_bytes`, and UTF-8 decoding remain
  owned by `VaultService`.
- After an optional leading UTF-8 BOM, frontmatter exists only when the first line is exactly `---`.
  The first subsequent line exactly equal to `---` or `...` closes it; delimiter line endings may be
  LF or CRLF, and a closing delimiter may end at EOF. Any other text is ordinary Markdown. An exact
  opener with no exact closer inside the frontmatter byte limit is invalid frontmatter, not hidden
  Markdown.
- The complete envelope from the opening delimiter through the closing delimiter, including their
  line endings when present but excluding the optional preceding BOM, is at most `65,536` UTF-8
  bytes. The existing `VaultService.max_note_bytes` remains the whole-note limit. A
  scalar's source representation is at most `8,192` UTF-8 bytes; a string mapping key is at most
  `256` UTF-8 bytes; container depth is at most `8`, with the root mapping at depth `1`; and the
  aggregate number of mapping entries plus sequence items is at most `1,024`. Checks must not
  require alias expansion or another unbounded intermediate representation.
- Parse exactly one YAML document whose root is a mapping. Nested mappings and sequences are
  supported within the bounds, mapping and sequence order is preserved, and unknown safe keys are
  retained. Mapping keys must be strings. Values are limited to strings, null, booleans, integers,
  finite floating-point numbers, mappings, and sequences.
- Implicit values follow YAML 1.2 Core Schema exactly: `true`/`false` are booleans; `null`/`~` are
  null; `yes`, `no`, `on`, and `off` are strings; date-like plain scalars such as `2026-09-26` are
  strings; and `0123` never has YAML 1.1 octal meaning and, when resolved numerically, is decimal
  `123`. The implementation must configure or verify the selected parser against these cases and
  must not silently coerce incompatible types.
- Reject the entire block for invalid YAML, a non-mapping root, duplicate keys at any mapping depth,
  a second YAML document, anchors, aliases, merge keys, explicit or custom tags, unsafe/general
  object construction, non-string mapping keys, timestamps/dates, binary or set values,
  language-specific or otherwise unsupported values, non-finite floats, or any exceeded bound.
- Return one immutable/read-only domain result in exactly one state: `absent`; `valid`, including a
  valid empty mapping and its ordered bounded metadata; or `invalid`, with no metadata. These states
  remain distinct from an unavailable/rejected note at the existing `VaultService` boundary.
- An invalid result carries one bounded diagnostic with one of these stable reason codes:
  `malformed_envelope`, `invalid_yaml`, `non_mapping_root`, `duplicate_key`, `multiple_documents`,
  `disallowed_yaml_feature`, `non_string_key`, `unsupported_value`, `frontmatter_too_large`,
  `scalar_too_large`, `mapping_key_too_large`, `container_too_deep`, or `too_many_items`. It may also
  carry one-based line and column integers relative to the YAML payload when safely available. No
  diagnostic or log may include a metadata key/value, parser excerpt, YAML snippet, or note content.
- Invalid or absent frontmatter never makes an otherwise readable note unreadable. Parsing is
  deterministic and read-only: it never rewrites, repairs, truncates, normalizes, or otherwise
  modifies Markdown.

**Implementation and tests**

- Choose the smallest safe YAML dependency or parsing approach that satisfies this contract;
  dependency selection belongs to the implementation change and must follow repository dependency
  policy. Do not use an unsafe loader or general object constructor.
- Keep ownership in a focused parser and the minimum domain result types; do not add a generic
  metadata/document service or publish the parser through REST, MCP, CLI, or dashboard surfaces.
- Test every envelope rule, result state, rejection class, exact bound (including just-below/at/over
  cases), YAML 1.2 scalar case above, order preservation, and privacy-safe diagnostics. Tests must
  prove malformed metadata leaves the original decoded Markdown available and unchanged.

**Compatibility / explicit non-goals:** existing REST and operation IDs, MCP tools/resources, CLI,
dashboard, semantic search/ranking/model/chunking/index signature, duplicate candidates, title and
relationship behavior, SQLite/schema/persistence/cache/graph storage, authentication, note writes
and frontmatter editing, Docker/TrueNAS/release artifacts, and all public schemas remain unchanged.
VB-111 does not implement alias/tag projection (VB-112), standard Markdown relationship parsing
(VB-113), the normalized relationship view (VB-114), multiple knowledge spaces, or any new endpoint,
response field, tool, resource, or UI capability.

---

### VB-112 — Implement portable aliases and tags projection — P1 ✅

**Status:** Completed on 2026-09-27. `project_portable_fields` derives independent immutable alias
and tag results from valid VB-111 metadata, with exact occurrence ordering, field-local bounds, and
bounded privacy-safe diagnostics. It does not change title, relationship, adapter, persistence,
semantic, write, or deployment behavior.

**Goal:** add the smallest immutable, read-only domain projection for portable `aliases` and `tags`
from the already-valid generic metadata produced by VB-111, following ADR 0005 without changing
canonical identity, title selection, relationships, public behavior, persistence, or Markdown.

**Authoritative implementation contract**

- Consume only a VB-111 `FrontmatterResult` in the `valid` state and read only the top-level keys
  exactly named `aliases` and `tags`. Do not parse YAML again or create a second metadata parser.
  Absent or invalid frontmatter produces no portable-fields projection and must not be
  reinterpreted as two valid empty fields. Within valid frontmatter, a missing key is the distinct
  `absent` state for that field.
- Represent each field independently with an immutable/read-only `absent`, `valid`, or `invalid`
  result. A valid result carries its ordered usable occurrences and any empty-value diagnostics; an
  invalid result carries no occurrences. Invalid `aliases` must not affect `tags`, invalid `tags`
  must not affect `aliases`, and neither can change the valid VB-111 frontmatter result or its
  generic metadata mapping.
- A present field accepts exactly one string or the VB-111 sequence representation containing only
  strings. A scalar string is one source value at source index `0`; sequence indexes are the source
  indexes. An empty sequence is valid with zero occurrences. A non-string scalar produces an
  invalid field, and any non-string sequence member produces an invalid field. Numbers, booleans,
  null, mappings, nested sequences, and other containers are never coerced to strings.
- Count source values before empty-value omission: the scalar form has count `1`, and the sequence
  form has its original length. Counts `255` and `256` are within the field limit; count `257` is
  over the limit and makes only that portable field invalid. More generally, every count from `0`
  through `256` passes the count check and any count greater than `256` is invalid.
- After type and count validation, use Python `str.strip()` only to decide whether each source
  string is empty. An empty or whitespace-only value is omitted from occurrences and produces an
  `empty_value` diagnostic without invalidating the otherwise well-typed field. A field containing
  only such values is therefore `valid`, with zero occurrences and diagnostics. For every usable
  value, `len(value.encode("utf-8"))` of `1,023` or `1,024` bytes is accepted and `1,025` bytes is
  over the limit; more generally, at most `1,024` UTF-8 bytes is accepted and any larger usable
  value makes only that portable field invalid. Empty-value classification occurs before this
  usable-value size check.
- Preserve every usable string exactly as held in VB-111 metadata, including surrounding
  whitespace, case, Unicode code points, and a written leading `#`. Preserve source order and
  repeated values in the primary occurrence view; do not silently deduplicate. The projection must
  never rewrite or mutate the generic metadata mapping, its nested values, or source Markdown.
- Do not add case folding, Unicode compatibility normalization, hierarchy expansion, or automatic
  `#` insertion/removal. A convenience set-like view is not required. If a future implementation
  includes one in this task, it must trim surrounding whitespace for exact comparison only, retain
  the first occurrence, and otherwise preserve these same non-normalizing rules.

**Diagnostics and deterministic validation**

- A portable-field diagnostic has only a stable reason, the allowlisted field name (`aliases` or
  `tags`), and an optional zero-based source index. The complete reason vocabulary is
  `field_type`, `member_type`, `source_value_count`, `value_size`, and `empty_value`. It contains no
  source value, metadata content, free-form parser text, or runtime type representation.
- Validate in this order: accepted outer field form; source-value count; member types in ascending
  source-index order; usable-value byte sizes in ascending source-index order; then construct the
  occurrence output and empty-value diagnostics. `field_type` and `source_value_count` have no
  source index; `member_type`, `value_size`, and `empty_value` identify the relevant source index.
- An invalid field exposes exactly one diagnostic for the first failure under that order. An absent
  field has no diagnostics. A valid field exposes one `empty_value` diagnostic for each omitted
  source occurrence, in source order, so diagnostics are bounded to at most `256` per valid field.
  Diagnostic objects and collections are immutable/read-only and must never include the offending
  alias or tag text.

**Alias and tag semantics**

- Aliases are portable lookup candidates only. They never become canonical note identity, override
  the verified canonical vault-relative path, change title behavior, resolve or select a note, or
  alter relationship resolution in VB-112. Any future alias lookup with zero or multiple live
  canonical-path candidates remains unresolved; alias resolution/search requires a separately
  authorized task.
- Tags come only from the valid top-level frontmatter `tags` field under the rules above. VB-112
  adds no inline Markdown `#tag` parsing, nested or hierarchical expansion, leading-`#`
  normalization, case normalization, or tag search/filter/query behavior.

**Implementation and future tests**

- Extend the VB-111 frontmatter/domain layer with only the minimum projection and immutable result
  types needed to own these invariants. Do not duplicate its parser or move filesystem, containment,
  decoding, generic metadata, title, relationship, adapter, or persistence ownership into this
  projection.
- Add focused tests for absent fields and absent/invalid frontmatter; scalar and sequence forms;
  field independence; non-string scalars and members, including mappings and nested sequences;
  empty and whitespace-only values, including an all-empty valid field; ordering and duplicates;
  source counts `255`/`256`/`257`; usable sizes `1,023`/`1,024`/`1,025` UTF-8 bytes with ASCII and
  multibyte strings; exact value preservation without coercion, case folding, Unicode
  normalization, hierarchy expansion, or `#` changes; unchanged generic metadata; and diagnostic
  reason, ordering, cardinality, immutability, and privacy bounds.
- Regression tests must prove existing title, REST, MCP, CLI, dashboard, semantic, relationship,
  persistence, and write behavior remains unchanged.

**Compatibility / explicit non-goals:** VB-112 adds no alias resolution; title projection or title
behavior change; tag search/filter/query behavior; inline Markdown tags; standard Markdown links
(VB-113); normalized relationships (VB-114); REST endpoint, operation ID, request/response schema,
MCP tool/resource, CLI, or dashboard change; SQLite/schema/persistence/cache/index change; semantic
ranking, embedding, model, chunking, or index-signature change; note write or frontmatter editing;
multiple knowledge spaces; dependency, Docker, TrueNAS, packaging, publication, or release change.
Existing public and deployment behavior remains unchanged.

---

### VB-113 — Implement contained standard Markdown note relationships — P1 ✅

**Status:** Completed on 2026-09-27. `MarkdownLinkResolver` provides bounded inline note-link
parsing and source-relative resolution through `VaultService`; additive `RelationshipService`
methods derive outgoing Markdown relationships and verified backlinks without changing existing
wikilink-backed adapters. Focused and full non-E2E validation pass. VB-114 later added the shared
normalized domain view without changing these dialect-specific methods.

**Goal:** add a bounded parser/resolver for inline standard Markdown links that target contained
Markdown notes, plus domain-only outgoing/backlink derivation through `RelationshipService`.
Standard Markdown links are supported alongside existing Obsidian wikilinks and do not replace or
reinterpret VB-100 through VB-104 behavior. REST, MCP, CLI, and dashboard relationship behavior
remain unchanged until a later task deliberately adopts a normalized multi-dialect view.

**Supported Markdown-link profile**

- Parse decoded Markdown in source order for `[Label](Note.md)`, `[Label](Folder/Note.md)`,
  `[Label](../Note.md)`, `[Label](Note.md#Heading)`, `[Label](<My Note.md>)`, and
  `[Label](<Folder/My Note.md#Heading>)`, including raw Unicode labels, paths, and fragments.
- This is deliberately not a full CommonMark parser. Support inline links only. Ignore image,
  reference-style, autolink, HTML-link, footnote, and wiki syntax; ignore links inside fenced code
  and inline code spans; preserve duplicate valid occurrences.
- Optional link titles such as `[Label](Note.md "Title")` are unsupported and must not become part
  of a filename. Non-angle destinations containing unescaped whitespace are unsupported, while
  angle-bracket destinations may contain spaces. Ignore malformed, incomplete, or NUL-containing
  syntax.
- Do not percent-decode or perform URI normalization. `%xx` remains literal source text.
- Preserve the written destination/path, optional written fragment after the first `#`, written
  label, optional verified canonical `resolved_path`, result order, and duplicates in the smallest
  immutable occurrence type. Do not case-fold, slugify, Unicode-normalize, title-resolve,
  alias-resolve, or rewrite these values.
- VB-114 owns the future shared `RelationshipOccurrence` shape. VB-113 must not replace existing
  wikilink types with that future model.

**Note-target classification and resolution**

- A candidate must have a non-empty local path portion ending in `.md`; a fragment may follow the
  path after the first `#`. Empty paths and fragment-only targets are not candidates.
- Exclude `http:`, `https:`, `mailto:`, all other URI schemes, `//host/...`, absolute POSIX paths,
  Windows drive/UNC paths, and non-Markdown destinations.
- Keep syntactically valid local `.md` targets as unresolved relationships when the destination is
  missing, escapes containment, is a directory, is an external/broken symlink, or fails exact-path
  verification.
- Resolve standard Markdown paths relative to the directory of the verified canonical source note:
  `Folder/Source.md` plus `Sibling.md` targets `Folder/Sibling.md`, and `../Root.md` targets
  `Root.md` only when it remains contained. Do not use wikilink global filename lookup.
- `VaultService` remains the sole owner of canonical path identity, containment, exact spelling,
  file/type checks, and symlink safety. Safe internal symlinks use existing canonical behavior;
  external and broken symlinks remain unresolved.
- Fragments are occurrence metadata only. Do not verify heading existence or implement fragment
  slug rules. Do not use VB-112 aliases, titles, tags, frontmatter mutation, or metadata heuristics
  for target resolution.

**RelationshipService integration**

- Add only additive domain methods for outgoing standard Markdown note-link relationships from one
  verified source and backlinks to one verified target. Existing public wikilink-backed
  `outgoing_relationships()` and `backlinks()` remain unchanged.
- Source reads and target verification go through `VaultService`; results are immutable and
  deterministic. Outgoing occurrences preserve source order and duplicates. Backlink sources use
  deterministic canonical enumeration.
- Include a backlink only when the occurrence's verified canonical `resolved_path` equals the
  requested canonical target, never by raw-text matching. Deduplicate stably only exact identical
  source + written destination + fragment + label occurrences.
- Keep parsing and relationship derivation read-only and deterministic. Use bounded scanning rather
  than catastrophic/backtracking regular expressions; do not read host files in the parser,
  perform network access, decode percent escapes, guess malformed syntax, or select ambiguous
  targets.

**Required tests**

- Parsing covers root/folder/parent-relative links, fragments, angle destinations with spaces,
  Unicode, duplicates/order, fenced and inline code exclusion, images and excluded syntaxes,
  optional titles, malformed/incomplete/NUL input, external/network/absolute targets, and
  non-Markdown destinations.
- Resolution covers source-relative siblings, contained parent traversal, vault escape, exact case,
  missing and directory targets, supported internal symlinks, external/broken symlinks, unverified
  fragments, literal percent escapes, and absence of alias/title fallback.
- Relationship tests cover VaultService-owned reads, resolved/unresolved output, duplicate/order
  preservation, canonical-only backlink inclusion, deterministic backlink order and deduplication,
  and unchanged existing wikilink relationships.
- Regression verification must keep existing REST/OpenAPI operation IDs and responses, MCP tools
  and resources, CLI/dashboard behavior, VB-111/VB-112 behavior, semantic/index/persistence
  behavior, and writes unchanged.

**Compatibility / explicit non-goals:** no shared normalized multi-dialect relationship view
(VB-114); public REST/MCP/CLI/dashboard exposure of Markdown-link relationships; alias/title target
resolution; reference links or full CommonMark; percent-decoded paths; fragment heading validation;
relationship persistence or graph/index storage; semantic ranking; writes/backlink insertion;
multiple knowledge spaces; dependency, Docker, TrueNAS, packaging, publication, or release work.

---

### VB-114 — Implement normalized multi-dialect relationship view — P1

**Status:** Completed on 2026-09-27. `RelationshipOccurrence` and additive `RelationshipService`
methods now normalize both existing dialects with true mixed source order and explicit bounded
resolution reasons. `VaultService` owns typed containment/path classification, legacy and public
adapters remain unchanged, and focused/full non-E2E plus real-symlink WSL validation pass.

**Goal:** normalize the already-supported Obsidian wikilink and standard Markdown-link dialects
into one immutable, live-derived domain view. Markdown remains authoritative, canonical
vault-relative path remains note identity, `VaultService` retains filesystem-safety ownership, and
`RelationshipService` owns relationship-domain composition.

**Normalized occurrence**

- Add one immutable occurrence type containing `source_path`, `written_target`, optional verified
  canonical `resolved_path`, explicit `resolution`, `origin`, `relationship_type`, optional
  `fragment`, optional `label`, zero-based cross-dialect `source_order`, and immutable bounded
  `origin_metadata`.
- `resolution` must distinguish at least `resolved`, `missing`, `ambiguous`, and `unsafe` without
  exposing absolute host paths, exception strings, symlink destinations, metadata content, or
  other private filesystem details. `ambiguous` applies only when current unqualified wikilink
  exact-name semantics find multiple valid candidates. `unsafe` covers traversal, absolute,
  external or broken symlink, out-of-vault, and other security-invalid targets. A safe supported
  local target without one exact live Markdown note is `missing`.
- `origin` is exactly `obsidian_wikilink` or `markdown_link`, and is part of relationship identity.
  `relationship_type` is exactly `note_link`. Keep `origin_metadata` minimal and use one shared
  immutable empty representation when no additional dialect-specific fact is required.
- Preserve duplicates and raw Unicode. Do not case-fold, normalize, slugify, or rewrite written
  target, fragment, or label values.

**Dialect mapping and source order**

- Wikilinks map `Wikilink.target`, `heading`, `alias`, and `resolved_path` to `written_target`,
  `fragment`, `label`, and `resolved_path`, with origin `obsidian_wikilink`.
- Standard Markdown links map `MarkdownLink.destination`, `fragment`, `label`, and `resolved_path`
  to the corresponding normalized fields, with origin `markdown_link`.
- Preserve true document order across both dialects, including alternating and same-line
  occurrences; do not concatenate all wikilinks before all Markdown links. Assign contiguous
  zero-based `source_order` across the combined supported occurrences.
- Reuse the existing dialect parsers. If merging requires source positions, add only the smallest
  internal immutable fact without changing existing semantic equality or public compatibility. Do
  not add a third parser.
- Preserve current wikilink global exact-name/ambiguity semantics and current Markdown-link
  source-relative exact-path semantics. Unsupported or malformed syntax remains excluded by its
  owning parser and does not become a normalized error occurrence.

**Resolution and ownership**

- `VaultService` remains the sole owner of containment, exact-path verification, canonicalization,
  Markdown file/type checks, and symlink safety. If explicit classification is needed, add only a
  small internal typed verification result/helper there and reuse it from both dialect resolvers.
- Safe internal symlinks resolve to the canonical contained target. External and broken symlinks
  are unsafe. Do not infer filesystem reasons from host exceptions in parsers.
- Do not use aliases, titles, tags, heading existence, frontmatter mutation, or metadata heuristics
  for resolution.

**RelationshipService integration**

- Add additive methods for normalized outgoing relationships from one verified source note and
  normalized backlinks to one verified target note. Existing `outgoing_relationships()`,
  `backlinks()`, `outgoing_markdown_relationships()`, and `markdown_backlinks()` outputs and
  behavior remain unchanged.
- Outgoing normalization reads the source through `VaultService`, derives both dialects from the
  same content snapshot, parses each dialect at most once, preserves duplicates, and returns one
  immutable tuple in true source order.
- Normalized backlinks verify the requested target through `VaultService`, enumerate source notes
  in deterministic canonical order, derive normalized outgoing occurrences from each source
  snapshot, and include only occurrences whose verified `resolved_path` equals the requested
  canonical target. Raw-text or unresolved matches never count.
- Backlink order is deterministic source-note order followed by source occurrence order. Stable
  deduplication removes only exact duplicate normalized occurrences. Wikilink and Markdown-link
  occurrences that resolve to the same note remain distinct because origin is part of identity.
- The normalized view is derived live from current Markdown. Add no persistence, cache, graph or
  relationship index, and perform no extra full-vault scan beyond the normalized backlink operation.

**Required tests**

- Cover exact field mapping for both dialects; resolved, missing, ambiguous, and unsafe outcomes;
  fragments, labels, and Unicode; immutable result/model/origin metadata; no alias/title/tag
  lookup; safe internal symlinks; and external/broken symlinks without host-detail leakage.
- Cover alternating dialects, multiple same-line and multi-line occurrences, duplicates,
  deterministic repeated runs, and contiguous true cross-dialect source order.
- Cover normalized backlinks from both dialects, both origins from one source, verified-path-only
  inclusion, deterministic source/path ordering, exact deduplication, and preserved source order.
- Prove existing wikilink, Markdown-link, legacy relationship, REST/OpenAPI, MCP, CLI, dashboard,
  VB-111, VB-112, semantic/index/persistence, and write behavior remains unchanged.

**Compatibility / explicit non-goals:** no REST/OpenAPI endpoint, operation ID, schema, MCP tool or
resource, CLI, or dashboard exposure; no alias/title resolution, tag relationships, heading
validation, persistence/cache/graph storage, semantic ranking/index-signature changes, note writes
or backlink insertion, multiple knowledge spaces, dependency, Docker, TrueNAS, packaging,
publication, or release work. VB-114 does not implement Milestone 13, VB-120, or any later task.

---

## Knowledge Query Layer

### VB-120 — Define bounded Knowledge Query capability / ADR — P1 ✅

**Status:** Completed on 2026-09-27. ADR 0006 is accepted as the authoritative domain contract for
bounded composition of live literal, path, portable-tag, frontmatter, normalized-relationship, and
existing semantic-search constraints. No runtime query engine, public adapter, persistence, ranking,
index-signature, dependency, infrastructure, or write behavior was added.

**Goal:** define one finite, client-agnostic Knowledge Query domain capability over the owners
established by ADR 0005 and VB-111 through VB-114. Markdown remains authoritative; every parsed,
relationship, semantic, cache, and query projection remains derived and rebuildable.

**Acceptance criteria**

- accept `docs/adr/0006-bounded-knowledge-query-capability.md` as the semantic contract for a future
  VB-121 runtime task;
- preserve `VaultService` ownership of containment, exact spelling, verified canonical
  vault-relative paths, safe reads, note bounds, and symlink handling; preserve the existing
  frontmatter/portable-field layer as metadata owner, `RelationshipService` as normalized
  relationship owner, and `SemanticSearchService` as semantic availability/ranking owner;
- define one conceptual immutable request with optional semantic text, optional exact literal text,
  recursive folder scope, a bounded exact-path tuple, required portable tags, bounded top-level
  metadata predicates, bounded normalized relationship predicates, and a visible result limit;
- define one conceptual immutable result containing ordered verified canonical paths, existing
  semantic score evidence only in semantic mode, deterministic ordering mode, and a bounded
  semantic-index-basis fact that does not claim freshness;
- allow text-free queries only when at least one structural or metadata constraint exists; reject an
  empty effective request and compose all constraints with AND except the bounded path tuple and
  optional relationship-origin choice defined by ADR 0006;
- define literal text as an exact case-sensitive Unicode substring live-content filter, never a
  ranking signal, regex, glob, normalization, or token language;
- define segment-aware recursive folder scope and exact canonical Markdown path identity through
  `VaultService`; unsafe scopes fail safely, while safely missing scopes match nothing;
- define tag matching from valid VB-112 tags after surrounding-whitespace trimming, with exact
  case-sensitive/non-normalized Unicode, literal `#`, stable duplicate handling, and no alias/title
  resolution;
- limit metadata to exact top-level keys and the finite `exists`, `equals`, `not_equals`, and
  `sequence_contains` operators over exact portable scalar types; absent/invalid frontmatter,
  nested traversal, coercion, recursive sequence search, and arbitrary YAML paths do not match;
- limit relationship predicates to resolved normalized `note_link` occurrences, canonical
  `other_path`, incoming/outgoing direction, and optional exact origin; unresolved relationships,
  written targets, fragments, labels, source order, and origin metadata are not queryable;
- require live path/content/metadata/tag/relationship constraints to establish eligibility before
  semantic selection; preserve the current semantic threshold, hybrid weights, relative floor,
  per-note aggregation, scores, tie-breaks, model, chunking, embedding input, and index signature;
- use `(path.casefold(), path)` ordering without semantic text and the existing semantic score chain
  plus canonical path with semantic text;
- define conservative non-atomic consistency: current Markdown owns live facts; compatible previous
  semantic data may remain searchable during refresh or after failure; stale deleted/unsafe notes
  are excluded; unindexed live notes cannot appear in semantic mode; unreadable/racing notes are
  omitted without unbounded retries or private diagnostics;
- fail a semantic request explicitly when no compatible searchable semantic index exists rather
  than silently changing query mode; keep non-semantic queries independent of index availability;
- enforce ADR 0006's exact request bounds: default/max visible limit `20`/`100`, `64` path scopes,
  `16` tags, `16` metadata predicates, `16` relationship predicates, bounded text/key/path/value
  sizes, signed-64-bit/finite-binary64 numeric request values, and at most
  `min(500, max(limit * 5, limit))` post-eligibility semantic candidates;
- reject arbitrary SQL/FTS, host-path predicates/globs/regex, YAML-path/JMESPath/JSONPath-style
  execution, user expressions, recursive boolean DSLs, and adapter-specific query semantics;
- require privacy-safe reason codes/aggregates that expose no absolute host path, symlink target,
  exception string, query or metadata value, note content, SQL, embedding, or storage detail;
- keep any later measured query projection derived, fingerprinted, compatibility/invalidation
  governed, and rebuildable from Markdown; authorize no projection or persistence in VB-120;
- require a separately accepted VB-121 BACKLOG contract and evaluation covering semantic, literal,
  scope, tag, metadata, relationship, mixed, failure, ordering, race, stale-index, English/Slovak,
  and bounds cases plus no regression of the accepted VB-022/VB-024 retrieval baseline.

**Compatibility / explicit non-goals:** VB-120 is documentation and architecture only. It adds no
runtime `KnowledgeQueryService` or query engine and does not implement VB-121 or VB-122. No
REST/OpenAPI endpoint, schema, or operation ID; MCP tool/resource; CLI command; dashboard behavior;
public JSON schema; dependency; database/schema/cache/index/graph/vector store; Redis, Celery, Qdrant,
background worker, or external service; ranking/model/chunking/index-signature change; metadata or
relationship persistence; note write; alias/title resolution; Docker/TrueNAS/package/release change;
multiple knowledge spaces; or Milestone 14+ work is authorized.

---

### VB-121 — Implement bounded Knowledge Query runtime and evaluation — P1

**Status:** Completed on 2026-09-28. The immutable domain runtime implements ADR 0006 through the
existing vault, frontmatter, relationship, and semantic owners. Focused owner/compatibility tests,
the unchanged 13-case retrieval baseline, full non-E2E tests, and real-symlink WSL validation pass.
No public adapter, persistence, dependency, write, or VB-122 behavior was added.

**Goal:** add one immutable, bounded, client-agnostic runtime capability that composes current live
Markdown facts from `VaultService`, VB-111/VB-112 metadata and portable tags, VB-114 normalized
relationships, and the existing `SemanticSearchService` ranking contract. Markdown remains
authoritative and semantic SQLite data remains derived and rebuildable.

**Domain boundary and safe failures**

- Add the smallest domain module, normally `app/services/knowledge_query.py`, with frozen request,
  predicate, match, and result values. `KnowledgeQuery` contains optional non-empty
  `semantic_text`, `literal_text`, and vault-relative `folder`; ordered bounded tuples of exact
  Markdown `paths`, required portable `tags`, `MetadataPredicate`, and `RelationshipPredicate`;
  and a positive visible `limit` defaulting to `20`.
- `MetadataPredicate` contains an exact top-level key, one of `exists`, `equals`, `not_equals`, or
  `sequence_contains`, and a value representation that distinguishes omission from supplied YAML
  null. `RelationshipPredicate` contains `outgoing` or `incoming`, an exact Markdown `other_path`,
  and optional `obsidian_wikilink` or `markdown_link` origin.
- `KnowledgeQueryResult` contains an immutable ordered tuple of `KnowledgeQueryMatch`, ordering
  `semantic` or `canonical_path`, and semantic-index basis `none`, `compatible_ready`,
  `compatible_previous_refresh`, or `compatible_previous_error`. A match exposes only verified
  canonical path and, in semantic mode, the existing final, semantic, and lexical scores.
- Use domain types, not Pydantic HTTP schemas. Expose bounded expected failure categories for
  `invalid_request`, `unsafe_scope`, and `semantic_unavailable`. Their messages, reprs, and logs
  must not expose host paths, symlink destinations, exception strings, query/literal text, tag or
  metadata values, note content, SQL, embeddings, or storage details. Programming errors are not
  converted into expected failures.

**Validation and exact bounds**

- Validate cheap shape, cardinality, UTF-8, and numeric constraints before vault enumeration,
  reads, relationship parsing, or embedding. Cardinality is checked before stable deduplication.
- Enforce visible limit default/max `20`/`100`; at most `64` paths and `16` each of tags, metadata
  predicates, and relationship predicates; semantic/literal text at most `4,096`/`8,192` UTF-8
  bytes; folder/path/relationship path at most `1,024`; metadata key at most `256`; and tag or
  string predicate value at most `1,024` UTF-8 bytes. Integer request values are signed 64-bit;
  float request values are finite IEEE-754 binary64.
- Reject non-positive or excessive limits; supplied empty text/folder; empty-after-trim tags; an
  empty effective query; unknown operators, directions, or origins; `exists` with a value; another
  metadata operator without a value; unsupported metadata types/containers; non-finite floats;
  out-of-range ints; and over-bound values. Do not coerce or stringify. Semantic/literal whitespace
  is content and is not trimmed.

**Scope and one-read live evaluation**

- Start with one deterministic live Markdown enumeration. `VaultService` remains sole owner of
  containment, traversal/absolute-path rejection, exact spelling, canonical vault-relative
  Markdown identity, safe reads, size/UTF-8 bounds, and symlink safety. Add only a minimal typed
  folder classifier if needed.
- Folder scope is recursive and segment-aware. A safely missing folder yields no matches; an
  unsafe, escaping, invalid, or broken-symlink folder fails with `unsafe_scope`; discovered spelling
  is exact; omission means vault root. Each `paths` entry is an exact Markdown identity OR choice:
  safely missing branches match nothing, unsafe paths fail the whole request, valid duplicates are
  stably deduplicated, and folder plus paths intersect. No alias/title/filename heuristic, glob,
  regex, or case-insensitive lookup is permitted.
- For each cheap-scope survivor, verify/read once through `VaultService`; reuse that content for
  literal, one frontmatter parse, VB-112 tag projection, metadata, and outgoing normalized
  relationship evaluation. A racing, missing, unsafe, unreadable, oversized, or invalid-UTF-8
  candidate is conservatively omitted without retry. Verify the canonical path again before final
  inclusion and omit stale/deleted/unsafe notes.

**Live predicates**

- Literal text is an exact case-sensitive Unicode code-point substring over complete decoded
  Markdown, including frontmatter source. It is filter-only: no case folding, normalization,
  regex/glob, tokenization, stemming, rendering, or ranking effect.
- Tags come only from `project_portable_fields(FrontmatterParser.parse(content)).tags`. All are
  required. Trim request tags and stored occurrences for exact case-sensitive/non-normalized
  comparison; `#` is literal. Stable-deduplicate request tags after trim and after cardinality
  validation. Invalid, absent, or unusable tags do not match; aliases/titles are not consulted.
- Metadata uses only the valid VB-111 top-level mapping. `exists` requires the exact key regardless
  of portable value shape. `equals` requires the same exact portable scalar type and value.
  `not_equals` additionally requires the stored scalar to be present and unequal. `sequence_contains`
  requires one immediate scalar member of the same exact type and value. Keys are exact and never
  nested paths; strings are not trimmed/normalized; bool differs from int and int from float;
  mappings and nested sequences support only `exists`; all predicates compose with AND; absent or
  invalid frontmatter matches none. Generic `title`, `aliases`, and `tags` keys retain these generic
  semantics rather than invoking projections.
- Relationship predicates use only resolved VB-114 `note_link` occurrences with verified canonical
  `resolved_path`. Validate each unique fixed `other_path` once: safely missing means that predicate
  matches none, unsafe fails the request, valid paths use canonical identity. Outgoing matches one
  candidate occurrence resolving to `other_path`; incoming matches an occurrence from `other_path`
  resolving to the candidate; optional origin is exact. Written target, fragment, label, order,
  origin metadata, and unresolved reason are not queryable.
- Incoming evaluation must read/derive each unique valid `other_path` once and reuse its target set;
  it must not call `normalized_backlinks()` per candidate. Add only minimal additive
  `RelationshipService` helpers to derive normalized relationships from verified content and reuse
  one immutable query-level wikilink resolution snapshot. Existing relationship behavior remains
  unchanged.

**Composition, semantic integration, ordering, and consistency**

- All constraints compose with AND except the path tuple OR and an omitted relationship origin
  accepting either supported origin. Both text modes may coexist. Text-free queries require at
  least one folder, path, tag, metadata, or relationship constraint. Do not add generic NOT/OR
  groups, expressions, SQL/FTS passthrough, regex/glob, nested paths, or user functions.
- With semantic text, every live constraint establishes the finite eligible canonical-path set
  before semantic chunk scoring, aggregation, ranking, and truncation. Extend
  `SemanticSearchService` additively to rank a caller-supplied eligible set. Existing `search()`
  behavior when no such set is supplied remains exact: query embedding, minimum score, lexical
  scoring, hybrid weights, best-chunk selection, relative floor, tie breaks, model, chunking,
  embedding input, signature, and persistence do not change.
- Request at most `min(500, max(limit * 5, limit))` post-eligibility semantic candidates, consume
  once, and return at most `limit` after final live verification; never widen/retry. An eligible live
  note absent from the compatible index cannot appear.
- Add/reuse a semantic-owned query-basis helper reflecting the existing lifecycle. No searchable
  compatible index fails as `semantic_unavailable`; do not fall back. Report ready, usable previous
  index during refresh, or usable previous index after error as the corresponding basis. Nonsemantic
  queries require no index and report `none`.
- Nonsemantic results sort exactly by `(path.casefold(), path)` and omit scores. Semantic results
  preserve existing semantic order and score evidence. No snippets, headings, content, tags,
  metadata, or relationships appear in results.
- Preserve conservative non-atomic semantics: one deterministic live enumeration starts the query;
  live Markdown owns live predicates; compatible semantic facts may be older; stale indexed paths
  are excluded; new unindexed live notes cannot appear in semantic mode; current live filters may
  combine with an older compatible score; races may return fewer than the limit; no vault-wide
  snapshot or unbounded retry is claimed; cached metadata never substitutes for a failed live read.

**Performance, tests, and compatibility**

- Keep the live-derived implementation bounded: enumerate candidate paths once; parse a candidate's
  frontmatter once; reuse candidate content; do not perform a full-vault backlink scan per candidate;
  validate fixed relationship sources once; and reuse one query-level resolution snapshot. Add no
  persistence, cache, metadata/relationship/query index, graph/vector store, or dependency.
- Focused tests cover immutable types and omitted/null distinction; every bound and invalid shape;
  root/folder/path scope, exact spelling and symlink safety; deterministic ordering; literal, tag,
  metadata, outgoing/incoming relationship semantics and bounded work; semantic-only and mixed
  queries, pre-ranking eligibility, unchanged thresholds/floor/ties, candidate window, stale/current
  combinations and all basis states; races, safe per-note omission, retries, and diagnostic privacy.
  Use only fake/local deterministic embedders. Run `tests/eval` and preserve all 13 accepted
  VB-022/VB-024 baseline cases. Real-symlink behavior requires WSL validation.
- Preserve all existing semantic callers/ranking/index behavior; VaultService reads/writes;
  VB-111/VB-112; wikilink/Markdown parsers and all existing relationship methods; REST/OpenAPI paths,
  schemas and operation IDs; MCP, CLI, dashboard, writes/reindex, Docker/TrueNAS/package/publication.
  No migration.

**Explicit non-goals:** no VB-122 adapter adoption; REST/OpenAPI, MCP, CLI, or dashboard query
surface; persistence/cache/query/metadata/relationship index; graph/vector database, Redis, Celery,
Qdrant, worker, or dependency; arbitrary query language; nested metadata; title/alias/time/heading or
unresolved/fragment/label predicates; semantic ranking changes; writes; multiple knowledge spaces;
or Milestone 14+ work.

---

### VB-122 — Expose bounded Knowledge Query through the CLI — P1 ✅

**Status:** Completed on 2026-09-28. One read-only `query` command exposes the accepted bounded
subset through `KnowledgeQueryService` without duplicating domain semantics.

**Accepted surface and behavior**

- Accept optional `--semantic-text`, `--literal-text`, and `--folder`; repeatable exact `--path` and
  required `--tag`; and `--limit`, with the domain default of `20`.
- Construct the existing vault, relationship, and semantic owners and pass one immutable
  `KnowledgeQuery` directly to `KnowledgeQueryService`. Domain validation, filtering, ordering,
  semantic availability, ranking, live verification, and bounds remain owned by VB-121.
- Print only deterministic canonical vault-relative paths, result ordering, compatible semantic
  index basis, and the existing final/semantic/lexical scores when present. Never print query text,
  note content, snippets, metadata, relationships, exception details, host paths, or credentials.
- Preserve the expected `invalid_request`, `unsafe_scope`, and `semantic_unavailable` categories as
  privacy-safe operational exit `1`; unexpected configuration/programming failures remain exit `2`.
- The command is read-only: it performs no implicit synchronization or rebuild and changes neither
  Markdown nor semantic persistence.

**Explicit non-goals:** no REST/OpenAPI, MCP, dashboard, persistence, dependency, write, semantic
ranking, index compatibility, metadata parsing, relationship, Docker/TrueNAS, or release change.

---

## Knowledge Capture / Portable Memory

### VB-130 — Define Knowledge Capture and provenance model / ADR — P1 ✅

**Status:** Completed as design-only work on 2026-09-28. ADR 0007 is accepted as the
authoritative domain contract. VB-131 subsequently implemented capture runtime and one CLI adapter;
VB-130 itself added neither.

**Goal:** define bounded, inspectable capture into portable Markdown, with provenance and an
explicit human/operator decision before captured material becomes promoted knowledge. Keep the
Markdown bytes and safe portable metadata authoritative under ADR 0005.

**Acceptance criteria**

- accept `docs/adr/0007-knowledge-capture-and-provenance.md` as the conceptual capture,
  provenance, intake-state, review, and promotion contract for separately scoped VB-131/VB-132;
- distinguish an inbox/draft Markdown capture artifact from a reviewed destination note: both are
  authoritative files, but intake is not implicitly promoted or included as curated knowledge;
- define source/provenance, capture event time, optional portable tags and capture type, including
  absent/unknown versus asserted facts, safe scalar types, bounds, and preservation in Markdown;
- require one explicit human/operator choice of create or append and its destination for each
  promotion; related/duplicate evidence is advisory and cannot choose a target, merge, rewrite,
  rename, delete, or silently discard a capture;
- reuse `VaultService` containment, size, UTF-8, create/append compatibility, and post-commit
  indexing boundaries conceptually; do not claim its current create/append checks are atomic under
  cooperating writers or retries; require separately contracted VB-131/VB-132 write protection that
  preserves non-overwriting create, idempotent append, contained Markdown-only writes, and existing
  caller behavior; state where existing methods do not yet implement arbitrary capture provenance;
- define deterministic validation/conflict/failure outcomes, privacy-safe diagnostics, bounded
  duplicate/related interaction, and ownership of metadata, relationships, query, and writes;
- keep VB-131 limited to its separately contracted portable inbox/draft capture capability and
  VB-132 limited to a separately contracted explicit review/promotion capability. This design
  task itself implemented neither capability or public adapter.

**Compatibility / explicit non-goals:** documentation and architecture only. No runtime capture,
REST/OpenAPI, MCP, CLI, dashboard, persistence/database/cache/index, dependency, automatic
promotion/merge/rewrite/rename/delete, silent chat-history ingestion, mandatory LLM/cloud/embedding
provider/external service, or change to current `create_note`/`append_note` behavior. No release or
deployment change.

---

### VB-131 — Portable inbox/draft capture — P1 ✅

**Status:** Implemented and merged as `c61d7c6` (PR #107). The capture domain service, coordinated
atomic create-if-absent boundary, local CLI `capture` adapter, and focused tests are present.
ADR 0007 governs capture authority, provenance, and scope; ADR 0005 governs portable metadata.
This item specifies VB-131 intake only; it grants no review or promotion behavior.

**Goal:** one explicit request creates exactly one new, contained, inspectable UTF-8 Markdown
artifact in `inbox` or `draft` state. Intake does not append to an existing note, promote content,
select a related target, merge duplicates, or change existing notes.

**Domain input and portable representation**

- The capture boundary accepts authored Markdown `content`, `state` (`inbox` or `draft`), a
  caller-supplied `capture_id`, and a caller-supplied `captured_at`. It accepts optional `title`,
  `source`, ordered `tags`, `capture_type`, and a bounded mapping of additional safe portable
  metadata. The caller must retain the same ID and timestamp for retries. No default timestamp or
  generated ID is substituted during a retry. A first-time adapter may help the caller construct
  these values before invoking the domain boundary, but must make them available for retry.
- `capture_id` is a canonical lowercase UUID version 4 string. It is an idempotency context and
  filename component, recorded visibly as `capture_id` in frontmatter. The verified canonical
  vault-relative Markdown path remains artifact identity; neither this ID nor a database row,
  source, timestamp, or title is a second identity authority. ID reuse with different request
  facts is a conflict, not an update.
- Emit one ADR 0005-valid YAML 1.2 Core frontmatter mapping followed by a blank line and the
  exact authored Markdown body, without inferred heading, content rewrite, or extra source stamp.
  Fixed fields appear in this order: `capture_id` (string), `capture_state` (string `inbox` or
  `draft`), `captured_at` (UTC RFC 3339 string at second precision, emitted with `Z`), then
  optional `title` (string), `source` (string), `tags` (sequence of strings), and `capture_type`
  (string). A supplied `+00:00` timestamp is normalized to `Z`; missing offset, fractional
  seconds, non-UTC offset, malformed timestamp, or invalid calendar value is rejected. Omitted
  optional fields stay absent. An explicitly supplied `source: unknown` remains an unverified
  declared value, distinct from an omitted source. A supplied empty tags sequence remains `[]`.
  Tags preserve order and duplicates without case folding, trimming, or `#` insertion/removal.
- Additional metadata is serialized after fixed fields, preserving its mapping/sequence order and
  parsed portable values. Reject keys that collide with any fixed field or each other;
  reject unsupported types and ADR 0005 profile violations. Additional metadata cannot override
  state, provenance, or ID. Use a safe serializer whose output round-trips through the existing
  frontmatter parser with the same typed values; quoted strings must stay strings. Unknown safe
  portable metadata is retained, not discarded or promoted into hidden storage. An additional
  safe `created` value retains its own meaning and is never reinterpreted as `captured_at`.

**Destination and exact conflict policy**

- The sole VB-131 destination is `Inbox/Captures/<capture_id>.md`, with that exact spelling and
  lowercase UUID filename. The request cannot supply a folder or target path. The optional title
  is display metadata only; it does not choose or sanitize a filename. A missing `Inbox/Captures`
  directory may be created only through a verified contained path. Existing directories must have
  exact spelling and be safe; case variants, files in place of a directory, traversal, absolute
  paths, symlink escapes, or a non-Markdown target fail without falling back elsewhere. The
  `VaultService` boundary owns verification and canonical path results, including fail-closed
  handling of traversal and symlink attacks through VaultBridge inputs. Active out-of-band
  namespace mutation during a commit has the separate concurrency boundary below.
- Create only if the exact path is absent. If it exists, read it through the verified bounded
  Markdown boundary and compare its complete bytes with the canonical bytes for this same
  request. Exact equality with the matching visible `capture_id` returns `already_applied` and
  the canonical path; any difference returns `conflict`. Unreadable, oversized, malformed, or
  unsafe existing targets fail safely and are never overwritten, repaired, renamed, or treated as
  successful retries. No suffix counter, alternate folder, or content-based deduplication is used.

**Validation, write, and retry contract**

- Validate types and all bounds before directory creation, enumeration, indexing, or write.
  Required content is non-empty, non-whitespace authored Markdown of at most 65,536 UTF-8 bytes;
  `title` is at most
  256 bytes, `source` 2,048, `capture_type` 64. At most 16 supplied tags, each at most 1,024
  bytes, are allowed before deduplication (which VB-131 does not do). Reject empty required
  content, invalid UTF-8 or non-string fields, invalid state/ID/time, unsupported metadata types,
  and exceeded bounds without coercion, truncation, or partial acceptance. Frontmatter including
  delimiters must fit ADR 0005's 65,536-byte profile and its 8,192-byte scalar source, 256-byte
  key, depth-8, and 1,024-item limits. The complete serialized Markdown must fit the configured
  `VaultService.max_note_bytes`; serialization growth counts toward both limits.
- The capture domain capability composes and validates the request and delegates contained,
  Markdown-only atomic creation to `VaultService`. VB-131 must introduce an additive internal
  create-if-absent primitive or an otherwise compatible VaultService-owned boundary that commits
  complete bytes atomically at the exact path across concurrent VaultBridge-mediated writers
  participating in the same coordination mechanism, including supported processes and threads.
  A cooperating VaultBridge writer must never overwrite an existing capture destination or expose
  a partial artifact as a successful commit. Verify containment and canonical destination safety
  at the filesystem commit boundary and fail closed on detected instability. The current
  `create_note` check-then-`write_text` is not atomic and cannot fulfill this requirement. Preserve
  its existing public signature, `created`/`unchanged`/conflict semantics,
  frontmatter defaults, and existing REST/MCP callers; do not route them through a changed
  capture policy. The concurrency mechanism must also coordinate any existing `create_note` or
  `append_note` caller targeting the same path, so it cannot overwrite or interleave with a
  capture commit. Internal synchronization may change; existing append semantics must not.
- The strong atomicity and idempotency guarantee applies to those cooperating VaultBridge writers
  while the `Inbox/Captures` destination namespace is stable. Ordinary external editing of Markdown
  remains supported. A non-cooperating filesystem writer can actively rename, replace, or relocate
  the `Inbox/Captures` directory chain or destination/staging during a capture; that concurrent
  namespace mutation is outside the strong guarantee. Directory descriptors can remain attached
  to renamed directories, advisory locks constrain only participants, and checks before or after
  commit cannot make namespace verification and commit one atomic operation. Neither locks nor
  descriptors establish exclusive namespace control. No such control is a VB-131 deployment
  prerequisite. Detected unsafe paths must fail closed; if the implementation cannot prove the
  canonical destination contains the expected complete bytes, it must not report `created`.
- A request with a confirmed `created` or `already_applied` result must never create another
  artifact on retry. On lost response or uncertain commit, retry only with the original ID and
  identical request facts; inspect the one exact path and return `already_applied` only after
  verified byte equality. Under a stable namespace and without evidence of a possible relocated
  commit, an absent path permits a new atomic create attempt. A different ID is a new capture
  request, not a retry. A conflicting or uninspectable path requires operator resolution; do not
  infer success or choose a new target. Once possible out-of-band namespace mutation makes a
  commit uncertain, `commit_unknown` remains unresolved even if the canonical path is currently
  absent. Neither an automatic retry nor a fresh create for that known same-ID request is allowed
  until an operator resolves the possible relocated artifact and explicitly authorizes another
  write attempt. If the operator cannot establish that another artifact will not be produced,
  no further write is authorized. This unresolved outcome must be retained by the caller across
  retries; VB-131 does not add hidden persistent idempotency state or require a global filesystem
  scan. Concurrent cooperating same-ID/same-request attempts yield one `created` and verified
  `already_applied` results; differing requests sharing an ID yield one `created` and conflicts.
  No hidden idempotency table, cache, or semantic index becomes authoritative.
- Return stable categories: `created` and `already_applied` with canonical path and committed
  Markdown status; `invalid_request`, `unsafe_destination`, `conflict`, `size_limit`, and
  `write_unavailable` without claiming a commit. Detected unsafe destination or namespace state
  before any possible commit is `unsafe_destination`; a write failure or detected namespace
  instability after a possible commit is `commit_unknown` unless canonical placement and complete
  bytes can be proved. `commit_unknown` is not a safe invitation to choose another ID or retry a
  write automatically. After a confirmed Markdown commit, post-commit semantic enqueue/index
  failure is `created` (or `already_applied` on retry) with `index_pending` after accepted enqueue or
  `index_unavailable` after failed enqueue as separate derived-state evidence. A later index
  failure leaves the Markdown result unchanged and the index stale until recovery. It never
  rolls back Markdown, reports the capture as uncommitted, or
  repeats the write. Index work may be retried for the canonical path independently. Programming
  errors are not silently recast as expected validation failures.
- Routine logs and errors contain only stable categories, operation identifiers, and safe counts
  or booleans. They contain no captured content, title/source/tag/other metadata values, URLs,
  vault-relative or host paths, credentials, symlink destinations, or raw exception strings.
  A successful authorized result may return the canonical vault-relative path for inspection;
  that path is not emitted in routine diagnostics. Existing authentication and read controls
  continue to govern inspection.

**Initial adapter and ownership:** VB-131 adds exactly one local CLI `capture` command as a thin
adapter over the one capture domain capability. It reads one explicitly supplied UTF-8 JSON
request object from standard input, bounded to 1,048,576 input bytes before parsing; JSON keys
are the domain field names above, with `metadata` for additional fields. Reject duplicate or
unknown top-level keys. Require the caller to supply `capture_id` and `captured_at`, including on
retry; echo the ID and canonical path on committed outcomes so they can be retained. Return the
stable category and index state without echoing content or metadata. No REST, MCP, dashboard,
background chat ingestion, remote URL fetch, duplicate search, or additional adapter is in scope.
The domain boundary owns capture semantics and portable composition; the ADR 0005 frontmatter
layer owns typed profile validation; `VaultService` owns filesystem safety and atomic creation;
the existing indexer owns derived post-commit work. No mandatory LLM, cloud, or external embedding
service is introduced.

**Focused implementation acceptance tests:** cover every bound and invalid type before mutation;
frontmatter parse/serialize round-trips (including quoted timestamps, `unknown` versus absent
source, ordered duplicate tags, empty tags, and ordered unknown safe metadata); title-independent
fixed filename; contained exact path and symlink/case/traversal failures; exact-byte retry versus
same-ID different-payload conflict and no overwrite; lost-response retry; simultaneous same-ID
same/different requests among cooperating threads and processes; complete-byte commit under
injected write failure; detected namespace instability before a possible commit versus after an
uncertain commit,
including absent canonical path with no automatic second write pending operator resolution;
post-commit enqueue/index failure with confirmed Markdown success and no second file;
privacy-safe diagnostics under invalid input and I/O failures; and unchanged behavior of existing
`create_note` callers. Tests must prove the atomic guarantee rather than only sequential checks.
They must not claim protection against arbitrary non-cooperating namespace mutation.

**Explicit non-goals:** VB-131 adds no VB-132 promotion or
review, append promotion, automatic target selection/merge/rewrite/rename/delete, hidden capture
database/index/cache, silent chat-history ingestion, remote fetching, new dependency or service,
broad VaultService API change, unrelated adapter, release, or deployment change.
No hidden idempotency database, global filesystem scan, privileged mount namespace, immutable
directory flag, exclusive host filesystem ownership, or deployment-specific ACL is required.

---

### VB-132 — Explicit review and promotion workflow — P1 (contract accepted; not implemented)

**Status:** Implementation contract accepted as design-only work. No VB-132 runtime or public
adapter exists. ADR 0007 owns the capture/provenance model, ADR 0005 the portable Markdown model,
and the implemented VB-131 contract above owns intake and its cooperative-writer boundary.

**Goal:** let a local human/operator inspect one VB-131 capture and explicitly approve exactly one
create or append of selected Markdown into one chosen authoritative destination. The capture stays
intact. A review, suggestion, timeout, prior decision, or semantic match cannot cause a write.

**Review snapshot and advisory evidence**

- Review accepts exactly one supplied canonical `Inbox/Captures/<capture_id>.md` path and matching
  lowercase UUID v4 `capture_id`. `VaultService` must verify exact spelling, containment, regular
  Markdown file, size, and readable UTF-8 bytes. Parse the current bounded ADR 0005 frontmatter
  from that same read; require one valid VB-131 `capture_id`, `capture_state` (`inbox` or `draft`),
  and UTC second-precision `captured_at` matching the fixed path/ID. Preserve absent versus
  explicitly declared optional fields and safe additional metadata. An unsafe, missing, oversized,
  unreadable, malformed, or mismatched artifact is not a reviewable capture (`unsafe_source` or
  `invalid_request` as applicable). Never repair or normalize it in place.
- Return the canonical source path, ID, state, capture metadata, exact authored body, and SHA-256
  of the **entire current capture byte stream** as the review snapshot. The digest is a bounded
  version check, not a second identity authority or approval. No hidden persistent review state,
  mtime-only check, or index snapshot is required.
- At operator request, obtain finite, explicitly limited duplicate/related/relationship or
  Knowledge Query evidence through their existing owners. Preserve their own ordering and
  unavailability signals; exclude unsafe/unverified candidate paths. Evidence is advisory even
  when exact title, link, or semantic results agree. It neither fills a destination nor changes
  the approved content, action, or disposition. Review remains possible without an index or any
  candidates; no broad scan is required merely to review a valid capture.

**One explicit `PromotionDecision`**

- The operator supplies `source_path`, `capture_id`, `expected_source_sha256`, exact
  `approved_content` (non-empty UTF-8 Markdown, at most 65,536 bytes), `action` (`create` or
  `append`), explicit canonical vault-relative `.md` `destination`, a caller-retained lowercase
  UUID v4 `promotion_id`, and caller-retained UTC second-precision `approved_at`. `approved_at`
  records this decision, not the capture event. The operator reviews the exact final
  `approved_content` alongside the capture; it may be a verbatim selection or an explicitly
  edited selection, never a silent transformation. The decision supplies `transfer_fields` as
  an ordered subset of only `title`, `tags`, and `capture_type`, in that fixed order; each
  selected field must be present in the reviewed capture. No other field is selectable in
  VB-132: reject `created`, arbitrary additional safe metadata, `capture_state`, and unknown
  names rather than accepting and dropping them. The source path/ID, source digest,
  `captured_at`, and any present declared source are mandatory provenance outside this subset
  and cannot be changed or invented. Do not infer a verified source from a declared `source`,
  URL, title, link, or match.
- `promotion_id` is a lowercase UUID v4 idempotency key scoped to the **one exact canonical
  destination path**, not a vault-wide unique promotion identity. The idempotency context is
  `(destination, promotion_id)`; `decision_sha256` binds the exact approved facts within that
  context. Compute it as lowercase SHA-256 of the UTF-8 bytes of one canonical JSON object:
  `action`, `approved_at`, `approved_content`, `capture_id`, `captured_at`, `destination`,
  `expected_source_sha256`, `promotion_id`, `source_path`, `transfer_fields` (the fixed-order
  selected-name list), and `transfer` (selected values under `capture_title`, `capture_tags`,
  or `capture_type`, exactly as emitted below), plus `capture_declared_source` only when present
  and exactly one of
  `expected_destination: "absent"` or `expected_destination_sha256`. Do not include the digest
  itself or serialized destination Markdown. Serialize this object with sorted keys, compact
  `,`/`:` separators, ASCII-escaped non-ASCII JSON strings, no nonfinite numbers, and no
  trailing LF. Use the same normalized `Z` timestamp, canonical paths, and exact approved
  content on every retry. Thus the digest does not depend on frontmatter or fence choices. A changed
  fact requires fresh operator approval for that exact decision. The same UUID at another
  destination denotes a separate context and cannot be detected as a conflict from the chosen
  path alone; it never carries approval to that destination. No global uniqueness or
  cross-destination conflict guarantee is claimed.
- Require lowercase 64-character SHA-256 hex digests. Accept `approved_at` only as a valid UTC
  RFC 3339 timestamp at second precision with `Z` or `+00:00`, normalize it to `Z` before
  composing bytes, and retain that normalized fact on retry. Reject changed review facts rather
  than silently recalculating hashes or timestamps. `approved_content` is UTF-8 with LF-only
  line breaks: reject CR, NEL, LINE SEPARATOR, and PARAGRAPH SEPARATOR before either write;
  never normalize, escape, or rewrite the approved bytes.
- `create` requires `expected_destination: absent`; `append` requires
  `expected_destination_sha256` from a verified read of the one selected existing destination.
  The operator must see that destination's current Markdown before approving append. Neither
  action, destination, content, provenance selection, expected hashes, ID, nor timestamp is
  defaulted at the write boundary. A retry repeats the exact decision, including ID and time;
  changing any bound fact requires a new review and decision. Validate the full request and
  composed size before filesystem mutation or advisory lookup. Unknown/duplicate fields, invalid
  types, path aliases, unsafe values, and omitted destination are `invalid_request` or the
  specific unsafe category. No best-match selection or create/append fallback exists.
- The destination cannot equal the source or be inside `Inbox/Captures`; append also rejects a
  destination whose current valid metadata marks it `inbox` or `draft`. Create requires an
  already existing, exactly spelled, verified contained parent directory and an absent `.md`
  basename; it does not create folders. A missing parent is `destination_missing`, a case alias,
  traversal, symlink escape, or non-Markdown target is `unsafe_destination`. Append requires one
  existing regular contained Markdown file at the exact chosen spelling.
- At the write boundary, re-read and verify the exact source path, matching capture metadata,
  and whole-byte digest under the same cooperating-writer coordination used for the destination.
  A difference from the reviewed source is `source_changed`; no write occurs. For append, re-read
  the exact canonical destination and compare its whole-byte digest to the approved preimage
  before a first write. Byte equality at the same canonical path is the review precondition; file
  timestamps are insufficient. A changed, missing, or unsafe destination requires a fresh human
  decision, never reuse of approval. Reverify path safety at commit and after a possible commit.

**Portable provenance and deterministic bytes**

- Compose the complete proposed destination bytes deterministically before writing. For create,
  insert frontmatter fields in this **exact order**: `promotion_id`,
  `promotion_decision_sha256`, `promotion_approved_at`, `promoted_from_capture_id`,
  `promoted_from_capture_path`, `promoted_from_sha256`, `captured_at`, then
  `capture_declared_source` if present, `capture_title` if selected, `capture_tags` if selected,
  and `capture_type` if selected. The last three carry the reviewed string, ordered string
  sequence (including duplicates or empty sequence), and string, respectively; an unselected
  field is absent. `capture_declared_source` is unverified: absent stays absent and declared
  `unknown` stays a string. No `verified_source` or destination `created` field is emitted;
  the capture's `created` is never copied or equated with `captured_at` or `approved_at`.
  `capture_id` and capture path identify only the source; the destination's canonical path is
  its identity.
- Pass that insertion-ordered mapping to the existing deterministic
  `app.services.frontmatter.serialize_frontmatter` contract: one YAML 1.2 Core JSON-flow
  mapping with fields in insertion order, `, ` between entries and `: ` between each key and
  value, double-quoted keys and string scalars using JSON escaping with literal valid Unicode,
  flow sequences for tags, and escaped U+0085/U+2028/U+2029 in YAML scalars. Its exact envelope
  is `---\n` + one mapping line + `\n---\n`. Append one LF and then the exact
  `approved_content` bytes, with no added terminal LF: complete create bytes are UTF-8 without
  BOM, with LF-only line endings and exactly one blank line between the closing delimiter and
  body. Reject serializer/profile/whole-note bound failures before write. The same normalized
  decision must emit identical bytes; no inferred heading, fetched content, or content rewrite.
- For append, leave existing frontmatter and prior body bytes untouched. Emit one block using
  the same source provenance and selected typed values as create. The canonical single-line
  manifest has exactly `source_path`, `capture_id`, `source_sha256`, `captured_at`,
  `approved_at`, and `transfer`, plus `capture_declared_source` only when present; `transfer`
  maps selected fields to the same `capture_title`, `capture_tags`, or `capture_type` values as
  create. Serialize the manifest with JSON `sort_keys=True`, `separators=(",", ":")`,
  `ensure_ascii=True`, and `allow_nan=False`, then escape every literal `<` as JSON `\u003c`
  before UTF-8 encoding; append no LF to the manifest itself. This fixes key order and quoting
  independently of mapping insertion order and prevents provenance values from creating marker
  prefixes in the manifest. Use the same JSON options without the `<` replacement for the
  normalized-decision digest object above.
  Let `fence_length = max(3, longest consecutive backtick run in the canonical manifest + 1)`;
  a manifest with no backtick run has longest run zero. The opening fence is exactly
  `fence_length` ASCII backticks followed immediately by `json`; the closing fence is exactly
  `fence_length` ASCII backticks with no suffix.
- The append bytes are exactly: two LF bytes; ASCII header
  `<!-- vaultbridge-promotion:v1 id=<promotion_id> sha256=<decision_sha256> -->`; LF;
  opening fence; LF; canonical manifest bytes; LF; closing fence; two LF bytes; unchanged
  UTF-8 `approved_content`; LF; ASCII closing marker
  `<!-- /vaultbridge-promotion:v1 id=<promotion_id> -->`; LF. The framing uses LF only,
  including when the approved content already ends in LF. No frontmatter edit, merge, or
  rewrite of prior content is permitted.
- Use one **reserved-marker recognition rule** for both pre-write validation and retry scanning.
  For the active `promotion_id`, the scanner recognizes either UTF-8 byte prefix
  `<!-- vaultbridge-promotion:v1 id=<promotion_id>` or
  `<!-- /vaultbridge-promotion:v1 id=<promotion_id>` anywhere, without relying on the digest,
  line position, or remaining suffix. Reject `approved_content` as `invalid_request` before
  either write if it contains either prefix. Do not escape or rewrite it. On append retry,
  scan the selected destination using those identical prefixes: exactly one opening and one
  closing occurrence at the expected block positions, with the complete canonical block bytes
  and matching digest, prove `already_applied`; any extra, partial, or different-digest
  occurrence for this destination-scoped ID is `conflict`. An identical retry cannot reinterpret
  previously accepted approved content as promotion evidence.

**Create, append, concurrency, and retries**

- Create uses an additive `VaultService` exact-path atomic create-if-absent operation for the
  composed complete Markdown bytes. It must coordinate with existing `create_note`, `append_note`,
  VB-131 capture, and other promotion writers across supported threads and processes. It never
  overwrites. If the exact path already contains the exact complete bytes and matching portable
  promotion attribution, including the same `promotion_decision_sha256`, for this decision,
  return `already_applied`; otherwise `conflict` at this destination.
  Existing `create_note` `unchanged` means only a matching tail and is **never** promotion proof:
  surface it as unresolved conflict and require a new decision. A retry uses the same ID,
  destination, and bytes; it never generates another filename or silently appends.
- Append uses an additive `VaultService` exact-path, expected-preimage, append-once operation
  under the same cross-process/thread coordination as every VaultService writer. Lock, verify
  source and destination, inspect for this promotion ID, compare the approved preimage, append
  the full block once, flush, and verify its exact complete occurrence at the canonical path
  before reporting `appended`. The legacy `append_note` signature, optional `dedupe_key`, marker,
  normalization, statuses, and existing caller behavior remain unchanged. Concurrent identical
  retries yield one `appended` and proven `already_applied`; the same ID at this destination
  with a different decision digest or incomplete block is `conflict`. A retry may report
  `already_applied` after later unrelated destination edits only if the exact attributable block
  remains complete and unique; otherwise
  `destination_changed` or `conflict`, with no new append. If no block is present, a changed
  preimage is `destination_changed`, a missing note `destination_missing`, and an unsafe path
  `unsafe_destination`; none selects another note.
- The strong guarantee covers cooperating VaultService-mediated writers with stable source and
  destination namespaces. Ordinary external Markdown editing is supported but active
  non-cooperating rename, replacement,
  or relocation during commit is outside that guarantee. Detected ambiguity is never reported as
  a successful promotion. Do not add exclusive filesystem ownership, ACLs, immutable directories,
  mount namespaces, a hidden database, or a deployment prerequisite.
- A failure before any possible write is `write_unavailable` (or a specific validation, missing,
  unsafe, or conflict category). After a possible write, inspect the one canonical path: return
  `already_applied` only when exact complete attribution is proved; otherwise `commit_unknown`.
  A known `commit_unknown` caused by possible namespace relocation remains unresolved even when
  the canonical path is absent. The caller retains that fact; there is no automatic second write,
  new destination, or replacement ID until an operator resolves possible committed material and
  explicitly authorizes another attempt. If absence of another copy cannot be established, no
  further write is authorized. Partial append evidence is `conflict`/`commit_unknown`, never a
  successful retry. No global filesystem scan or hidden retry table is required.

**Disposition, results, indexing, and privacy**

- A confirmed destination write leaves the source capture byte-for-byte intact in `inbox` or
  `draft`; no automatic promoted flag, two-file transaction, cleanup, move, rename, or delete.
  The result reports destination promotion separately. A later disposition action needs its own
  explicit contract.
- Stable categories include `invalid_request`, `unsafe_source`, `source_changed`,
  `unsafe_destination`, `destination_missing`, `destination_changed`, `conflict`,
  `already_applied` (proved only), `commit_unknown`, `write_unavailable`, `created`, and
  `appended`; `size_limit` may refine validation. Return `committed` separately. A confirmed
  Markdown commit remains `created`/`appended` or proven `already_applied` when derived indexing
  is `index_pending` (enqueue accepted) or `index_unavailable` (enqueue failed). Later index
  failure leaves the Markdown result committed and triggers only independent indexing recovery;
  never repeat the write for indexing. Programming defects are not recast as expected failures.
- Routine logs, errors, and failure output contain stable categories, operation IDs, and safe
  counts/booleans only: no capture/destination content, source/title/tag/metadata values, URLs,
  vault-relative or host paths, credentials, symlink destinations, or raw exceptions. Authorized
  successful output may include the minimum canonical source/destination paths and IDs needed to
  inspect the commit, never content or arbitrary metadata.

**Initial adapter and ownership:** Add exactly one local CLI `promote` adapter with `review` and
`apply` modes over one domain review/promotion capability. `review` takes one explicit capture
path/ID and, when requested, a finite candidate limit; it displays the bounded capture snapshot,
hash, and optional advisory evidence to the local operator, with an explicit option to inspect
the chosen append destination and its hash. `apply` reads one UTF-8 JSON decision from standard
input, capped at 1,048,576 bytes before parsing, rejects duplicate/unknown keys, and returns
category, committed flag, index state, and only committed canonical identifiers/paths. The
operator retains the review hash, approved content, destination preimage hash, promotion ID,
approved time, and any `commit_unknown` result in the decision file for exact retry; the CLI
does not persist approval. The domain owns decision/provenance semantics; ADR 0005 parsing owns
metadata validity; `VaultService` owns safe reads/writes and coordination; existing candidate,
relationship, query, and index owners retain their boundaries. No REST, MCP, or dashboard adapter.

**Focused future runtime tests:** require source path/ID/frontmatter/UTF-8/size rejection;
changed capture between review and write; explicit action/destination/content and advisory-only
evidence; no automatic target selection; deterministic portable create frontmatter and exact
append block provenance; byte-identical create/append serialization on retries, fixed field
order, JSON escaping and fence length, reserved-marker rejection even with another digest;
exact same-decision retry, same-destination conflicting ID reuse,
independent same-ID contexts at distinct destinations requiring distinct approvals, rejection
of unsupported selected metadata, and exact representation of each supported selection;
concurrent create and append retries among threads and processes;
destination changed/missing/unsafe and partial
block conflicts; conservative `create_note` `unchanged`; lost response and `commit_unknown`,
including absent canonical path after possible relocation; post-commit index failure without
second write; intact source capture; privacy-safe diagnostics; unchanged legacy
`create_note`/`append_note` callers; and Windows/POSIX behavior under the accepted
cooperative-writer threat model. Tests must exercise failure paths, not merely sequential success.

**Explicit non-goals:** no VB-132 runtime in this design task; automatic promotion, duplicate
merge, target selection, capture disposition/cleanup, broad Markdown editing, REST/OpenAPI, MCP,
dashboard, hidden workflow database, job queue, mandatory LLM/cloud/embedding/external service,
semantic-ranking change, VB-140, Milestone 16, release, or deployment change.

---

## Release history

### v1.2.0 release

**Status:** Published and independently verified on 2026-09-20.

Stable GitHub Release `v1.2.0` and its GHCR image were published from release source commit
`375bf484fbe6a302424951d33c701f6fd9773e3f`. Exact-source CI, the recovered publication workflow,
all stable aliases and OCI metadata, anonymous exact-digest pull, both MCP transports, and the full
immutable-image functional gate pass. The durable record, including the initial pre-publication
workflow failure and the distinct workflow hotfix commit, is in
[`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md#v120-release-evidence).

At the `v1.2.0` release point, the accepted TrueNAS Community App was still on application image
`1.1.0`; that release did not update the catalog or close VB-082. Its edit-persistence,
ixVolume-uninstall, valid-prior-state upgrade, and rollback gates remain open. Upgrading the
VaultBridge application from `v1.1.0` to `v1.2.0` performs one safe automatic derived-index rebuild;
Markdown and the SQLite schema remain unchanged.

### v1.2.1 release

**Status:** Published as a stable GitHub Release on 2026-09-20.

Package, FastAPI, and MCP server metadata target `1.2.1` at release source commit
`59666d75b6ec1d4ca01430d61669f212d46180b5`. This backward-compatible patch contains only the
already-merged dashboard favicon and two sanitized real TrueNAS canary screenshots. It adds no API,
MCP tool, data format, migration, semantic-index contract, configuration, or deployment architecture
change.

Release-triggered workflow run `35507290152` passed release-source verification, GHCR image
publication, and stable-alias publication. The release did not update the accepted upstream TrueNAS
catalog, which at that release point was package `1.0.0` with application image `1.1.0`.

### v1.3.0 release

**Status:** Published and independently verified on 2026-09-25.

Package, FastAPI, and MCP server metadata target `1.3.0`. This published backward-compatible feature
release collects the completed VB-100 through VB-106 work: Obsidian wikilink parsing/resolution,
outgoing links and backlinks, REST and MCP relationship tools, the dashboard Relationships UI,
graph-aware retrieval evaluation, default-off MCP `create_note`/`append_note`, and first-class
TrueNAS MCP configuration source. The graph-aware evaluation did not change production ranking.

Stable GitHub Release `v1.3.0` and its GHCR image were published from release source commit
`a7e14ece0de74632d1d9be599d53678931dc64b3`. Workflow `36019163750`, all stable aliases, OCI
metadata, anonymous exact-digest pull, MCP read/write smoke, and the full immutable-image functional
gate pass. Durable evidence is in
[`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md#v130-release-evidence).

Current upstream TrueNAS package `1.0.2` selects application image `1.3.0`, but its form still omits
the first-class MCP fields. Delivering those fields and completing live TrueNAS lifecycle validation
remain separate from the application release.

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
→ VB-070 ✓
→ VB-071 ✓
→ VB-072 ✓
→ VB-073 ✓
→ VB-074 ✓
→ VB-075 ✓
→ VB-076 ✓
→ VB-080 ✓
→ VB-081 ✓
→ VB-082 PRE-UPSTREAM GATES ✓
→ VB-083 ✓
→ VB-082 POST-MERGE LIFECYCLE VALIDATION IN PROGRESS / PARTIAL
→ VB-090 ✓ (independent MCP design track)
→ VB-091 ✓ (not NEXT)
→ VB-092 IMPLEMENTED
→ VB-093 ✓
→ VB-100 ✓
→ VB-101 ✓
→ VB-102 ✓
→ VB-103 ✓
→ VB-104 ✓
→ VB-105 ✓ (production ranking not supported)
→ VB-106 ✓
→ v1.2.1 ✓
→ v1.3.0 ✓
→ VB-110 ✓ (accepted portable PKM document model / ADR)
→ VB-111 ✓ (bounded YAML frontmatter parsing)
→ VB-112 ✓ (portable aliases and tags projection)
→ VB-113 ✓ (contained standard Markdown note relationships)
→ VB-114 ✓ (normalized multi-dialect relationship domain view)
→ VB-120 ✓ (accepted bounded Knowledge Query capability / ADR)
→ VB-121 ✓ (bounded Knowledge Query domain runtime and evaluation)
→ VB-122 ✓ (read-only bounded Knowledge Query CLI adapter)
→ VB-130 ✓ (accepted Knowledge Capture and provenance model / ADR)
→ VB-034 (optional opt-in write task)
```

VB-057 through VB-060 close the confirmed containment, native-Windows test-portability,
release-version alignment, and repository-exposure-safety blockers. Stable `v1.0.0` and its final
distribution gates are complete; immutable evidence remains recorded in `docs/RELEASE_CHECKLIST.md`.

VB-070 through VB-076 complete the bundled Web Dashboard architecture, shell, public health-backed
Overview, protected literal/semantic Search, usability/accessibility/image hardening, publication,
and the persistent HttpOnly session replacement. VB-080 completes the version-neutral Community
App packaging design, and VB-081 is
complete with the released `1.1.0` image, current upstream metadata, officially generated artifacts,
and Docker-backed render/deploy validation. VB-083 is complete after PR #5805 review/merge, accepted
source and generated-entry verification, and operator-confirmed Discover Apps availability. The
accepted package uses the reviewer-provided CDN icon and default Web UI port `30491`. VB-075 is
complete with exact-source CI and full exact-image functional runtime evidence. VB-023 retrieval benchmarking is
complete. VB-032 and VB-033 remain deferred optional
work. VB-055 remains optional
and is not a dashboard prerequisite. Milestone 9 is **UPSTREAM ACCEPTED / POST-MERGE VALIDATION IN
PROGRESS**. VB-082 remains partial: the initial catalog install/form/masking/Portal, Host Path,
ixVolume configuration, healthy-vault, and rotation-migration checks are operator-confirmed, while
edit-form persistence, ixVolume uninstall semantics, a valid prior-state upgrade, and rollback remain
open. VB-090 and VB-091 complete the read-only stdio MCP design and implementation. VB-092 adds the
opt-in read-only Streamable HTTP path, and VB-093 completes its container CI validation. This does
not establish production TrueNAS runtime behavior. A separate isolated TrueNAS source-build smoke
also passed against synthetic data without using the production vault. The favicon-and-screenshot-only
`v1.2.1` patch remains historical release evidence. `v1.3.0` is the published stable GitHub/GHCR
release from source commit `a7e14ece0de74632d1d9be599d53678931dc64b3`, with immutable image
evidence recorded in `docs/RELEASE_CHECKLIST.md`. Current upstream TrueNAS package `1.0.2` selects
image `1.3.0` but still lacks the first-class MCP form fields, and the remaining VB-082 gates stay
open. Milestone 11 is complete as a read-first relationship track; VB-105 records evaluation
evidence but does not support a production graph-ranking change, while VB-106 adds default-off MCP
write parity and first-class TrueNAS MCP configuration source. VB-032/VB-033 remain deferred and
VB-034 remains a later, opt-in write capability. VB-110 accepts ADR 0005 as the portable PKM
document-model contract. VB-111 implements its bounded read-only YAML frontmatter parser, and
VB-112 implements immutable field-local alias/tag projection without changing public behavior, and
VB-113 implements bounded contained standard Markdown relationships at the domain layer without
changing wikilink-backed public adapters. VB-114 completes the Portable PKM milestone with an
immutable live normalized relationship view and no public adapter or persistence change. VB-120
accepts ADR 0006 as the bounded Knowledge Query domain contract. VB-121 implements that contract as
an immutable domain-only runtime with live eligibility before unchanged semantic ranking, bounded
safe failures, and no persistence. VB-122 exposes only its accepted read-only CLI subset without
adding another query implementation or any REST, MCP, dashboard, persistence, or write surface.

Do not infer scope from sequence alone. Always read the exact task definition before implementation.
