# VaultBridge Roadmap v2

> **Project goal:** build VaultBridge into a reliable, client-agnostic, self-hosted knowledge layer
> for Markdown-based personal knowledge systems. Portable Markdown remains authoritative.
> VaultBridge provides safe retrieval, metadata, relationships, controlled knowledge operations,
> and AI/client integration without requiring migration into a proprietary knowledge store.
>
> Obsidian is an important supported Markdown/PKM workflow, not an architectural dependency. REST,
> MCP, CLI, the Web Dashboard, and future integrations are adapters over shared knowledge-domain
> services.

VaultBridge is self-hosted, not local-only. It may run on a workstation, NAS, home server, VPS, or
another operator-controlled host. "Local-first" describes the default privacy and processing model:
no mandatory cloud AI, embedding, database, or knowledge service is required.

## Product principles

1. **Portable knowledge is authoritative.** Markdown files and portable metadata are the source of
   truth. SQLite indexes, embeddings, relationship projections, caches, diagnostics, and other
   machine-oriented structures are derived state. Derived state must remain rebuildable where
   feasible and must never become a required proprietary replacement for the Markdown knowledge
   base.
2. **Markdown-native, not Obsidian-dependent.** Support Obsidian-compatible syntax where useful,
   but do not make one application's dialect the architectural core. Dialect-specific parsing and
   capabilities belong behind explicit boundaries that can feed shared domain representations.
3. **Client agnostic.** REST, MCP, CLI, the Web Dashboard, AI agents, scripts, and future adapters
   reuse domain services. No adapter, including MCP or the dashboard, becomes the product core or
   calls another adapter by default.
4. **Self-hosted and local-first, not local-only.** Local semantic processing remains the default
   direction. No external AI, embedding, database, or cloud service is mandatory.
5. **Safe knowledge operations.** Preserve vault-relative containment, symlink protection,
   Markdown-only mutation boundaries, conservative non-overwriting writes, authentication,
   idempotency, bounded inputs, minimal API surface, no arbitrary filesystem API, and no secret or
   knowledge-content logging.
6. **Measured derived intelligence.** Semantic ranking, relationship-aware retrieval, future graph
   signals, models, chunking, and indexes require repeatable evaluation rather than intuition.
   Derived relationship capability begins read-first and does not imply a graph database.
7. **Platform-neutral core.** TrueNAS, Docker, and future distribution targets package and launch
   the same application. They do not own or fork knowledge-domain behavior. The bundled dashboard
   remains one first-party client in the normal application image, not a required control plane.
8. **Simple operations; no premature infrastructure.** A single application plus SQLite should
   remain enough for normal personal knowledge bases. Do not add Redis, Celery, Qdrant, Kubernetes,
   a message broker, a second UI service, or distributed index coordination without measured need
   and an explicit architecture decision.

## Knowledge-space concept

A **knowledge space** is the product-level boundary for a contained Markdown knowledge base and the
policies/capabilities applied to it. Today VaultBridge supports exactly one knowledge space backed by
one contained Markdown filesystem root. The existing "vault" is the first knowledge-space
implementation and remains the name used by current classes, configuration, storage mounts, API
fields, resource URIs, and compatibility contracts.

Roadmap terminology does not authorize an immediate rename of `VaultService`, `VAULT_PATH`,
API fields, MCP resources, paths, or other identifiers. Terminology should change in implementation
only when it creates architectural value and a compatibility-safe migration has been designed.

## Conceptual architecture direction

This is the product direction, not a claim that generic `DocumentService`, `MetadataService`, or
`KnowledgeSpace` abstractions already exist:

```text
                         Knowledge clients
             +--------------+-------+--------------+
             | REST / API   |  MCP  | CLI / UI     |
             +--------------+-------+--------------+
                            |
                     protocol adapters
                            |
                            v
                      VaultBridge core
             +--------------+--------------+
             |              |              |
             v              v              v
        documents /      retrieval /   relationships /
           writes          search         metadata
             +--------------+--------------+
                            |
                            v
                 Markdown knowledge space
                   /                    \
                  v                      v
     authoritative files          rebuildable derived state
      Markdown / metadata        SQLite / embeddings / views
```

The direction is to normalize useful portable PKM semantics in the domain layer while keeping
syntax-specific parsers and protocol-specific presentation at the edges. It does not introduce a
second authoritative store.

---

# Current project state — `v1.3.0` published

The published stable GitHub Release and GHCR application release are `v1.3.0`, built from source commit
`a7e14ece0de74632d1d9be599d53678931dc64b3`. The exact image is
`ghcr.io/mrtrollex/vaultbridge:1.3.0` with OCI digest
`sha256:5a1709c279c3731f891b59026adb7e8f5497c299687596b74b49ffd64a9f5a0e`.
Stable aliases `1.3`, `1`, and `latest` were also published.

This application release does **not** prove that the upstream TrueNAS Community App contains the
new v1.3.0 MCP configuration fields. Upstream TrueNAS package/image-version updates, including its
automated image-update process, remain a separate distribution lifecycle. The unresolved VB-082
post-merge validation gates also remain separate from application publication.

## Current implementation architecture

```text
Clients: REST / Web Dashboard / MCP / CLI / integrations
                              |
                        thin adapters
                              |
       +----------------------+-----------------------+
       |                      |                       |
       v                      v                       v
  VaultService      SemanticSearchService    RelationshipService
       |                      |                       |
       |              SemanticRepository       WikilinkResolver
       |                      |                       |
       +----------------------+-----------------------+
                              |
                    one contained Markdown root
                              |
                +-------------+-------------+
                |                           |
                v                           v
       authoritative `.md` files    derived SQLite / embeddings
```

Current ownership is intentionally concrete:

```text
app/main.py                       application composition and dependency wiring
app/api/                          health, note/search, and relationship REST adapters
app/ui/                           bundled REST-backed dashboard client
app/cli.py                        local read and stopped-service index operations
app/mcp_server.py                 shared stdio/HTTP MCP adapter and optional safe writes
app/services/vault.py             contained Markdown reads, writes, listing, and literal search
app/services/semantic_search.py   chunking, embeddings, ranking, and index orchestration
app/repositories/semantic.py      SQLite semantic persistence
app/services/wikilinks.py         Obsidian-compatible wikilink parsing and safe resolution
app/services/relationships.py     live outgoing relationships and verified backlinks
app/services/duplicate_candidates.py advisory live-title and semantic duplicate evidence
```

There is no generic portable document model, normalized multi-dialect relationship store, metadata
query engine, capture pipeline, hygiene engine, or multiple-knowledge-space runtime today.

## Completed foundation tasks

- [x] **VB-001 — Project identity**
- [x] **VB-002 — Typed configuration**
- [x] **VB-004 — VaultService**
- [x] **VB-003 — FastAPI routers**
- [x] **VB-005 — Semantic service/repository split**
- [x] **VB-010 — Semantic index state model**
- [x] **VB-011 — Batch index commits**
- [x] **VB-012 — Background startup indexing**
- [x] **VB-013 — Enqueue reindex after note writes**
- [x] **VB-014 — Optional filesystem watcher**
- [x] **VB-015 — Rich health/readiness output**
- [x] **VB-020 — Markdown heading-aware chunker**
- [x] **VB-021 — Embed title + heading hierarchy + chunk**
- [x] **VB-022 — Retrieval evaluation fixture**
- [x] **VB-023 — Retrieval benchmark command**
- [x] **VB-024 — Tune hybrid ranking from evaluation data**
- [x] **VB-025 — Fingerprint semantic embedding compatibility**
- [x] **VB-030 — Duplicate candidate service**
- [x] **VB-031 — Verified related-note suggestions**
- [x] **VB-040 — Structured JSON logging**
- [x] **VB-041 — Request IDs and latency logging**
- [x] **VB-043 — Lightweight rate limiting**
- [x] **VB-050 — Introduce `/api/v1`**
- [x] **VB-052 — Generic Docker deployment docs**
- [x] **VB-053 — TrueNAS deployment docs**
- [x] **VB-054 — Publish GHCR image workflow**
- [x] **VB-056 — GitHub v1.0 release checklist**
- [x] **VB-057 — Enforce symlink containment in vault enumeration**
- [x] **VB-058 — Fix cross-platform path assertion**
- [x] **VB-059 — Align v1.0 version metadata**
- [x] **VB-060 — Public repository exposure audit**

## Current verified release baselines

The `v1.0.0` release established the public repository, exact-source CI, GHCR publication,
anonymous exact-digest pull, and a disposable TrueNAS runtime smoke. The later `v1.3.0` stable
release is identified above. Immutable artifact values and complete historical evidence remain in
[`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md); this roadmap does not replace that record.

## Current known limitations

1. One configured filesystem root provides the only current knowledge space.
2. External filesystem changes are automatically queued only when the optional watcher is enabled.
3. The deterministic retrieval fixture does not by itself measure representative real-model quality
   or latency.
4. Multiple VaultBridge processes sharing one semantic index are not coordinated.
5. Relationship extraction understands Obsidian-compatible wikilinks only; standard Markdown links,
   portable aliases, tags, and general frontmatter metadata are not yet normalized domain features.
6. Clients can still invent relationship targets unless they use verified VaultBridge results.

---

# Milestone 1 — Application foundation ✅ COMPLETE

**Goal:** convert the original working prototype into a maintainable application without changing the public API.

Completed:

```text
VB-001
   ↓
VB-002
   ↓
VB-004
   ↓
VB-003
   ↓
VB-005
```

Exit criteria:

- [x] typed configuration
- [x] thin `main.py`
- [x] API routers separated from domain logic
- [x] independently testable vault service
- [x] semantic orchestration separated from persistence
- [x] public API compatibility preserved
- [x] semantic tests can run without downloading the real model

---

# Milestone 2 — Index lifecycle and non-blocking synchronization ✅

**Goal:** make semantic indexing observable, resumable, and eventually independent of normal search requests.

## Completed

### VB-010 — Add index state model ✅

Persisted lifecycle states:

```text
uninitialized
     |
     v
 indexing
   /   \
  v     v
ready  error
```

Key behavior:

- state is persisted in SQLite metadata,
- compatible existing indexes can become `ready`,
- signature mismatch invalidates old semantic data and returns to `uninitialized`,
- interrupted `indexing` is recovered as `error`,
- successful retry transitions to `ready`.

### VB-011 — Batch index commits ✅

Key behavior:

- synchronization mutations commit in configurable note-count batches,
- completed batches remain durable after interruption,
- retry reuses completed work through incremental synchronization,
- synchronization remains synchronous internally and is safe to run in the background.

### VB-012 — Background startup indexing ✅

Key behavior:

- application startup schedules one in-process background synchronization job,
- normal semantic search no longer performs synchronization inline,
- first-time search returns no results until the initial index is ready,
- a previously ready committed index remains searchable during refresh,
- shutdown requests cancellation and stops safely between batches; active uninterruptible calls can delay exit.

### VB-013 — Enqueue reindex after note writes ✅

Key behavior:

- successful API note mutations enqueue their vault-relative note path,
- duplicate pending paths coalesce in one in-process queue,
- full and targeted synchronization remain serialized by the VB-012 worker,
- writes during active work receive a follow-up refresh, and failed full jobs retain full-retry debt,
- strict targeted failures keep the previous committed index and retain paths for retry,
- committed writes stay successful if enqueue/submission fails,
- shutdown leaves Markdown authoritative; the next startup full synchronization recovers discarded in-memory work.

### VB-015 — Rich health/readiness output ✅

Expose useful lifecycle/progress information without requiring Docker-log inspection.

Implemented flat compatibility-preserving response:

```json
{
  "ok": true,
  "vault_exists": true,
  "semantic_index_ready": false,
  "semantic_index_state": "indexing",
  "semantic_search_available": true,
  "semantic_indexer_running": true,
  "full_sync_required": false,
  "indexed_notes": 734,
  "semantic_chunks": 4610,
  "vault_notes": 842,
  "last_successful_sync": "2026-08-23T10:00:00+00:00"
}
```

The counts expose useful operator context and inferred completeness. VB-015 does not introduce
explicit per-sync current-note, percentage, current-batch or ETA counters; those remain future work.

### VB-014 — Optional filesystem watcher — P1 ✅

The disabled-by-default recursive watcher uses `watchdog`, safe vault-relative Markdown event
interpretation, one monotonic debounce dispatcher, and the existing VB-012/VB-013 targeted queue.
Deletes remove only their derived path rows and renames process both old and new paths. Startup full
synchronization remains authoritative for downtime reconciliation.

## Milestone 2 exit criteria

- [x] synchronization commits durable progress in batches
- [x] full vault rebuild no longer runs inline in normal search requests
- [x] restart does not discard already committed batches
- [x] lifecycle and inferred completeness are observable
- [x] note writes can trigger targeted semantic refresh
- [x] optional external Markdown watching reuses targeted semantic refresh
- [x] no external queue/service is required

---

# Milestone 3 — Retrieval quality and evaluation ✅ COMPLETE

**Goal:** improve relevance using measured changes before considering a larger model or vector database.

### VB-020 — Markdown heading-aware chunker — P0 ✅

- heading hierarchy is preserved in chunk metadata
- ATX section boundaries are preferred over arbitrary character boundaries
- bounded oversized sections split through exact source slices; prose-only overlap does not cross sections
- adjacent tiny sections coalesce with bounded first-to-last hierarchy metadata
- lists, fenced code, nested headings, Unicode and sparse/empty notes have focused tests
- the chunker signature automatically invalidates and rebuilds old derived chunks

### VB-021 — Embed title + heading hierarchy + chunk — P0 ✅

Embedding context should intentionally include note/section identity.

Example:

```text
Title: Infrastructure Notes
Heading: Jellyfin > Transcoding

<chunk content>
```

Implemented with one deterministic builder shared by full and targeted indexing. Persisted Markdown
chunk content remains unchanged; canonical heading metadata is added only to embedding input and is
not redundantly prepended when already present at the chunk start. The `v3-heading-context`
signature automatically invalidates and rebuilds VB-020 embeddings without a schema migration.

### VB-022 — Retrieval evaluation fixture — P0 ✅

Implemented with sanitized EN/SK/cross-language query cases under:

```text
tests/eval/retrieval_cases.json
```

The deterministic fixture runs through the production retrieval pipeline with only FastEmbed
replaced. Its checked 13-case baseline is Hit@1 100%, Hit@3 100% and MRR 100%. Reversed-order and
material-tie guards keep the fixture deterministic, while controlled ablations prove semantic,
lexical, heading-context and cross-language sensitivity.

### VB-023 — Retrieval benchmark command — P1 ✅

The repository benchmark now runs the sanitized evaluation corpus in disposable state through the
real production model/service contract. Stable Markdown and JSON record query latency, paths,
headings, semantic/lexical/final scores, ranks, Hit@1, Hit@5, MRR, and latency aggregates. Model
loading/indexing is reported separately from query latency; offline tests use deterministic injected
embeddings and timing without changing the VB-022 baseline or retrieval behavior.

### VB-024 — Tune hybrid ranking from evaluation data — P1 ✅

Completed without changing the established semantic-to-lexical ratio, boosts or thresholds. The
hybrid combination is normalized by total signal weight instead of clamped, so high-relevance
candidates retain separation. Equal candidates use explicit semantic, lexical and canonical-path
ordering; equal chunks within a note fall back to source chunk index. Before and after metrics remain
Hit@1 100%, Hit@3 100% and MRR 100%, with all per-case ranks unchanged.

Candidate metrics:

- Top-1 accuracy
- Recall@5
- Mean Reciprocal Rank
- latency

### VB-025 — Fingerprint semantic embedding compatibility — P0 ✅

The semantic index now binds stored vectors to the effective FastEmbed mean-pooling adapter contract
and exact resolved ONNX/tokenizer artifacts. Legacy or missing signatures rebuild once from Markdown;
identity-resolution failures keep old derived rows intact but unavailable. Runtime batch, memory,
provider and ranking settings do not cause unnecessary rebuilds.

## Milestone 3 exit criteria

- [x] retrieval quality is repeatably measurable
- [x] Markdown structure contributes context
- [x] ranking regressions are detectable
- [x] default-model changes require evidence

---

# Milestone 4 — Knowledge-maintenance operations

**Goal:** let clients maintain a knowledge base safely, not merely search it.

### VB-030 — Duplicate candidate service — P1 ✅

The protected legacy and `/api/v1` duplicate-candidate routes now combine conservative live filename
equivalence with one existing semantic search. Exact-title evidence leads semantic-only results; all
semantic paths cross the VB-031 live Markdown verification boundary, and bounded overfetch is applied
before canonical-path deduplication and the caller limit. The result is advisory: semantic similarity
is not a duplicate verdict, and the operation performs no merge, note write, or index mutation.

### VB-031 — Verified related-note suggestions — P1 ✅

The existing legacy and `/api/v1` related-note routes now filter semantic candidates through live
vault containment and Markdown-file verification, return canonical paths/titles, and use bounded
overfetch to backfill filtered stale candidates without changing ranking or response fields. Live
content edits can still leave semantic scores/snippets stale until synchronization.

### VB-032 — Section-level update design / ADR — P1 — DEFERRED / OPTIONAL

Design concurrency and section identity only if section mutation becomes a demonstrated need.

### VB-033 — `updateNoteSection` endpoint — P1 — DEFERRED / OPTIONAL

Do not implement before a resumed VB-032 decision; any future endpoint must include conflict detection.

### VB-034 — Opt-in verified backlink insertion — P2

This remains the first relationship task allowed to mutate Markdown. It depends on verified
relationship resolution through VB-102, stays explicitly opt-in, never invents or creates a target
note, and requires conflict/write-safety design before implementation.

---

# Milestone 5 — Operational maturity and security — COMPLETE

**Goal:** make VaultBridge predictable to run continuously on NAS/server hardware.

### VB-040 — Structured JSON logging — P0 ✅
### VB-041 — Request IDs and latency logging — P0 ✅
### VB-044 — Liveness and readiness endpoints — P0 ✅
### VB-045 — Index integrity/rebuild CLI — P0 ✅
### VB-042 — API key rotation — P1 ✅

The required current `API_KEY` can overlap with one optional secret-safe `API_KEY_PREVIOUS` during
an operator-controlled restart/redeploy rotation window. Both legacy and `/api/v1` protected routes
share the same constant-time Bearer verification; public health routes remain unaffected.

### VB-043 — Lightweight rate limiting — P1 ✅

Protected legacy and `/api/v1` application traffic now uses one process-local, bounded fixed-window
limiter before authentication. It uses monotonic time and the direct ASGI peer address, deliberately
ignores forwarded client-address headers, and preserves public health/privacy access. HTTP `429`
includes a safe fixed detail and `Retry-After`. The limiter is not persistent or distributed and
adds no Redis, service, background worker, or dependency.

---

# Milestone 6 — Public API and developer experience ✅ COMPLETE

**Goal:** make VaultBridge easy to understand and deploy by someone other than the original author.

### VB-050 — Introduce `/api/v1` — P0 ✅

New integrations use the stable `/api/v1` note/search namespace and explicit `*V1` operation IDs.
The original unversioned paths and operation IDs remain compatibility aliases for the existing
ChatGPT Action and other clients. Operational health probes remain unversioned.

### VB-051 — VaultBridge CLI — P1 ✅

The standard-library local CLI exposes read-only status, literal search, and existing-index semantic
retrieval plus stopped-service incremental indexing and clean reindexing. It reuses `VaultService`
and `SemanticSearchService`, preserves the VB-045 `index check`/`index rebuild` interface, validates
vault-relative folders, and live-verifies semantic result paths before display.
### VB-052 — Generic Docker deployment docs — P0 ✅
### VB-053 — TrueNAS deployment docs — P0 ✅

The TrueNAS SCALE runbook now preserves the existing compatibility identifiers and documents the
source-built Custom App YAML include, separate vault/derived-data datasets, UID/GID access principle,
fresh installation, first-start health verification, managed/no-Git updates, rollback, logs, and
stopped-service semantic-index maintenance without changing deployment behavior.

ChatGPT Action remains an integration example, not the core product identity.

---

# Milestone 7 — Distribution and `v1.0.0` ✅ COMPLETE

**Goal:** ship VaultBridge as a polished public open-source project.

### VB-054 — Publish GHCR image workflow — P0 ✅

Published GitHub Releases now rerun the established source checks and build the existing production
Dockerfile before pushing a normal Linux runner image to GHCR. Exact semantic-version tags are always
created; only stable releases update rolling aliases and `latest`. Publication uses job-scoped
package-write permission, `GITHUB_TOKEN`, SHA-pinned actions, OCI metadata, and a post-push manifest
inspection. Existing source-build deployments remain unchanged.

### VB-055 — Multi-architecture image — P1
### VB-056 — GitHub v1.0 release checklist — P0 ✅

### VB-057 — Enforce symlink containment in vault enumeration — P0 ✅

Literal search, note listing, and semantic full synchronization now apply one resolved-target
containment rule before reading or inspecting discovered Markdown files. External and broken
symlinks are skipped, internal file aliases are canonicalized and deduplicated, and real Linux
symlink regressions cover service, legacy/v1 HTTP, direct-read, and semantic-index paths.

### VB-058 — Fix cross-platform path assertion — P0 ✅

The remaining native-Windows failure was confirmed as a hard-coded POSIX test assumption rather
than an API or runtime defect. The exact note path is now asserted through `pathlib.Path`, preserving
native VaultService serialization while passing on Windows and Linux. No application behavior or
public contract changed.

### VB-059 — Align v1.0 version metadata — P0 ✅

The existing package and FastAPI application metadata now target `1.0.0` without introducing a new
version source. Release identity remains the Git tag/GitHub Release, prerelease and stable GHCR tags
remain workflow-derived, and immutable deployments remain digest-addressed. No API, runtime,
semantic-index, dependency, deployment, or workflow behavior changed.

### VB-060 — Public repository exposure audit — P0 ✅

Tracked source, generated artifacts, public documentation, workflow permissions, licensing, and all
reachable branch history were audited and remediated before exposure. The generated distribution
bundle was removed, `SECURITY.md` now states the current reporting-channel limitation, and the
repository owner's historical commit metadata was replaced with the configured GitHub noreply
identity after a verified external backup. Rewritten remote branches were updated atomically with
explicit leases. The repository is now public and the anonymous clean-install gate has passed.

## `v1.0.0` acceptance criteria

- [x] clean install succeeds from public documentation
- [x] CI is green
- [x] container image is published
- [x] public API is versioned
- [x] full semantic rebuild does not block ordinary request handling
- [x] retrieval evaluation suite exists
- [x] no known authentication/path-traversal bypass
- [x] secrets are not committed or logged
- [x] upgrade/rebuild procedure is documented

VB-056 and final stable release evidence are recorded in
[`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md). VB-057 closes the confirmed literal
search/list symlink-containment bypass after focused Linux and compatibility review, VB-059 closes
the version-alignment blocker, and VB-060 closes the repository-exposure-safety blocker. Public
repository visibility, anonymous isolated clean installation, RC2 verification, stable GitHub/GHCR
publication, stable alias/digest verification, and final exact-digest TrueNAS smoke testing are
complete. Milestone 7 is complete; VB-055 remains an optional post-v1 P1 improvement.

---

# Milestone 8 — Web Dashboard / operator experience — COMPLETE

**Goal:** provide a small first-party browser interface for inspecting and using VaultBridge without
turning the project into a general knowledge-management frontend.

The bundled shell, authenticated browser session, public health-backed Overview, protected
literal/semantic Search interface, and final usability/accessibility/release hardening are complete.
The first-release information architecture is:

```text
Overview
Search
API / Integration
About
```

The Overview presents facts already exposed by the current public `/health` contract, including
application/vault health, semantic lifecycle and availability, note/chunk counts, and the last
successful full synchronization. Current HTTP responses do not expose watcher enabled/running
state, so the first dashboard omits it rather than inferring it. Search exposes the existing literal
and semantic related-note behavior without duplicating ranking, filtering, containment, or domain
logic.

Implementation direction:

- follow accepted [ADR 0003](docs/adr/0003-web-dashboard-architecture-and-security.md): canonical
  `/ui/`, explicit `/ui/assets/`, same-origin API calls, no SPA catch-all, and no second service;
- keep the API independently usable and keep API/CLI behavior unchanged when the UI is unused;
- prefer small bundled HTML/CSS/vanilla JavaScript or similarly lightweight assets;
- require no mandatory Node/npm build pipeline, frontend framework, second service, or second
  container for the first version;
- preserve the existing Bearer-auth boundary for protected data requests; never inject, return, log,
  or place `API_KEY` in HTML, JavaScript source, or URLs;
- retain the operator-supplied Bearer key in namespaced `sessionStorage` only after successful
  protected API validation; logout and `401` clear it, and strict CSP plus text-only rendering reduce
  the browser threat surface;
- display index state only. Any UI-triggered index mutation requires a separate safety design that
  preserves stopped-service CLI maintenance and process ownership.

Task sequence:

```text
VB-070 architecture and security design ✓
   ↓
VB-071 dashboard shell and authenticated session ✓
   ↓
VB-072 overview and health visibility ✓
   ↓
VB-073 search interface ✓
   ↓
VB-074 usability, accessibility, and release hardening ✓
```

Milestone exit criteria:

- [x] platform-neutral dashboard architecture and authentication threat model are agreed
- [x] canonical dashboard shell and authenticated `sessionStorage` lifecycle are implemented
- [x] one lightweight dashboard is bundled with and verified in the normal VaultBridge
  application/image
- [x] the API and CLI remain independently usable and behaviorally compatible
- [x] overview data reuses the existing public health contract without duplicating domain logic
- [x] search behavior reuses existing backend/domain capabilities
- [x] no note editor, account system, general file manager, or live index-rebuild mutation is added
- [x] accessibility, privacy-safe rendering, browser smoke tests, and container verification pass

VaultBridge `v1.1.0` is published and its exact OCI index is anonymously pullable. VB-080 defines the
version-neutral packaging contract and VB-081 is complete with exact tag `1.1.0`, matching metadata,
officially generated artifacts, and current-upstream Docker-backed package validation. VB-075's
exact-source CI and exact-image functional release evidence also pass. VB-083 is complete after
upstream PR #5805 was reviewed and merged, accepted source/generated entries were verified, and
Discover Apps availability was operator-confirmed. VB-082 remains in progress / partial validation:
the initial catalog install path is operator-confirmed, while unresolved post-merge lifecycle gates
remain open.

---

# Milestone 9 — TrueNAS Community App distribution — UPSTREAM ACCEPTED / POST-MERGE VALIDATION IN PROGRESS

**Goal:** make the same published, dashboard-capable VaultBridge image installable through the
TrueNAS Apps catalog with generated configuration and Web Portal integration.

TrueNAS application packaging is a distribution adapter, not the VaultBridge runtime architecture:

```text
TrueNAS Apps UI
      |
      | configuration / storage / port / environment
      v
TrueNAS catalog definition
      |
      | pulls
      v
ghcr.io/mrtrollex/vaultbridge:<released-version>
      |
      v
normal VaultBridge runtime
      |
      +-- /api/v1
      +-- /ui (bundled dashboard completed in Milestone 8)
      +-- /vault
      +-- semantic data
```

The core `mrtrollex/VaultBridge` repository owns the application runtime, API, semantic behavior,
CLI, bundled dashboard, Dockerfile, GHCR image, and generic deployment documentation. The accepted
upstream Community App definition is owned by `truenas/apps`. The published image is the
interface between the repositories. Temporary local packaging/test fixtures may exist for
reproducibility, but there must be no permanent `VaultBridge-TrueNAS` runtime fork or two
authoritative catalog definitions. TrueNAS-specific code must not enter core domain services.

Task sequence and current gates:

```text
VB-080 packaging design ✓
   +
published and verified dashboard-capable VaultBridge image (VB-075)
   ↓
VB-081 Community App definition ✓
   ↓
VB-082 executable pre-upstream validation ✓
   ↓
VB-083 submission/review/merge/catalog availability ✓
   ↓ creates the supported real catalog surface
VB-082 post-merge lifecycle validation (IN PROGRESS / PARTIAL)
   ↓
VB-082 completion after its remaining gates have actual evidence
```

Milestone exit criteria:

- [x] packaging consumes a published VaultBridge GHCR image without rebuilding/forking runtime code
- [x] release-neutral metadata, questions, Compose template, and focused fixtures statically render
  through the current upstream library without changing VaultBridge runtime code
- [x] storage, identity, secret, port, health, portal, resource, upgrade, and rollback contracts are
  documented in [`docs/TRUENAS_COMMUNITY_APP_DESIGN.md`](docs/TRUENAS_COMMUNITY_APP_DESIGN.md)
- [x] current-upstream schema, generation, catalog-port, render, and local deploy/health checks pass
- [x] pre-upstream install/restart/edit/persistence/negative/host-path-uninstall gates are recorded;
  initial real-catalog install/form/masking/Portal/Host Path/ixVolume configuration is
  operator-confirmed
- [ ] edit-form persistence and ixVolume uninstall semantics are verified; upgrade remains
  **UNSUPPORTED / NO VALID PRIOR PACKAGE STATE** and rollback remains blocked until a valid prior
  catalog revision exists
- [x] upstream PR #5805 was reviewed and merged; accepted source/generated entries and Discover Apps
  availability are verified

---

# Milestone 10 — MCP integration — COMPLETE

**Goal:** add MCP as a small client/integration surface over existing VaultBridge services without
replacing REST, duplicating domain logic, or creating a permanent runtime fork.

ADR 0004 establishes the transport and ownership model:

```text
local MCP client
       |
       | stdio (VB-091, read-only)
       v
MCP protocol adapter
       |
       +------------------+
       |                  |
       v                  v
 VaultService     SemanticSearchService
       |                  |
   Markdown        SQLite / FastEmbed

future network client
       |
       | Streamable HTTP /mcp (opt-in, existing process/port)
       +------------------> same MCP adapter and services
```

VB-091 is deliberately limited to an explicit read-only stdio entry point, five tools
(`list_notes`, `read_note`, `search_notes`, `related_notes`, and `duplicate_candidates`), and one
contained note Resource template. It starts no FastAPI app, background indexer, watcher,
synchronization, or semantic-index writer. The main API process is not required for local stdio.

VB-092 implements the selected Streamable HTTP transport at opt-in `/mcp` in the existing FastAPI
process and port. It reuses the live services, current/previous Bearer verification, direct-peer
application limiter, and parent lifespan while delegating protocol and Host/Origin checks to the
official SDK. Legacy HTTP+SSE, a second service/container, write tools, OAuth, Prompts, and
VaultBridge subscription features remain outside the scope.

VB-093 completes the production-image CI gate. PR #62 merged as `f03f48b`, and GitHub Actions run
`35355236057` passed fresh disabled and enabled containers from the exact image built by the Docker
job, with official-client protocol, read-only surface, REST, authentication, Host/Origin, clean
shutdown, and disposable-data checks.

VB-106 extends the shared adapter with default-off `create_note` and `append_note` parity. The
default surface remains the exact seven read-only tools after VB-103; write-enabled HTTP reuses the
live application indexer, while stdio owns a targeted indexer only in write mode and shuts it down
without starting a full sync. The checked-in TrueNAS package source adds first-class MCP HTTP,
write, Host, and Origin fields on the existing Web Port. The `v1.3.0` application image containing
this work is now published; the separate upstream TrueNAS package/catalog update remains externally
owned delivery work and is not implied by the application release.

A separate isolated TrueNAS smoke built main commit `8ae99d3` from source and passed liveness,
authenticated REST and the official MCP client over Streamable HTTP protocol `2026-07-28` against
synthetic `Smoke.md`, with exactly the five read-only tools. The existing production app on port
`30491` remained healthy, and all disposable smoke resources were removed. This is not evidence that
the production vault was used or that the Community catalog already ships `v1.2.0`.

Task sequence:

```text
VB-090 MCP architecture / ADR ✓
   ↓
VB-091 read-only stdio MCP server ✓
   ↓
VB-092 read-only Streamable HTTP implemented
   ↓
VB-093 container-level MCP HTTP validation ✓
```

Milestone exit criteria:

- [x] current MCP transports, authorization guidance, tool/Resource rules, and official Python SDK
  are documented from authoritative current sources
- [x] service ownership, process lifecycle, authentication, safety, observability, packaging, and
  compatibility contracts are accepted in
  [`docs/adr/0004-mcp-integration.md`](docs/adr/0004-mcp-integration.md)
- [x] the VB-091 read-only stdio tool and Resource surface is implemented and verified through the
  official SDK's in-memory and subprocess clients
- [x] the VB-092 read-only Streamable HTTP surface is implemented and verified locally through the
  official modern MCP client under SDK v2.1.1 and v2.2.0
- [x] VB-093 container smoke passes against the Docker job's exact `vaultbridge:ci` image
- [x] existing REST, dashboard, CLI, ChatGPT Action, Docker, and TrueNAS behavior remains unchanged
  after implementation; PR #55's Python job passes the full suite and compile check, and its Docker
  job passes Compose validation, Linux image build, MCP dependency import in that image, and MCP
  stdio EOF smoke

This milestone remains an independent post-v1 integration track. VB-091 did not replace or alter
VB-075's release-evidence criteria, which were completed separately on 2026-09-07. VB-092 and
VB-093 add no release or TrueNAS catalog claim; the successful gate is container CI evidence, not
production TrueNAS runtime validation.

---

# Milestone 11 — PKM Relationship Foundation — COMPLETE

**Goal:** establish a safe, read-first knowledge-relationship boundary derived from contained
Markdown while keeping authoritative files and existing client behavior compatible.

VB-100 through VB-105 established the first relationship capability using Obsidian-compatible
wikilinks. Wikilinks are the first supported relationship dialect, not the definition of the
relationship architecture. Parsing, outgoing-link, and backlink operations inspect live contained
Markdown and reuse `VaultService` for target containment, Markdown verification, symlink protection,
and canonical paths. `WikilinkResolver` owns the supported syntax and resolution rules;
`RelationshipService` owns the shared outgoing/backlink behavior.

The resulting relationships are a derived view, never a second source of truth. No persistent
relationship index, graph database, automatic note creation, or Markdown mutation was introduced by
VB-100 through VB-105. A persistent projection may be considered only after measurement shows that
live inspection is insufficient. REST and MCP remain thin adapters, and the dashboard remains a
bounded read-only consumer rather than a graph explorer, editor, file manager, or product core.

VB-105 evaluated verified relationships as a retrieval candidate. It found a narrow quality signal,
but the evidence did not justify a production ranking change because live-scan cost and
representative general-query benefit remained unproven. Production retrieval therefore remains
unchanged.

Task sequence:

```text
VB-100 parse and safely resolve Obsidian-compatible wikilinks ✓
   ↓
VB-101 verified outgoing note relationships ✓
   ↓
VB-102 verified backlinks ✓
   ↓
VB-103 REST and MCP note relationships ✓
   ↓
VB-104 dashboard note relationships ✓
   ↓
VB-105 evaluate relationship-aware retrieval signal ✓ (production ranking not supported)
```

VB-032 and VB-033 remain deferred/optional and are not prerequisites for this sequence. VB-034
remains in Milestone 4 as a P2 knowledge-maintenance operation, but its implementation is sequenced
after verified read-only relationship resolution. It remains optional rather than the primary
strategic next step. It is the first relationship task allowed to write Markdown and must remain
opt-in with conflict and write safety defined first.

Milestone exit criteria:

- [x] one deterministic parser/resolver handles the first supported relationship dialect, ignores fenced code,
  and resolves only verified contained Markdown targets through existing vault security boundaries
- [x] outgoing links and backlinks distinguish resolved from unresolved relationships and perform
  no writes or persistent graph indexing
- [x] `/api/v1` and MCP expose the same read-only domain capabilities without extending legacy REST
  aliases or duplicating relationship logic
- [x] the dashboard provides a bounded read-only relationship section without graph visualization
  or client-side relationship ownership
- [x] relationship-aware retrieval is measured against the existing semantic/lexical baseline before any
  production ranking decision
- [x] existing REST, MCP, CLI, dashboard, TrueNAS, semantic-search, authentication, containment, and
  deployment behavior remains compatible
- [x] VB-034 remains separately controlled and no automatic backlink or Markdown mutation occurs
  before it is explicitly implemented

---

# Milestone 12 — Portable PKM model — VB-111 NEXT TO SCOPE

**Goal:** give VaultBridge a portable document/metadata model that understands useful PKM semantics
without making any one Markdown application the architectural owner.

This milestone is incremental. VB-110 completed its design-first phase by accepting
[`ADR 0005`](docs/adr/0005-portable-pkm-document-model.md), which defines the domain model, syntax
boundaries, malformed-input and resource-limit responsibilities, compatibility implications, and
what remains live versus derived. The next step is to scope VB-111 independently in `BACKLOG.md`
before implementation. The milestone must not create another authoritative database or require
every conceptual field to be persisted.

### Accepted design and planned task sequence

VB-110 is accepted design work with an authoritative `BACKLOG.md` contract and ADR. VB-111 through
VB-114 remain roadmap-planned identifiers; each must receive its own authoritative backlog scope and
acceptance criteria before implementation begins.

```text
VB-110 portable PKM document model / ADR ✓
   ↓
VB-111 bounded YAML frontmatter parsing — next to scope
   ↓
VB-112 portable aliases and tags
   ↓
VB-113 contained standard Markdown relationships
   ↓
VB-114 normalized relationship view
```

### VB-110 — Define portable PKM document model / ADR — ACCEPTED

ADR 0005 defines a domain-level representation that can express, where available:

- canonical path and identity;
- title and aliases;
- headings;
- bounded frontmatter;
- tags;
- links and normalized relationships;
- content-derived metadata.

The accepted ADR distinguishes authoritative portable data from rebuildable projections. It favors
an in-memory/domain representation unless persistence is justified by measured behavior. Existing
`VaultService` containment and compatibility identifiers remain in place; the design should extend
current ownership rather than rename it cosmetically.

### YAML frontmatter

Safely parse an explicitly supported subset/profile of YAML frontmatter into bounded metadata.
Before implementation, decide how parser dependencies, unsafe YAML features, nesting/depth, scalar
and document sizes, duplicate keys, malformed delimiters, encoding failures, and unsupported values
behave. Malformed metadata must not bypass containment, make a note writable, or corrupt
authoritative Markdown.

### Aliases and canonical identity

Portable aliases may participate in relationship and knowledge operations without weakening exact
filesystem identity or containment. Alias ambiguity must be represented explicitly; VaultBridge
must not guess between multiple live notes or silently make an alias authoritative over a path.

### Tags

Expose supported portable tags as structured knowledge metadata. Tag semantics remain domain data,
not application-specific navigation or UI behavior.

### Standard Markdown links

Generalize relationship extraction beyond `[[wikilinks]]` to standard Markdown links that resolve
to contained Markdown notes. Reuse the verified `VaultService` and `RelationshipService` boundary;
do not duplicate containment or resolve arbitrary URLs/files as notes.

### Relationship normalization

Different supported syntax sources should be able to feed a shared derived representation such as:

```text
Relationship
- source
- target
- type
- origin / dialect
- resolved
```

This is a conceptual domain shape, not a commitment to a new persisted schema. Dialect-specific
metadata may remain available where needed, and source ordering/duplicate semantics require an
explicit design decision rather than accidental normalization.

Milestone progress and exit direction:

- [x] portable document and relationship concepts are accepted in ADR 0005;
- bounded frontmatter, aliases, and tags have explicit supported semantics and failure behavior;
- standard Markdown internal links reuse the existing verified relationship boundary;
- Markdown and portable metadata remain authoritative;
- no current API, environment variable, resource URI, class, or mount is renamed without a
  compatibility-safe reason.

---

# Milestone 13 — Knowledge Query Layer — PLANNED

**Goal:** let clients query knowledge using semantic, structural, and metadata constraints through
one safe domain capability.

The future capability should compose supported constraints such as:

- semantic and literal query text;
- contained folder/path scope;
- tags;
- supported frontmatter metadata;
- normalized relationship constraints;
- modification or creation metadata only where it is reliably available and clearly defined.

Conceptual request example:

```json
{
  "query": "TrueNAS authentication",
  "folder": "Projects",
  "tags": ["homelab"],
  "metadata": {
    "status": "active"
  }
}
```

This is not a committed API schema. The layer must not expose arbitrary SQL, SQLite internals,
arbitrary filesystem predicates, or an unbounded query language. REST, MCP, CLI, and the dashboard
should eventually consume one domain query capability instead of implementing independent
filtering/ranking semantics. Any frontmatter query language is part of this bounded capability, not
a separate generic language feature.

Proposed tasks, subject to `BACKLOG.md` definition after Milestone 12 establishes the model:

```text
VB-120 knowledge-query capability / ADR
   ↓
VB-121 bounded domain query implementation and evaluation
   ↓
VB-122 thin adapter adoption where separately approved
```

---

# Milestone 14 — Knowledge Capture / Portable Memory — PLANNED

**Goal:** allow clients and AI agents to capture knowledge safely into portable Markdown while
retaining provenance and human/operator control.

This is not a vector-memory database. The intended staged model is:

```text
capture
   ↓
portable Markdown inbox / draft
   ↓
duplicate / related analysis
   ↓
human or operator review
   ↓
explicit promote / append / create
```

Possible portable metadata includes source/provenance, creation timestamp, tags, and capture type.
No proprietary memory schema is mandatory. Capture must not silently persist chat history, rewrite
existing knowledge, assume every capture belongs in an authoritative note, or automatically merge,
promote, or delete content. Any approved write path must remain compatible with the existing
non-overwriting `create_note` and idempotent `append_note` safety model and post-commit indexing
rules.

Proposed tasks, to be refined after the query/model foundations:

```text
VB-130 capture and provenance model / ADR
   ↓
VB-131 portable inbox/draft capture
   ↓
VB-132 explicit review and promotion workflow
```

---

# Milestone 15 — Knowledge Hygiene — PLANNED

**Goal:** diagnose knowledge-base quality and safely propose maintenance actions before introducing
aggressive automatic mutation.

Potential read-first diagnostics include:

- unresolved relationships and broken contained Markdown links;
- orphan notes or notes without relationships, using explicit definitions;
- near-duplicate notes, reusing `DuplicateCandidateService` and verified related-note behavior;
- duplicate aliases and conflicting supported metadata;
- empty or suspiciously empty notes;
- stale derived-state diagnostics where staleness can be measured reliably.

Relationship resolution, containment, duplicate evidence, and live-note verification remain owned
by their existing domain boundaries. Diagnostics may later be consumed by the dashboard, but the
dashboard must not become a general Markdown editor. This milestone authorizes no automatic delete,
merge, rewrite, rename, or repair.

Proposed tasks:

```text
VB-140 knowledge-hygiene definitions / ADR
   ↓
VB-141 bounded read-only diagnostic services
   ↓
VB-142 thin diagnostic adapters and optional dashboard views
```

---

# Milestone 16 — Multiple Knowledge Spaces and Scope Policies — PLANNED

**Goal:** support explicitly scoped access and retrieval across more than one contained Markdown
knowledge space without weakening current safety or compatibility.

Conceptual policy boundary:

```text
KnowledgeSpace
- root
- read policy
- write policy
- indexing policy
- supported dialect / capabilities
```

Personal, work, research, and archive spaces are examples of future operator configuration, not
current support claims. Cross-space retrieval and writes must be explicitly permission-aware;
ambiguous identities must not be guessed across spaces. The design must preserve containment within
each root and define adapter-visible scope without exposing host paths. The old multiple-vault and
per-folder access-policy candidates are absorbed here; neither is current behavior.

This milestone does not authorize multi-tenancy, account administration, a hosted SaaS control
plane, or a dashboard user-management system. The current one-root vault configuration remains the
only supported implementation until a design and compatibility/migration plan are accepted.

Proposed tasks:

```text
VB-150 knowledge-space and scope-policy ADR
   ↓
VB-151 compatibility-safe multi-space domain boundary
   ↓
VB-152 permission-aware query and write adapter integration
```

---

# Post-1.0 roadmap classification

## Product capabilities

Milestones 12–16 now own the portable PKM model, knowledge query, controlled capture, hygiene, and
multiple-space/scoping directions. They are product capabilities, not storage-backend selections.
VB-032/VB-033 remain deferred optional section mutation, and VB-034 remains an optional opt-in
verified backlink write; none is the strategic NEXT item.

## Domain architecture

VB-110 establishes accepted portable-PKM domain semantics through its authoritative backlog contract
and ADR 0005. VB-111–VB-114 and the proposed VB-120/VB-130/VB-140/VB-150 design tracks remain
roadmap-planned rather than implementation-ready until each is added to `BACKLOG.md` with bounded
acceptance criteria.

## Deployment and integration candidates

- read-only operating mode;
- webhook/event integrations with explicit authentication, privacy, and delivery semantics;
- optional multi-architecture image publication (existing VB-055);
- remaining TrueNAS lifecycle validation (existing VB-082) and separately owned upstream package
  updates.

## Scale / optional backend candidates

These are implementation/scaling choices, not product milestones. Promote one only when repeatable
measurements show that the current local implementation cannot meet an accepted requirement:

- pluggable embedding providers
- alternative local embedding models
- SQLite vector extension / HNSW acceleration
- Qdrant or another external vector backend for demonstrably large knowledge spaces
- distributed semantic-index coordination

---

# Explicit non-goals

Do not implement these unless requirements explicitly change:

- arbitrary filesystem API
- remote shell execution
- automatic note deletion
- a proprietary knowledge database replacing authoritative Markdown and portable metadata
- a mandatory Obsidian dependency or an attempt to become an Obsidian clone
- a general-purpose Markdown editor
- automatic AI-driven rewriting, merging, renaming, or deletion of authoritative knowledge
- a hidden AI memory store that users cannot inspect and export as portable knowledge
- silently converting chat history or every capture into permanent knowledge
- mandatory cloud services or a mandatory cloud LLM/embedding provider
- mandatory external embedding APIs
- Kubernetes deployment
- full Obsidian synchronization replacement
- general-purpose vector database by default
- distributed/multi-process semantic-index coordination before there is a real need
- a premature generic storage-provider abstraction merely to advertise Notion, Google Docs, or
  other non-Markdown systems
- product claims for unsupported Markdown dialects or PKM applications
- a multi-user SaaS/account platform without a demonstrated product need
- making VaultBridge TrueNAS-only or creating a separate TrueNAS runtime fork
- requiring the Web Dashboard for API or CLI use
- turning the dashboard into an Obsidian replacement, graph explorer, WYSIWYG editor, general file
  manager, multi-user/account administration system, or NAS administration interface
- returning or embedding configured secrets, or adding a secret-return endpoint
- exposing live semantic-index rebuild through HTTP without a separate concurrency/safety design
- requiring Node/npm, a frontend framework, a second UI service/container, an external database, or
  Kubernetes for the initial dashboard
- changing authoritative Markdown or local-first semantic behavior for platform packaging

---

# Recommended implementation path

```text
FOUNDATION ✓
   ↓
INDEX / DERIVED KNOWLEDGE ✓
   ↓
RETRIEVAL ✓
   ↓
SAFE KNOWLEDGE OPERATIONS ✓ / optional writes remain controlled
   ↓
PUBLIC API / CLI / DASHBOARD ✓
   ↓
DISTRIBUTION / TRUENAS ✓ / lifecycle follow-up remains open
   ↓
MCP ✓
   ↓
PKM RELATIONSHIP FOUNDATION ✓
   ↓
VB-110 PORTABLE PKM MODEL / ADR ✓
   ↓
VB-111 FRONTMATTER — NEXT TO SCOPE
   ↓
KNOWLEDGE QUERY LAYER
   ↓
KNOWLEDGE CAPTURE / PORTABLE MEMORY
   ↓
KNOWLEDGE HYGIENE
   ↓
MULTIPLE KNOWLEDGE SPACES / SCOPE POLICIES
   ↓
OPTIONAL SCALE / BACKEND WORK WHEN MEASURED
```

`v1.0.0` has shipped, and VB-070 through VB-074 complete Milestone 8's dashboard design,
shell/session, Overview, Search, and final hardening. VB-080 completes the version-neutral Community
App packaging design, and VB-081's definition now pins the published `1.1.0` image with matching
metadata. **Release `v1.1.0`, workflow, aliases, digests, labels, platform, anonymous pull,
exact-source CI, and the full exact-image functional gate are recorded; VB-075 is complete.**
Official package generation/deployable validation passes and VB-081 is complete. VB-083 is complete:
PR #5805 was reviewed and merged, the accepted package uses the reviewer-provided CDN icon and
default Web UI port `30491`, and Discover Apps availability is operator-confirmed.
VB-082 has completed every executable pre-upstream lifecycle and negative gate with sanitized
evidence or an allowed classification. Post-merge operator evidence also confirms initial catalog
installation, the usable masked-secret form, `/ui/` Portal, Host Path and ixVolume configuration,
healthy vault visibility, and rotation migration. Edit-form persistence, ixVolume uninstall
semantics, a valid prior-state upgrade, and rollback remain required before VB-082 completion.
VB-023 retrieval benchmarking is complete. VB-032 and VB-033 remain explicitly deferred/optional,
and VB-055 remains optional rather than a dashboard
prerequisite. Milestone 9 package definition, official Docker-backed validation, upstream acceptance,
and initial real-catalog installation are complete. Milestone 9 remains open because VB-082 still has
unresolved post-merge lifecycle gates. ADR 0004 completes VB-090's MCP design-only
work, PR #55 CI completes VB-091's read-only stdio implementation verification, VB-092 adds the
opt-in read-only HTTP transport, and PR #62 CI completes VB-093's production-image container gate.
An isolated source-built TrueNAS smoke also passes with synthetic data while the production app
remains healthy. The favicon-and-screenshot-only `v1.2.1` patch remains historical release evidence.
`v1.3.0` is now the published stable GitHub/GHCR release from source commit
`a7e14ece0de74632d1d9be599d53678931dc64b3`; exact image tag `1.3.0`, aliases `1.3`, `1`, and
`latest`, and OCI digest
`sha256:5a1709c279c3731f891b59026adb7e8f5497c299687596b74b49ffd64a9f5a0e` are published. This does not
claim that the upstream TrueNAS Community App already contains the new MCP settings. Upstream
package/image updates remain separate, and VB-082's lifecycle gates stay open.

Milestone 11 is complete as the PKM relationship foundation. VB-100 implements the reusable
read-only Obsidian-compatible wikilink parser/resolver,
VB-101 adds verified outgoing relationships from one live contained note, and VB-102 derives
verified backlinks with a measured live scan, VB-103 exposes both capabilities through
versioned-only REST routes and the shared MCP server, and VB-104 adds their bounded read-only view to
the selected-note dashboard workflow. VB-105 completes the evaluation track without recommending a
production relationship-ranking change because measured live-scan cost and narrow cases do not
establish acceptable general benefit. Wikilinks remain the first supported relationship dialect,
not the architectural definition.

The strategic continuation is Milestone 12. VB-110 and ADR 0005 are accepted design work. VB-111 is
the next roadmap item to scope, but it is not implementation-ready until a separate authoritative
`BACKLOG.md` contract is accepted. VB-032/VB-033 remain deferred, and VB-034 remains a later optional
write task rather than NEXT. VB-111–VB-114, VB-120–VB-122, VB-130–VB-132, VB-140–VB-142, and
VB-150–VB-152 remain roadmap-planned identifiers and reserve no implementation scope by themselves.

---

# Codex execution rules

The roadmap describes direction. **`BACKLOG.md` is authoritative for individual task scope and acceptance criteria.**

For every implementation task Codex should:

1. Read `AGENTS.md`.
2. Read `PROJECT_STATE.md`.
3. Read `ARCHITECTURE.md`.
4. Read `ROADMAP.md`.
5. Read the exact task in `BACKLOG.md`.
6. Inspect current code/tests before proposing changes.
7. Implement one backlog task only.
8. Run the required checks.
9. Update state/architecture docs only when the task changes those facts.
10. Stop before implementing the next backlog item.

After every merged task:

- update task status in `BACKLOG.md`,
- update `PROJECT_STATE.md`,
- update `ARCHITECTURE.md` only if architecture changed,
- update `ROADMAP.md` only when milestone/current-status information changed,
- update `CHANGELOG.md` when appropriate.
