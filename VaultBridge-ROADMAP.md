# VaultBridge Roadmap

> **Historical planning snapshot:** this file preserves the original pre-v1 roadmap. It is not the
> current planning source. See [`ROADMAP.md`](ROADMAP.md), [`BACKLOG.md`](BACKLOG.md), and
> [`PROJECT_STATE.md`](PROJECT_STATE.md); the current next recommended task is VB-071.

> **Project goal:** turn the current working Obsidian ↔ AI bridge into a small, reliable, client-agnostic, self-hosted application that can be shipped publicly on GitHub and deployed with Docker.

## Product principles

1. **Markdown is the source of truth.** VaultBridge must never require importing the vault into a proprietary store.
2. **Minimal API surface.** Expose only the operations that are actually needed.
3. **Local-first semantic search.** Embeddings stay local by default; no external embedding API is required.
4. **Safe by default.** No arbitrary filesystem access, no delete endpoint, no secret logging, no unauthenticated write operations.
5. **Simple operations.** Docker + SQLite should remain enough for a normal personal vault.
6. **Client agnostic.** ChatGPT is one client, not the architecture. Claude, scripts, automations, and custom frontends should be able to use the same API.
7. **No premature infrastructure.** Do not add Redis, Celery, Qdrant, Kubernetes, or a message broker until a measured requirement justifies them.

---

## Current baseline — `v0.1`

The current implementation already provides:

- FastAPI service
- Bearer API key authentication
- create/read/append/list operations
- literal text search
- local semantic search using FastEmbed + ONNX Runtime
- multilingual MiniLM embeddings
- incremental SQLite semantic index
- hybrid semantic + lexical reranking
- path traversal protection
- Docker and TrueNAS deployment files
- 6 passing API tests
- Custom GPT Action schema and instructions

The goal of the roadmap is **not to rewrite this from scratch**. Each phase should preserve working behaviour while improving structure and operability.

---

# Phase 0 — Repository baseline and project identity

**Target:** make the existing codebase safe to evolve as `VaultBridge`.

### Tasks

- [ ] Rename user-facing project identity from `Obsidian ChatGPT Bridge` to `VaultBridge`.
- [ ] Keep compatibility with the existing Docker/TrueNAS deployment until a documented migration step exists.
- [ ] Adopt semantic versioning starting at `0.1.0` for the public project.
- [ ] Add `pyproject.toml` for test/lint configuration.
- [ ] Add CI for tests, compile checks, and Docker build.
- [ ] Add `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, and `CHANGELOG.md`.
- [ ] Ensure `.env`, model cache, SQLite indexes, and vault content can never be committed accidentally.
- [ ] Document the current API contract before refactoring.

### Definition of done

- `pytest` passes.
- Docker image builds.
- No runtime behaviour changes.
- README clearly explains what VaultBridge is and what it is not.

---

# Phase 1 — Application architecture refactor

**Target:** turn the current small prototype into a maintainable application without changing the public API.

### Target structure

```text
app/
├── main.py
├── api/
│   ├── dependencies.py
│   ├── health.py
│   ├── notes.py
│   └── search.py
├── core/
│   ├── config.py
│   ├── logging.py
│   └── security.py
├── schemas/
│   ├── notes.py
│   └── search.py
├── services/
│   ├── vault.py
│   ├── semantic_search.py
│   └── indexer.py
└── storage/
    └── semantic_repository.py
```

### Tasks

- [ ] Move environment parsing into a typed `Settings` object.
- [ ] Move Bearer authentication into `core/security.py`.
- [ ] Move path validation and Markdown I/O into a `VaultService`.
- [ ] Split HTTP routes into FastAPI routers.
- [ ] Move semantic index persistence behind a repository/service boundary.
- [ ] Move Pydantic request/response models out of `main.py`.
- [ ] Add unit tests for services in addition to API tests.
- [ ] Preserve all current endpoint paths and operation IDs.

### Definition of done

- `main.py` is primarily application wiring.
- No circular dependencies.
- Existing API tests pass unchanged.
- Semantic index can be tested without starting FastAPI.

---

# Phase 2 — Background indexing and index lifecycle

**Target:** semantic search should never feel like a request is frozen because the whole vault is being indexed synchronously.

### Tasks

- [ ] Add an application-managed background indexer.
- [ ] On startup, compare vault state with the SQLite index.
- [ ] Index notes in configurable batches.
- [ ] Commit progress after each batch.
- [ ] Persist enough state to resume after interruption.
- [ ] Add index state: `idle`, `indexing`, `ready`, `error`.
- [ ] Add progress counters: total notes, indexed notes, chunks, last update.
- [ ] Make semantic search usable while a background refresh is running when a previous valid index exists.
- [ ] Add an optional filesystem watcher for changed Markdown files.
- [ ] Debounce bursts of file changes from Obsidian sync operations.

### Proposed health output

```json
{
  "status": "ok",
  "vault": {"available": true, "notes": 842},
  "semantic_index": {
    "state": "ready",
    "indexed_notes": 842,
    "chunks": 5317,
    "last_updated_at": "2026-08-23T08:00:00Z"
  }
}
```

### Definition of done

- First-time indexing reports progress.
- Restarting during indexing does not require starting from zero.
- A normal search request no longer performs a full vault synchronization inline.

---

# Phase 3 — Retrieval quality

**Target:** improve relevance before considering a larger embedding model or vector database.

### Tasks

- [ ] Replace character-first chunking with Markdown-aware section chunking.
- [ ] Preserve heading hierarchy (`H1 > H2 > H3`) as chunk metadata.
- [ ] Embed `title + heading path + content`.
- [ ] Keep code fences and lists intact where practical.
- [ ] Make chunk size configurable by approximate tokens or characters.
- [ ] Separate `semantic_score`, `lexical_score`, and final `score` in the API contract.
- [ ] Calibrate default thresholds using a small evaluation dataset.
- [ ] Add title/path exact-match boosts only when justified by query terms.
- [ ] Add retrieval tests for Slovak, English, and cross-language queries.
- [ ] Add a benchmark script that records latency and top-k quality.

### Evaluation dataset

Create `tests/eval/retrieval_cases.yaml` with real but sanitized query → expected-note pairs.

Example:

```yaml
- query: "professional courses and certifications"
  expected_paths:
    - "Vzdelavanie/Learning COURSES.md"
```

### Definition of done

- Retrieval changes are measured against repeatable test cases.
- Quality regressions can be detected in CI.
- No model upgrade is accepted solely because it is larger.

---

# Phase 4 — Knowledge operations

**Target:** make VaultBridge useful for maintaining a knowledge base, not just searching it.

### 4.1 Duplicate detection

- [ ] Add `findPossibleDuplicates` or an internal duplicate-check service.
- [ ] Compare title similarity + semantic similarity + folder context.
- [ ] Return candidates; do not silently merge notes.

### 4.2 Verified related notes

- [ ] Add `suggestRelatedNotes` returning only notes that actually exist.
- [ ] Include path, title, score, and best matching heading/snippet.
- [ ] Never invent wikilink targets.

### 4.3 Safe note update

- [ ] Design `updateNoteSection` or `upsertNote` with explicit semantics.
- [ ] Prefer section-level changes over arbitrary full-file replacement.
- [ ] Add optimistic concurrency (`content_hash` or `updated_at`) before destructive rewrites.
- [ ] Keep append idempotency.

### 4.4 Backlinks

- [ ] Support optional verified backlink suggestions.
- [ ] Make automatic insertion opt-in.
- [ ] Never create more than a small configurable number of links automatically.

### Definition of done

- AI clients can avoid duplicate notes without being allowed to arbitrarily rewrite the vault.
- Every generated wikilink can be traced to an existing note returned by VaultBridge.

---

# Phase 5 — Operational maturity

**Target:** make the service predictable to run continuously on a NAS/server.

### Tasks

- [ ] Structured JSON request logs.
- [ ] Request/correlation IDs.
- [ ] Log latency, endpoint, status code, result count, and index state.
- [ ] Never log API keys or full note content by default.
- [ ] Add `/health/live` and `/health/ready`.
- [ ] Add configurable lightweight rate limiting.
- [ ] Support current + previous API key for zero-downtime key rotation.
- [ ] Add graceful shutdown for index jobs.
- [ ] Add database integrity check and rebuild command.
- [ ] Add optional metrics endpoint later if there is a real consumer for it.

### Definition of done

- Common failures can be diagnosed without attaching a debugger.
- API key rotation does not require downtime.
- Index corruption can be repaired from the Markdown source of truth.

---

# Phase 6 — Public API and developer experience

**Target:** make the project pleasant for someone other than its original author.

### Tasks

- [ ] Introduce `/api/v1/...` routes.
- [ ] Keep old routes temporarily or provide a migration note.
- [ ] Serve OpenAPI docs optionally (`/docs` disabled by default for public deployment if desired).
- [ ] Add a CLI:

```text
vaultbridge status
vaultbridge index
vaultbridge reindex
vaultbridge search "Oracle APEX"
vaultbridge related "professional education"
```

- [ ] Add Docker Compose quick start.
- [ ] Add TrueNAS guide.
- [ ] Add generic Linux Docker guide.
- [ ] Add optional Unraid guide if tested.
- [ ] Add ChatGPT Action as an integration example, not as the core product identity.
- [ ] Add generic curl/API examples.
- [ ] Publish architecture diagram.

### Definition of done

A new user can clone the repository, create `.env`, mount a vault, run Docker Compose, and execute a semantic search without reading source code.

---

# Phase 7 — Distribution and `v1.0.0`

**Target:** ship VaultBridge as a real open-source project.

### Tasks

- [ ] Build multi-architecture Docker images when feasible (`amd64`, `arm64`).
- [ ] Publish images to GitHub Container Registry.
- [ ] Add GitHub release workflow.
- [ ] Pin/review critical dependency versions.
- [ ] Generate SBOM/container provenance if practical.
- [ ] Run dependency/security scanning in CI.
- [ ] Complete threat model review.
- [ ] Record a clean install from scratch.
- [ ] Create screenshots/demo GIF or short video.
- [ ] Finalize README and release notes.

### `v1.0.0` acceptance criteria

- clean install succeeds from public documentation
- CI green
- Docker image published
- API versioned
- index lifecycle does not block requests
- retrieval evaluation suite exists
- no known path traversal/auth bypass issue
- secrets are not committed or logged
- documented upgrade/rebuild procedure

---

# Post-1.0 ideas

These are intentionally **not** required for the first public release:

- pluggable embedding providers/models
- SQLite vector extension / HNSW acceleration
- Qdrant adapter for very large vaults
- read-only mode
- per-folder access policies
- multiple vaults
- bundled web dashboard (now promoted to planned Milestone 8 in the current `ROADMAP.md`)
- MCP server adapter
- webhook/event integrations
- note metadata/frontmatter query language
- graph-aware ranking using Obsidian links

---

# Explicit non-goals for now

Do **not** implement these unless requirements change:

- arbitrary filesystem API
- remote shell execution
- automatic note deletion
- mandatory cloud services
- mandatory external embedding APIs
- Kubernetes deployment
- full Obsidian sync replacement
- a general-purpose vector database by default

---

# Recommended implementation order

```text
Phase 0  Repository baseline
   ↓
Phase 1  Refactor without behaviour changes
   ↓
Phase 2  Background indexing
   ↓
Phase 3  Retrieval quality + evaluation
   ↓
Phase 4  Duplicate/update/backlink operations
   ↓
Phase 5  Logging, health, rate limiting, key rotation
   ↓
Phase 6  Versioned API + CLI + docs
   ↓
Phase 7  GHCR + public v1.0 release
```

Do not start multiple phases at once. Each Codex task should be a reviewable change with tests and a clear acceptance criterion.
