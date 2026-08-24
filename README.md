# VaultBridge

**VaultBridge is a self-hosted REST and semantic search API for an Obsidian vault.**

It exposes a deliberately small API for reading, searching and safely writing notes while keeping Markdown files as the source of truth. Semantic search runs locally with an ONNX embedding model and a disposable SQLite index.

> Status: **pre-release / working prototype**. The current implementation is already usable; the repository is being refactored toward a public `1.0.0` release. See [`ROADMAP.md`](ROADMAP.md).

## Why VaultBridge

VaultBridge is intended to sit between an Obsidian vault and API-capable clients such as ChatGPT, scripts, automations or future applications.

```text
Client
  |
  | HTTPS + Bearer key
  v
VaultBridge (FastAPI)
  |                 \
  v                  v
Markdown vault    Semantic retrieval
                     |
                 FastEmbed/ONNX
                     |
                 SQLite index
```

The vault is never replaced by the index and there is no general filesystem endpoint.

## Current capabilities

- create Markdown notes
- append idempotently to existing notes
- read one note
- literal text search
- multilingual semantic related-note search
- hybrid semantic + lexical ranking
- list notes
- Bearer API-key authentication
- path traversal protection
- incremental semantic indexing
- targeted background semantic refresh after successful API note writes
- Docker deployment
- TrueNAS deployment example
- Custom GPT Action example

## Current API

| Method | Path | Operation |
|---|---|---|
| GET | `/health` | `healthCheck` |
| POST | `/notes` | `createNote` |
| POST | `/notes/append` | `appendNote` |
| GET | `/notes/read` | `readNote` |
| POST | `/notes/search` | `searchNotes` |
| POST | `/notes/related` | `findRelatedNotes` |
| GET | `/notes/list` | `listNotes` |

The public versioned `/api/v1` API is planned before `1.0.0`.

## Health and operator status

`GET /health` is unauthenticated as before and returns a cheap factual snapshot without loading the
embedding model, synchronizing the vault, generating embeddings, or performing a search.

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

`ok` means the application answered the health request; vault availability remains a separate fact.
`semantic_index_ready` retains its original meaning (`semantic_index_state == "ready"`). Search
availability is separate because an existing compatible index can remain searchable while a refresh
is `indexing` or after that refresh fails with `error`. `full_sync_required` is process-local and is
also true while unresolved full-sync recovery debt exists; it does not claim system-wide recovery.

Indexed-note/chunk counts come from one short-lived, coherent read-only SQLite snapshot.
`vault_notes` counts semantic-index-eligible Markdown files using the same containment, internal
directory exclusion and maximum-size policy as full synchronization, without reading note contents.
Together these counts provide inferred completeness; they are not explicit current-note, percentage,
batch or ETA progress counters. `last_successful_sync` is `null` until the first successful full
synchronization and is not advanced by targeted note refreshes.

## Semantic search

Default embedding model:

```text
sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

FastEmbed runs the ONNX model locally on CPU. Markdown is split on ATX heading boundaries where
practical, and each chunk retains its heading hierarchy. Oversized sections remain bounded and are
split through exact source slices while fitted fenced-code blocks stay intact. Adjacent tiny sections
coalesce into useful bounded chunks whose metadata identifies the contained heading range. Markdown
chunks and normalized embeddings are stored in SQLite. Persisted chunk content remains unchanged;
embedding input adds the canonical heading hierarchy between note title and content when that context
is not already the chunk's first heading. The semantic database is derived data and can always be
rebuilt from the vault.

Upgrading from the earlier fixed-size chunker or the VB-020 heading-aware embedding representation
changes the semantic index signature. VaultBridge automatically discards incompatible derived chunks
and rebuilds them from the Markdown vault; no manual SQLite migration is required. If a targeted
refresh encounters an older signature, it safely performs the required full rebuild.

Example:

```bash
curl -X POST http://127.0.0.1:8765/notes/related \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"professional courses and certifications","limit":5}'
```

## Quick start with Docker

```bash
cp .env.example .env
```

Generate a secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set at minimum:

```env
API_KEY=replace-with-your-secret
OBSIDIAN_VAULT_PATH=/path/to/your/Obsidian/Vault
```

Start:

```bash
docker compose up -d --build
curl http://127.0.0.1:8765/health
```

Application startup begins downloading the embedding model, when needed, and synchronizing the index in the background. Semantic requests return no results while the first index is still building; if that initial build fails, semantic requests return HTTP `503`. A compatible committed index remains searchable during an in-process refresh. After restart from a persisted `error`, VaultBridge conservatively waits for a successful startup full synchronization before exposing that index again.

Successful `createNote` and `appendNote` mutations queue only the affected note for background semantic refresh. Repeated pending writes to the same note are coalesced, and note-write responses do not wait for embedding or fail after a durable write if scheduling is temporarily unavailable. Failed targeted refreshes keep their paths for a later write-triggered retry; shutdown may discard the process-local queue, with the next startup full synchronization recovering from Markdown. External edits are still discovered by startup/full synchronization; no filesystem watcher is enabled.

The current production/TrueNAS deployment keeps the Obsidian Markdown source at `/vault` and sets
`SEMANTIC_DATA_PATH=/data`. The `/data` mount contains `semantic-index.sqlite3` plus downloaded
model/cache data, keeping disposable semantic artifacts outside the vault.

## Application configuration

VaultBridge reads and validates its application settings once at startup. Invalid numeric values or empty paths/model names stop startup with a configuration error; the API key is treated as a secret and is not included in the settings representation.

| Environment variable | Default | Constraint |
|---|---|---|
| `API_KEY` | empty | Required for authenticated endpoints, as before |
| `VAULT_PATH` | `/vault` | Non-empty path |
| `MAX_NOTE_BYTES` | `1000000` | Positive integer |
| `SEMANTIC_DATA_PATH` | `/vault/.obsidian-chatgpt-data` | Non-empty path |
| `SEMANTIC_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Non-empty name |
| `SEMANTIC_CHUNK_CHARS` | `600` | Integer, at least `250` |
| `SEMANTIC_CHUNK_OVERLAP` | `100` | Non-negative integer, at most half of chunk size |
| `SEMANTIC_INDEX_BATCH_SIZE` | `25` | Positive integer; maximum notes committed per indexing transaction |

`OBSIDIAN_VAULT_PATH`, `API_PORT`, `PUID`, and `PGID` remain Docker Compose inputs and are not read by the Python application.
The application default for `SEMANTIC_DATA_PATH` remains inside `/vault` for backward compatibility;
the production TrueNAS compose file explicitly overrides it to `/data`.

## Semantic index administration

After stopping VaultBridge, inspect the configured vault and persisted semantic index without loading
the embedding model or changing any semantic-storage file:

```bash
python -m app.cli index check
```

The check reports vault, database, schema, signature, persisted lifecycle state, standalone persisted
searchability, physical stored counts and the stored last successful full sync. It uses SQLite's
immutable mode and refuses to inspect a database with WAL/SHM sidecars; stop VaultBridge first. It
does not scan note contents, construct FastEmbed, or report live process availability. Use `/health`
and `/health/ready` as the authoritative live-process views.

Explicitly rebuild all derived semantic data from authoritative Markdown:

```bash
python -m app.cli index rebuild
```

Stop the VaultBridge application before check or rebuild. There is no cross-process index lock, and
rebuild intentionally clears derived notes/chunks and current lifecycle state before using the normal full-sync,
chunking, embedding and batching pipeline. Markdown files are not changed.
SQLite files explicitly identified by SQLite as corrupt/not-a-database are recreated as derived data;
lock, permission and other database failures remain failures rather than being mislabeled corruption.

Exit codes are stable: `0` means healthy/success, `1` means an integrity/readiness problem or failed
rebuild, and `2` means invalid CLI usage/configuration or an unexpected programming failure. A failed
full-sync finalization atomically preserves the previous successful-sync timestamp and does not persist
`ready`. Output is
concise human-readable text; VB-045 does not add JSON mode because its backlog item does not require it.

## TrueNAS

The existing TrueNAS-specific deployment files are kept for compatibility with the working prototype. See [`README_TRUENAS.md`](README_TRUENAS.md).

The service/container names, data-directory name, and `ObsidianChatGPT*` TrueNAS paths in those files are legacy compatibility identifiers. They remain unchanged so an existing deployment can be rebuilt without an implicit migration.

## Development

See [`DEVELOPMENT.md`](DEVELOPMENT.md).

Core checks:

```bash
PYTHONPATH=. pytest -q
python -m compileall -q app
```

## Codex development

The repository is prepared for task-by-task Codex work:

- [`AGENTS.md`](AGENTS.md) — constraints Codex should follow
- [`ROADMAP.md`](ROADMAP.md) — project phases
- [`BACKLOG.md`](BACKLOG.md) — small issue-sized tasks
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — current and target design
- [`docs/CODEX_PLAYBOOK.md`](docs/CODEX_PLAYBOOK.md) — ready-to-use prompts

Start with `VB-001`, then follow the recommended sequence in `BACKLOG.md`.

## Security

- use a long random API key,
- expose the service publicly only through HTTPS,
- never expose the vault directory itself,
- do not commit `.env`, semantic databases or model cache,
- embeddings are computed locally by default,
- there is intentionally no delete endpoint.

See [`SECURITY.md`](SECURITY.md).

## License

VaultBridge is licensed under the [MIT License](LICENSE).
