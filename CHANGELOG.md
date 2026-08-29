# Changelog

All notable changes to VaultBridge will be documented here.

The project intends to follow Semantic Versioning after the public repository baseline is established.

## [Unreleased]

### Fixed

- Docker clean installations now give FastEmbed/Hugging Face a predictable writable `HF_HOME`
  inside the existing derived semantic-data mount, preserving generic `PUID:PGID` and TrueNAS
  `568:568` non-root execution

### Added

- accepted ADR 0003 for the planned Web Dashboard, defining canonical same-origin `/ui/` routing,
  operator-supplied Bearer-key handling in `sessionStorage`, strict CSP and text-safe rendering,
  reuse of current health/search contracts, and no note/index mutation; no dashboard runtime is
  implemented by this documentation change
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

### Changed

- post-v1 documentation now plans a platform-neutral bundled Web Dashboard followed by a TrueNAS
  Community App that consumes the normal published VaultBridge image; neither capability is
  implemented or released by this documentation change
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
