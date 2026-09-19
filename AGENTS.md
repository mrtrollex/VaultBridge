# AGENTS.md — VaultBridge

This file defines repository-level instructions for Codex and other coding agents.

## Mission

VaultBridge is a small self-hosted REST + semantic search bridge for Obsidian Markdown vaults. Markdown files remain the source of truth. The project should stay simple enough to run on a home server/NAS with Docker and CPU-only embeddings.

## Core constraints

- Preserve the **minimum API surface necessary**.
- Do not add arbitrary filesystem operations.
- Do not add a delete endpoint without an explicit project decision.
- Do not send note content to external embedding services by default.
- Do not add Redis, Celery, Qdrant, Kubernetes, or another service without a measured requirement and an ADR.
- SQLite is the default semantic metadata/index store.
- The Obsidian vault must remain usable without VaultBridge.
- All vault paths must be vault-relative and protected against traversal/symlink escape.
- Never log or commit API keys, vault contents, model caches, or generated semantic databases.

## Current compatibility contract

Preserve these endpoints and operation IDs. New integrations should prefer `/api/v1`; the
unversioned note/search routes remain compatibility aliases:

| Method | Path | operationId |
|---|---|---|
| GET | `/health` | `healthCheck` |
| GET | `/health/live` | `livenessCheck` |
| GET | `/health/ready` | `readinessCheck` |
| POST | `/notes` | `createNote` |
| POST | `/notes/append` | `appendNote` |
| GET | `/notes/read` | `readNote` |
| POST | `/notes/search` | `searchNotes` |
| POST | `/notes/related` | `findRelatedNotes` |
| POST | `/notes/duplicates` | `findDuplicateCandidates` |
| GET | `/notes/list` | `listNotes` |
| POST | `/api/v1/notes` | `createNoteV1` |
| POST | `/api/v1/notes/append` | `appendNoteV1` |
| GET | `/api/v1/notes/read` | `readNoteV1` |
| POST | `/api/v1/notes/search` | `searchNotesV1` |
| POST | `/api/v1/notes/related` | `findRelatedNotesV1` |
| POST | `/api/v1/notes/duplicates` | `findDuplicateCandidatesV1` |
| GET | `/api/v1/notes/list` | `listNotesV1` |

The schema-hidden `/privacy` route also remains unversioned. Do not silently change request or
response semantics.

## Working method

For every implementation task:

1. Read `ROADMAP.md`, `ARCHITECTURE.md`, and the relevant item in `BACKLOG.md`.
2. Inspect the existing implementation before proposing a rewrite.
3. Make the smallest coherent change that satisfies the task.
4. Add or update tests for changed behaviour.
5. Run the required checks.
6. Update docs only where behaviour actually changed.
7. Summarize files changed, design decisions, tests run, and any remaining risk.

## Required checks

Before implementation, prepare a compact task packet from the repository root:

```bash
python scripts/agent_task.py --task-file path/to/task.md
```

For short task context, use `--task "..."` instead; exactly one task source is required. The tool
writes `.agent/task_packet.md` by default and does not invoke Codex. Start the implementation session
with that packet, then inspect task-relevant code and authoritative documentation as needed. Use
repeatable `--context-file` arguments only for repository files known to be relevant.

For the normal end-of-task workflow, use the repository-local wrapper from the repository root:

```bash
python scripts/agent_finish.py --task-file path/to/task.md
```

For short task context, use `--task "..."` instead of `--task-file`; exactly one task source is
required. The wrapper runs `agent_check.py`, stops without a review packet when verification fails
or is required-but-unavailable, and otherwise writes `.agent/review_packet.md` with
`agent_review.py`. It does not invoke Codex. Give that packet to a fresh Codex session without the
implementer's conversation history.

The underlying tools remain independently usable. To run only the repository-local verification
selector:

```bash
python scripts/agent_check.py
```

It discovers branch, staged, unstaged, and relevant untracked changes, then selects the checks for
the affected surface. A task is not complete when it reports a failed or required-but-unavailable
check. UI changes include the Chromium Playwright regression suite; the selector reports missing
Python or browser support without installing it. TrueNAS/deployment and Action/OpenAPI changes may
also report required agent verification that must be performed separately. See
`docs/CODEX_PLAYBOOK.md` for browser acceptance guidance. The selector complements GitHub CI; it
does not replace CI.

After standalone verification passes, `agent_review.py` can still generate a packet directly:

```bash
python scripts/agent_review.py --task-file path/to/task.md --output review_packet.md
```

The reviewer inspects and reports findings only; it does not modify files or commit. The normal flow
is `agent_task.py -> implementation session with .agent/task_packet.md -> implement ->
agent_finish.py -> fresh Codex review of .agent/review_packet.md -> fix confirmed findings if
necessary -> agent_finish.py again -> APPROVE -> PR / CI`. See
`docs/CODEX_PLAYBOOK.md` for handoff details.

The underlying canonical Python checks remain:

```bash
ruff check app tests scripts
PYTHONPATH=. pytest -q --ignore=tests/e2e
python -m pytest -q tests/e2e --browser=chromium
python -m compileall -q app
```

When Docker-related files change:

```bash
docker compose config
docker build -t vaultbridge:test .
```

When OpenAPI/action files change, verify operation IDs still map to actual endpoints.

## Refactoring rules

- Refactors should be behaviour-preserving unless the task explicitly changes behaviour.
- Prefer dependency injection and small services over global state, but do not introduce a framework solely for DI.
- Keep FastAPI/Pydantic idiomatic.
- Prefer standard library solutions where they are sufficient.
- Avoid abstractions that have only one trivial implementation unless they isolate I/O or make testing materially easier.
- Keep semantic search testable with a fake embedder; tests must not download ML models.

## Semantic search rules

- Measure retrieval quality with repeatable evaluation cases before changing the default model.
- Title/path lexical boosts must remain secondary to semantic relevance.
- Changing model/chunking/index format must invalidate or migrate the index safely.
- The service must always be able to rebuild the semantic index from the Markdown vault.
- Search results should expose enough information to debug ranking (`semantic_score`, lexical/final score where applicable).

## Security rules

Treat these areas as security-sensitive:

- path resolution
- authentication
- note writes
- API key handling
- reverse-proxy/public deployment instructions
- content size limits

Changes in these areas require explicit tests for failure cases.

## Dependency policy

Before adding a dependency, explain:

- what concrete problem it solves,
- why the standard library/current stack is insufficient,
- runtime size/operational impact,
- whether it introduces another service or network dependency.

Do not add an LLM orchestration framework to the core project.

## Documentation language

Public repository documentation should be written in English. Deployment examples must use placeholders and must never contain the author's real API key or private vault content.
