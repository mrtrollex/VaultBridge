# Current Project State

This document is the current factual snapshot for future Codex sessions. It should describe what exists **now**, not future plans.

## Baseline date

2026-08-23

## Current development position

Completed:

- VB-001 — Project identity
- VB-002 — Typed configuration
- VB-004 — VaultService
- VB-003 — FastAPI routers
- VB-005 — Semantic service/repository split
- VB-010 — Semantic index state model
- VB-011 — Batch index commits

Next recommended task:

- **VB-012 — Background startup indexing**

Current milestone:

- **Milestone 2 — Index lifecycle and non-blocking synchronization**

## Working production characteristics

- FastAPI application
- Python 3.12 container
- FastAPI routes, vault operations, semantic orchestration and SQLite persistence have separate modules
- typed runtime settings via `app/core/config.py`
- local semantic model through FastEmbed / ONNX Runtime
- default model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- SQLite semantic index using WAL
- chunk configuration currently defaults to 600 chars / 100 overlap
- semantic search uses cosine similarity plus lexical/title/path reranking
- semantic index lifecycle state is persisted separately from schema availability
- semantic index states: `uninitialized`, `indexing`, `ready`, `error`
- semantic index mutations commit in configurable note-count batches (default `25`)
- `SemanticSearchService` owns state transitions
- `SemanticRepository` persists semantic data and lifecycle state
- TrueNAS container commonly runs as UID/GID 568
- existing production deployment uses port 8765 → 8000
- separate `/vault` and `/data` mounts are used by the TrueNAS compose file

## Current implementation boundaries

```text
app/main.py
    application construction, dependency wiring and router registration

app/api/
    HTTP routers and API dependencies

app/core/config.py
    typed runtime configuration

app/services/vault.py
    safe vault-relative path resolution and Markdown operations

app/services/semantic_search.py
    embedding, batched synchronization orchestration, ranking and lifecycle transitions

app/repositories/semantic.py
    SQLite semantic persistence and lifecycle-state storage

app/semantic.py
    compatibility facade retained for pre-VB-005 internal API compatibility
```

## Semantic index lifecycle

Persisted states:

```text
uninitialized
indexing
ready
error
```

Deterministic behavior:

- missing database/schema → `uninitialized`
- synchronization starts → `indexing`
- successful synchronization, including empty vault → `ready`
- synchronization failure → `error`
- later successful retry → `ready`
- compatible legacy index with chunks → inferred/persisted `ready`
- compatible legacy index without chunks → `uninitialized`
- signature mismatch → existing invalidation, then `uninitialized`
- persisted `indexing` after restart → converted to `error`
- each completed synchronization batch is durable; interruption rolls back at most the active batch
- retry reuses already committed batches through incremental change detection

`semantic_index_ready` is currently derived from `state == ready`.

## Known limitations observed during real use

1. First semantic indexing can take a long time and appears to block the request.
2. Indexing progress is not visible through the health endpoint.
3. Search currently calls synchronization inline.
4. Background startup indexing does not yet exist.
5. Note writes do not yet enqueue targeted background reindex work.
6. Current chunking is still primarily character-based rather than fully Markdown-aware.
7. Default ranking thresholds require evaluation rather than ad-hoc tuning.
8. Multiple application processes sharing one index are not coordinated.
9. GPT/AI clients can invent wikilinks unless client instructions require verified existing notes.

## Verified baseline after VB-011

Linux compatibility run (WSL):

```text
52 passed
```

Additional checks:

```text
Ruff: passed
Python compileall: passed
git diff --check: passed
all 7 endpoint paths and operation IDs: unchanged
```

Focused semantic/config/API tests at VB-011 completion:

```text
41 passed
```

Native Windows: `50 passed, 1 failed, 1 skipped`; the failure remains the known pre-existing
path-separator assertion around VaultService paths, and the skip is the privilege-dependent symlink
test. The Linux compatibility run is green. Docker checks were unavailable because the Docker CLI
was not installed in the verification environment.

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
