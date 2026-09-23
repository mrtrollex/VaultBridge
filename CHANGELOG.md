# Changelog

All notable changes to VaultBridge will be documented here.

The project intends to follow Semantic Versioning after the public repository baseline is established.

## [Unreleased]

### Added

- default-off MCP `create_note` and `append_note` tools with REST-equivalent validation and write
  semantics, targeted semantic refresh after committed changes, and safe stdio/HTTP indexer ownership
- first-class TrueNAS package fields for MCP HTTP, MCP writes, and explicit Host/Origin allowlists,
  mapped to the existing application port without requiring normal Additional Environment Variables

- a reusable read-only Obsidian wikilink parser/resolver with fenced-code exclusion, exact
  ambiguity-safe matching, and canonical target verification through the existing vault boundary
- a read-only outgoing-relationship service that verifies and reads one source note through the
  vault boundary, preserves wikilink occurrences in source order, and distinguishes resolved
  canonical targets from unresolved links without graph persistence
- a read-only verified-backlink service that scans canonical live Markdown sources using one
  reusable resolution snapshot, returns deterministic deduplicated relationship metadata, and
  includes a synthetic 1,000-note benchmark without a CI latency gate or persistent graph index
- protected versioned-only REST outgoing-link and backlink endpoints plus matching `note_links` and
  `note_backlinks` MCP tools, all backed by the same read-only relationship domain service
- a bounded read-only dashboard Relationships section for a successfully read note, with independent
  outgoing/backlink loading, empty, failure, logout, and stale-response handling through the existing
  authenticated session boundary and text-only rendering
- a separate deterministic and real-model graph-retrieval evaluation over sanitized fixtures; it
  records a verified one-hop quality signal and live-scan cost without changing the accepted
  retrieval baseline or production behavior, and concludes that production graph ranking is not
  supported by the current evidence

## [1.2.1] - 2026-09-20

### Added

- a branded dashboard favicon that reuses the existing bundled VaultBridge logo
- sanitized real-application dashboard screenshots prepared for a future TrueNAS catalog metadata
  update without changing the published `v1.2.0` release or accepted catalog package

## [1.2.0] - 2026-09-20

### Added

- a read-only MCP stdio adapter using the official Python SDK, exposing exactly `list_notes`,
  `read_note`, `search_notes`, `related_notes`, and `duplicate_candidates` plus contained Markdown
  note Resources, without starting FastAPI, an index writer, or another service
- an opt-in, disabled-by-default Streamable HTTP endpoint at exact `/mcp` on the existing FastAPI
  process and port, reusing the live vault, semantic, duplicate-candidate, indexer, and lifecycle
  owners instead of creating a second runtime
- Bearer authentication for network MCP using the existing current and optional previous API keys,
  the shared direct-peer rate limit, and official SDK Host and present-Origin validation
- a repeatable real-model retrieval benchmark command that emits Markdown and JSON reports without
  changing the deterministic evaluation baseline or production retrieval behavior
- accepted TrueNAS Community App packaging for the published `1.1.0` image; the catalog package has
  not yet been updated to a `1.2.0` image

### Changed

- semantic embedding defaults now use a measured batch size of `4` and expose typed batch-size and
  ONNX CPU memory-arena controls, reducing indexing memory pressure without changing the model,
  pooling, chunking, ranking, or stored-vector contract
- semantic-index compatibility now fingerprints the effective ONNX graph, tokenizer/configuration
  artifacts, and application-owned pooling/vector contract; incompatible legacy indexes rebuild
  automatically from Markdown without a SQLite schema migration
- full semantic synchronization now processes eligible Markdown files in deterministic path order,
  preserving index contents and retrieval behavior while making rebuilds repeatable

### Validation

- GitHub Actions now exercises the built production image with MCP HTTP disabled and enabled,
  including the official MCP client, the exact five-tool read-only surface, synthetic-note access,
  REST compatibility, current/previous credentials, invalid Bearer/Host/Origin rejection, and clean
  shutdown
- the dashboard now has an isolated Chromium Playwright regression suite covering public Overview,
  protected unlock, literal search and note reading, same-tab session restore, logout data clearing,
  and unexpected browser/network failures without using a real vault or embedding model
- repository-local task, verification, finish, and fresh-review tooling now selects affected-surface
  checks and produces bounded implementation/review packets without invoking Codex or publishing
  artifacts
- a disposable source-built image from main commit `8ae99d3` passed an isolated TrueNAS runtime
  smoke: liveness, authenticated REST listing, official Streamable HTTP protocol `2026-07-28`, the
  exact five read-only tools, and `list_notes` against synthetic `Smoke.md`; the production app on
  port `30491` remained healthy and all disposable smoke resources were removed afterward

## [1.1.0] - 2026-09-02

### Fixed

- dashboard hardening improves restored-session focus behavior, API-key error association, input
  instructions, explicit semantic score labels, stale Overview request suppression, narrow/mobile
  navigation reflow, long-content wrapping, focus/contrast regression coverage, and explicit
  reduced-motion handling without adding dashboard features or changing API behavior
- Docker clean installations now give FastEmbed/Hugging Face a predictable writable `HF_HOME`
  inside the existing derived semantic-data mount, preserving generic `PUID:PGID` and TrueNAS
  `568:568` non-root execution

### Added

- completed dashboard release-readiness evidence covering automated regressions, a real Chrome
  usability/accessibility/privacy audit, and a disposable TrueNAS normal-image gate for bundled
  assets, API/CLI compatibility, authenticated retrieval, semantic startup, safe logs, restart
  persistence, clean stop, and cleanup; no release version or published image is claimed
- a protected dashboard Search area with accessible Literal and Semantic modes backed directly by
  the existing `/api/v1/notes/search` and `/api/v1/notes/related` contracts; server-order result
  cards, semantic score visibility, null-safe text rendering, in-memory-only queries/results,
  abort/stale-response control, and session-safe `401`/`429`/`503`/validation/network states without
  backend ranking, API, dependency, note/index mutation, or persistence changes
- a public `/health`-backed dashboard Overview with compact application, vault, semantic-index, and
  background-indexer cards; deterministic Ready/Indexing/Degraded/Unavailable presentation;
  locale-safe counts and timestamps; manual refresh; and explicit loading, malformed, and unavailable
  states without polling, authentication coupling, API changes, or maintenance controls
- a bundled, schema-hidden `/ui/` Web Dashboard shell with canonical redirects, explicit local
  assets, responsive Overview/Search/API/About navigation, authenticated `sessionStorage` lifecycle,
  centralized Bearer requests and safe status handling, strict CSP/security headers, and no
  note/index mutation or new frontend/runtime dependency
- a complete safe read-only note reader that opens literal or semantic results through the existing
  protected `/api/v1/notes/read` contract, renders Markdown source as text, suppresses stale requests,
  restores focus to results, and clears protected content on logout or authentication failure
- accepted ADR 0003 for the planned Web Dashboard, defining canonical same-origin `/ui/` routing,
  operator-supplied Bearer-key handling in `sessionStorage`, strict CSP and text-safe rendering,
  reuse of current health/search contracts, and no note/index mutation
- an optional, disabled-by-default cross-platform filesystem watcher using `watchdog`, with safe
  recursive Markdown event interpretation, monotonic debounce/coalescing, and lifecycle integration
  through the existing single semantic indexer
- a dependency-free local CLI with persisted `status`, offline incremental `index`, clean `reindex`,
  literal `search`, and existing-index semantic `related` commands, while retaining VB-045
  `index check` and `index rebuild`
- protected legacy and `/api/v1` advisory duplicate-candidate routes combining conservative live
  title equivalence with one existing semantic search, VB-031 live-path verification, deterministic
  exact-first merging, and no note or semantic-index writes
- an operator-controlled API-key rotation window using required current `API_KEY` plus one optional
  secret-safe `API_KEY_PREVIOUS`, shared by legacy and `/api/v1` protected routes with constant-time
  Bearer comparison and no authentication-contract or health-route changes
- a dependency-free, process-local fixed-window limiter for protected legacy and `/api/v1` traffic,
  keyed only by the direct ASGI peer, with monotonic timing, bounded client state, deterministic
  cleanup/eviction, `429` plus `Retry-After`, and public health/privacy exemptions
- an accepted version-neutral TrueNAS Community App design plus a release-neutral development
  definition with metadata, questions, Compose template, Web Portal, and focused static render
  fixtures; the production image pin, live TrueNAS validation, upstream submission, and Discover
  availability remain explicitly unclaimed

### Changed

- post-v1 documentation now records the platform-neutral dashboard shell, session, Overview, and
  protected Literal/Semantic Search as implemented, together with final operator-console polish,
  streamlined protected access, and complete note reading, while retaining image publication and
  immutable-image verification as separate release work
- targeted synchronization now removes derived rows for safely contained missing Markdown paths, so
  external deletes and both sides of renames are processed without a full vault scan
- related-note suggestions now return only live, contained regular Markdown paths verified through
  `VaultService`, canonicalize safe internal aliases, and boundedly backfill filtered stale candidates
  while preserving response fields, surviving order, caller limits, and semantic-index contents

## [1.0.0] - 2026-08-26

### Added

- project roadmap and Codex development playbook
- initial repository governance/security documentation
- CI preparation
- compatibility-preserving rich `/health` output for vault, semantic lifecycle, search availability, background activity, recovery debt, coherent eligible-note/index counts, and last successful full synchronization
- deterministic retrieval evaluation with a sanitized Markdown corpus, EN/SK/cross-language
  relevance cases, Hit@1/Hit@3/MRR metrics, and production-pipeline failure diagnostics
- safe standard-library JSON application logging for container lifecycle, semantic synchronization,
  targeted reindexing, and committed note-write events
- context-local request correlation, `X-Request-ID` response headers, and monotonic HTTP lifecycle
  latency events without request-body, query-string, header, or credential logging
- public, minimal `/health/live` and `/health/ready` orchestration probes with dependency-free
  liveness and side-effect-free vault plus semantic-search readiness
- a standard-library semantic-index administration CLI with filesystem-immutable stopped-service
  persisted inspection, stable exit codes, and explicit offline rebuild through the production
  batched synchronization pipeline with atomic successful-sync finalization
- a stable `/api/v1` namespace for every protected note/search operation with explicit `*V1`
  operation IDs, shared legacy/v1 endpoint registration, and contract-matrix coverage
- a generic Docker Compose guide covering clean installation, host vault and UID/GID mapping,
  first-start readiness, authenticated `/api/v1` verification, logs, updates, stopped-service index
  maintenance, loopback-only networking, and security without relying on TrueNAS-specific paths
- a TrueNAS SCALE operational runbook covering the existing Custom App YAML include, legacy
  production identifiers, dataset ACL principles, fresh and bundle/no-Git installation, managed
  source/image updates, health and log verification, rollback, and offline semantic-index recovery
- a release-gated GHCR publication workflow for the existing production Dockerfile, with
  semantic-version tags, stable-only aliases and `latest`, OCI metadata, minimal package permission,
  SHA-pinned actions, minimal BuildKit provenance, repeated release-source validation, and
  published-manifest inspection
- an evidence-backed release checklist with explicit v1.0 acceptance status, isolated clean-install
  validation, supported-platform and artifact-version contracts, RC/stable GHCR procedures, draft
  release notes, and exact unresolved release blockers

### Changed

- public-exposure remediation now excludes generated distribution bundles, documents the current
  private vulnerability-reporting limitation, and uses the configured GitHub noreply identity in
  reachable commit metadata without changing runtime behavior
- existing package and FastAPI application metadata now target `1.0.0`; Git tags/GitHub Releases and
  GHCR digests remain the authoritative release and immutable deployment identities
- the create/read/search/append API regression now verifies the exact returned note path with a
  platform-neutral `Path` comparison instead of assuming POSIX separators on native Windows
- Markdown discovery for literal search, note listing, and full semantic synchronization now
  validates each resolved target against the resolved vault root before stat/read, skips external
  and broken symlinks, and canonicalizes/deduplicates safe internal file aliases
- semantic index synchronization now commits durable, configurable note batches so interruption only rolls back the active batch
- application startup now runs semantic-index synchronization in one managed background worker; search no longer performs synchronization inline
- failed initial indexing now returns semantic-search HTTP 503, while compatible indexes remain searchable after failed refreshes
- background shutdown now requests cooperative cancellation between batches, and embedder execution is serialized per service instance
- successful note creates/appends now enqueue coalesced, targeted semantic refreshes on the existing background worker
- targeted refreshes preserve batch durability, prior committed search data on failure, and full-sync concurrency ownership
- Markdown indexing now prefers ATX heading boundaries, stores heading hierarchy, bounds oversized
  sections deterministically, and automatically rebuilds indexes created by the previous chunker
- bounded chunk splits now preserve exact Markdown source characters, coalesce adjacent tiny
  sections, and retain leaf headings when hierarchy metadata reaches its length limit
- committed writes now remain successful across enqueue/submission errors; unresolved full-sync debt forces a full retry before targeted recovery
- targeted missing, unreadable, invalid-UTF-8, excluded, oversized, and escaping paths now fail and remain retryable instead of being silently skipped
- semantic chunk embeddings now include canonical heading hierarchy context when it is not already
  present at the chunk start, while persisted Markdown content and query/ranking behavior stay unchanged
- the `v3-heading-context` index signature automatically rebuilds VB-020 embeddings; targeted refresh
  against an older signature performs a safe full rebuild without a SQLite schema migration
- hybrid retrieval now normalizes the existing `1.0:0.70` semantic/lexical combination instead of
  clamping distinct high scores, and resolves exact ties through semantic score, lexical score,
  canonical path and source chunk order without depending on SQLite iteration order
- new integrations now prefer `/api/v1`; existing unversioned paths and operation IDs remain
  compatibility aliases, while operational health endpoints stay public and unversioned
- Docker build contexts now exclude local environments, vault/index/model data, caches, tests,
  documentation, repository automation, and generated artifacts from build-context transfer
- public repository availability and the generic Docker clean-install path were verified from an
  anonymous clone on TrueNAS SCALE / Linux amd64 with a disposable vault; source build, container
  startup, liveness, readiness, and authenticated `/api/v1` note listing all passed
