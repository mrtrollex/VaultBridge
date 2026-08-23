# VaultBridge Roadmap

> **Project goal:** turn VaultBridge into a small, reliable, client-agnostic, self-hosted application for safe Obsidian note operations and local semantic retrieval, suitable for public GitHub distribution and Docker deployment.

## Product principles

1. **Markdown is the source of truth.** VaultBridge must never require importing the vault into a proprietary store.
2. **Minimal API surface.** Expose only operations that are actually required.
3. **Local-first semantic search.** Embeddings stay local by default; no external embedding API is required.
4. **Safe by default.** No arbitrary filesystem access, no delete endpoint, no secret logging, and no unauthenticated writes.
5. **Simple operations.** Docker + SQLite should remain enough for a normal personal vault.
6. **Client agnostic.** ChatGPT is one client, not the architecture.
7. **No premature infrastructure.** Do not add Redis, Celery, Qdrant, Kubernetes, or a message broker without measured need and an ADR.
8. **Measured retrieval changes.** Ranking/model/chunking changes must be evaluated, not tuned only by intuition.
9. **Derived semantic data.** The semantic index must always be rebuildable from the Markdown vault.

---

# Current project state — `v0.1.x`

VaultBridge has moved beyond the original prototype and now has explicit API, service, repository, and configuration boundaries.

## Current architecture

```text
Client
(ChatGPT / curl / future CLI / integrations)
                     |
                     | HTTPS + Bearer token
                     v
                FastAPI app
                     |
                API routers
          +----------+-----------+
          |                      |
          v                      v
     VaultService       SemanticSearchService
          |                      |
          v                      v
  Obsidian Markdown      SemanticRepository
                                 |
                                 v
                         SQLite semantic index
                                 |
                                 v
                    FastEmbed / ONNX Runtime
```

## Current implementation

```text
app/main.py                     application construction and dependency wiring
app/api/                        health, note and search routers
app/core/config.py              typed runtime configuration
app/services/vault.py           safe Markdown/vault operations
app/services/semantic_search.py semantic orchestration, embeddings, ranking, indexing
app/repositories/semantic.py    SQLite semantic persistence
app/semantic.py                 legacy compatibility facade
```

## Completed foundation tasks

- [x] **VB-001 — Project identity**
- [x] **VB-002 — Typed configuration**
- [x] **VB-004 — VaultService**
- [x] **VB-003 — FastAPI routers**
- [x] **VB-005 — Semantic service/repository split**
- [x] **VB-010 — Semantic index state model**

## Current verified baseline

At completion of VB-010:

```text
48 tests passed on the Linux deployment target
Ruff passed
compileall passed
git diff --check passed
all existing endpoint paths and operationIds verified unchanged
```

## Current known limitations

1. Initial/full semantic synchronization still runs synchronously.
2. A long first index can make a request appear frozen.
3. One large synchronization can still cover too much work before durable progress is committed.
4. Index progress counters are not yet exposed.
5. Search still performs synchronization inline.
6. Background startup synchronization does not yet exist.
7. File changes are not automatically queued outside request-driven synchronization.
8. Chunking is still primarily character-based rather than Markdown-structure-aware.
9. Retrieval thresholds/ranking weights need repeatable evaluation.
10. Multiple VaultBridge processes sharing one semantic index are not coordinated.
11. AI clients can invent wikilinks unless they use verified note results.

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

# Milestone 2 — Index lifecycle and non-blocking synchronization ← CURRENT

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

## Next

### VB-011 — Batch index commits — P0 ← NEXT

**Goal:** avoid one large transaction covering the entire initial/full synchronization.

Requirements:

- configurable batch size,
- durable progress after each batch,
- preserve incremental indexing behavior,
- preserve ranking/chunking/model behavior,
- interruption loses at most the active batch,
- no background worker yet.

### VB-012 — Background startup indexing — P0

**Depends on:** VB-010, VB-011

Goal:

```text
VaultBridge starts
      |
      +--> API becomes available
      |
      +--> background index synchronization
                    |
                    v
                  ready
```

No Redis/Celery/task queue.

### VB-013 — Enqueue reindex after note writes — P0

Writes should enqueue only affected notes for semantic refresh.

### VB-015 — Rich health/readiness output — P0

Expose useful lifecycle/progress information without requiring Docker-log inspection.

Target direction:

```json
{
  "status": "ok",
  "vault": {
    "available": true,
    "notes": 842
  },
  "semantic_index": {
    "state": "indexing",
    "ready_for_search": true,
    "indexed_notes": 734,
    "total_notes": 842,
    "chunks": 4610,
    "last_successful_sync": "2026-08-23T10:00:00Z"
  }
}
```

### VB-014 — Optional filesystem watcher — P1

Only after the background indexer exists.

Potential approach:

- `watchdog`/inotify,
- debounce Obsidian/Syncthing bursts,
- enqueue changed Markdown paths,
- watcher remains optional.

## Milestone 2 exit criteria

- [ ] synchronization commits durable progress in batches
- [ ] full vault rebuild no longer runs inline in normal search requests
- [ ] restart does not discard already committed batches
- [ ] lifecycle and progress are observable
- [ ] note writes can trigger targeted semantic refresh
- [ ] no external queue/service is required

---

# Milestone 3 — Retrieval quality and evaluation

**Goal:** improve relevance using measured changes before considering a larger model or vector database.

### VB-020 — Markdown heading-aware chunker — P0

- preserve heading hierarchy
- avoid arbitrary fenced-code splitting where practical
- handle lists and long sections predictably
- add focused Markdown tests

### VB-021 — Embed title + heading hierarchy + chunk — P0

Embedding context should intentionally include note/section identity.

Example:

```text
Title: Infrastructure Notes
Heading: Jellyfin > Transcoding

<chunk content>
```

Changing representation must invalidate the index signature.

### VB-022 — Retrieval evaluation fixture — P0

Create sanitized EN/SK/cross-language query cases under:

```text
tests/eval/retrieval_cases.yaml
```

### VB-023 — Retrieval benchmark command — P1

Record latency, paths, semantic score, lexical score, final score, and rank.

### VB-024 — Tune hybrid ranking from evaluation data — P1

No weight/threshold changes without before/after evaluation.

Candidate metrics:

- Top-1 accuracy
- Recall@5
- Mean Reciprocal Rank
- latency

## Milestone 3 exit criteria

- [ ] retrieval quality is repeatably measurable
- [ ] Markdown structure contributes context
- [ ] ranking regressions are detectable
- [ ] default-model changes require evidence

---

# Milestone 4 — Knowledge-maintenance operations

**Goal:** let clients maintain a knowledge base safely, not merely search it.

### VB-030 — Duplicate candidate service — P1

Return candidates only; no automatic merging.

### VB-031 — Verified related-note suggestions — P1

Return only real vault paths with path/title/score/heading/snippet.

### VB-032 — Section-level update design / ADR — P1

Design concurrency and section identity before implementation.

### VB-033 — `updateNoteSection` endpoint — P1

Must include conflict detection.

### VB-034 — Opt-in verified backlink insertion — P2

No invented wikilink targets.

---

# Milestone 5 — Operational maturity and security

**Goal:** make VaultBridge predictable to run continuously on NAS/server hardware.

### VB-040 — Structured JSON logging — P0
### VB-041 — Request IDs and latency logging — P0
### VB-044 — Liveness and readiness endpoints — P0
### VB-045 — Index integrity/rebuild CLI — P0
### VB-042 — API key rotation — P1
### VB-043 — Lightweight rate limiting — P1

Do not add Redis.

---

# Milestone 6 — Public API and developer experience

**Goal:** make VaultBridge easy to understand and deploy by someone other than the original author.

### VB-050 — Introduce `/api/v1` — P0
### VB-051 — VaultBridge CLI — P1
### VB-052 — Generic Docker deployment docs — P0
### VB-053 — TrueNAS deployment docs — P0

ChatGPT Action remains an integration example, not the core product identity.

---

# Milestone 7 — Distribution and `v1.0.0`

**Goal:** ship VaultBridge as a polished public open-source project.

### VB-054 — Publish GHCR image workflow — P0
### VB-055 — Multi-architecture image — P1
### VB-056 — GitHub v1.0 release checklist — P0

## `v1.0.0` acceptance criteria

- [ ] clean install succeeds from public documentation
- [ ] CI is green
- [ ] container image is published
- [ ] public API is versioned
- [ ] full semantic rebuild does not block ordinary request handling
- [ ] retrieval evaluation suite exists
- [ ] no known authentication/path-traversal bypass
- [ ] secrets are not committed or logged
- [ ] upgrade/rebuild procedure is documented

---

# Post-1.0 candidates

- pluggable embedding providers
- alternative local embedding models
- SQLite vector extension / HNSW acceleration
- Qdrant adapter for very large vaults
- read-only mode
- per-folder access policies
- multiple vaults
- web dashboard
- MCP server adapter
- webhook/event integrations
- frontmatter query language
- graph-aware ranking using Obsidian links

---

# Explicit non-goals

Do not implement these unless requirements explicitly change:

- arbitrary filesystem API
- remote shell execution
- automatic note deletion
- mandatory cloud services
- mandatory external embedding APIs
- Kubernetes deployment
- full Obsidian synchronization replacement
- general-purpose vector database by default
- distributed/multi-process semantic-index coordination before there is a real need

---

# Recommended implementation path

```text
FOUNDATION
VB-001 ✓
   ↓
VB-002 ✓
   ↓
VB-004 ✓
   ↓
VB-003 ✓
   ↓
VB-005 ✓
   ↓
INDEX LIFECYCLE
VB-010 ✓
   ↓
VB-011  ← NEXT
   ↓
VB-012
   ↓
VB-013
   ↓
VB-015
   ↓
RETRIEVAL QUALITY
VB-020
   ↓
VB-021
   ↓
VB-022
   ↓
VB-024
   ↓
OPERATIONS / KNOWLEDGE / DX
...
   ↓
v1.0.0
```

`VB-014`, `VB-023`, and other P1/P2 tasks may be scheduled when their prerequisites exist and their value is demonstrated.

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
