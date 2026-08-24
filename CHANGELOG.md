# Changelog

All notable changes to VaultBridge will be documented here.

The project intends to follow Semantic Versioning after the public repository baseline is established.

## [Unreleased]

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

### Changed

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

## [0.1.0] - TBD

Planned first public baseline based on the currently working Obsidian bridge with local semantic search.
