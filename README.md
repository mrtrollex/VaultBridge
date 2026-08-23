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

## Semantic search

Default embedding model:

```text
sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

FastEmbed runs the ONNX model locally on CPU. Markdown chunks and normalized embeddings are stored in SQLite. The semantic database is derived data and can always be rebuilt from the vault.

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

The first semantic request downloads the embedding model and builds the initial index. Subsequent requests reuse the index and only changed notes need to be re-embedded.

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
