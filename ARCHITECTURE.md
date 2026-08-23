# VaultBridge Architecture

## Context

VaultBridge exposes a deliberately small API over an Obsidian Markdown vault. It supports ordinary note operations and local semantic retrieval without turning the vault into a proprietary database.

## Current architecture

```text
Client (ChatGPT / curl / future integrations)
                    |
                    | HTTPS + Bearer token
                    v
               FastAPI app
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

### Current implementation modules

```text
app/main.py          HTTP API, models, auth, route wiring
app/core/config.py   typed environment configuration
app/services/vault.py safe path resolution and Markdown note operations
app/semantic.py      chunking, embeddings, SQLite index, hybrid ranking
```

This is acceptable for a prototype but is the main maintainability limitation.

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

Bearer token verification and future key rotation logic.

### `services/vault.py`

- safe path resolution
- Markdown read/write/append
- note enumeration
- content size validation

### `services/indexer.py`

- background synchronization lifecycle
- batching and progress
- file-change queue
- index state

### `services/semantic_search.py`

- query embedding
- candidate scoring
- hybrid reranking
- result aggregation

### `storage/semantic_repository.py`

All SQLite persistence for semantic metadata/chunks/index state.

### `schemas/`

Pydantic HTTP request/response models only.

### `api/`

FastAPI routers and dependencies. Routes should orchestrate services rather than implement domain logic.

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
- state/progress
- last completed synchronization

The index is **derived data**. Migrations should be used when cheap; otherwise a safe automatic rebuild is acceptable.

---

## Request flows

### Literal search

```text
request → auth → validate folder → scan/search Markdown → response
```

A future optimization may add a lexical index, but only if measured vault sizes justify it.

### Semantic search — target

```text
request
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
response

Background index maintenance happens independently.
```

### Note write

```text
request → auth → safe path → validate size → write Markdown → enqueue note for re-index
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
