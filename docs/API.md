# REST API and semantic retrieval

Use `/api/v1` for new integrations. See [setup](DOCKER.md), [named scopes](KNOWLEDGE_SPACES.md) and the [compatibility contract](../ARCHITECTURE.md#http-api-versioning).

The examples below use legacy/default selection. Named requests follow the explicit scope guide;
named readiness covers only the authorized default and does not certify every registry entry.

## Current API

New integrations should use the versioned application API:

| Method | Path | Operation |
|---|---|---|
| POST | `/api/v1/notes` | `createNoteV1` |
| POST | `/api/v1/notes/append` | `appendNoteV1` |
| GET | `/api/v1/notes/read` | `readNoteV1` |
| POST | `/api/v1/notes/search` | `searchNotesV1` |
| POST | `/api/v1/notes/related` | `findRelatedNotesV1` |
| POST | `/api/v1/notes/duplicates` | `findDuplicateCandidatesV1` |
| GET | `/api/v1/notes/list` | `listNotesV1` |
| GET | `/api/v1/notes/links` | `listNoteLinksV1` |
| GET | `/api/v1/notes/backlinks` | `listNoteBacklinksV1` |
| POST | `/api/v1/knowledge/hygiene/scan` | `scanKnowledgeHygieneV1` |
| POST | `/api/v1/knowledge/query` | `queryKnowledgeV1` |

Existing clients may continue using the unversioned compatibility layer:

| Legacy path | Legacy operation | Preferred path |
|---|---|---|
| `/notes` | `createNote` | `/api/v1/notes` |
| `/notes/append` | `appendNote` | `/api/v1/notes/append` |
| `/notes/read` | `readNote` | `/api/v1/notes/read` |
| `/notes/search` | `searchNotes` | `/api/v1/notes/search` |
| `/notes/related` | `findRelatedNotes` | `/api/v1/notes/related` |
| `/notes/duplicates` | `findDuplicateCandidates` | `/api/v1/notes/duplicates` |
| `/notes/list` | `listNotes` | `/api/v1/notes/list` |

Both paths in each compatibility pair use the same authentication, validation, response, error, and domain-service
implementation. The checked-in ChatGPT Action schema remains on the legacy paths so existing Actions
continue working without an immediate configuration change. Migrating that external configuration
and removing the compatibility layer are separate future decisions; no removal date is set.

The relationship endpoints are versioned-only and have no unversioned compatibility aliases. They
return live derived link metadata and perform no Markdown or semantic-index mutation.

Operational routes remain unversioned: `GET /health` (`healthCheck`), `GET /health/live`
(`livenessCheck`), and `GET /health/ready` (`readinessCheck`). The public `GET /privacy` text endpoint
is intentionally excluded from OpenAPI and also remains unversioned.

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
refresh encounters an older signature, it safely performs the required full rebuild. Upgrading from
`v1.1.0` to `v1.2.0` also performs one automatic rebuild because `v1.2.0` fingerprints the effective
embedding backend and model artifacts before reusing persisted vectors. Markdown is not changed.

Example:

```bash
curl -X POST http://127.0.0.1:8765/api/v1/notes/related \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"professional courses and certifications","limit":5}'
```

Before creating a note, clients can request advisory duplicate candidates:

```bash
curl -X POST http://127.0.0.1:8765/api/v1/notes/duplicates \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title":"Professional certifications","text":"Courses, exams, and renewal notes","limit":5}'
```

The response contains only real live vault notes. Conservative normalized filename equivalence is
reported as `exact_title` and is stronger evidence than semantic relatedness; a `semantic` match does
not prove that two notes are duplicates. Semantic paths are verified against the live vault before
return, and the operation never merges, creates, appends, renames, deletes, or otherwise writes notes
or the semantic index.
