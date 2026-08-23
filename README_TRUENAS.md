# VaultBridge TrueNAS deployment

The `ObsidianChatGPT*` host paths and `obsidian-chatgpt` container name shown here are legacy compatibility identifiers. Keep them for an existing installation until a separate migration is documented; they do not change the public product name, VaultBridge.

## Recommended storage

Use three locations:

- `/mnt/Apps/AppsData/ObsidianChatGPT` — bridge source code
- `/mnt/Apps/AppsData/ObsidianVault` — Obsidian vault
- `/mnt/Apps/AppsData/ObsidianChatGPTData` — SQLite semantic index + downloaded embedding model

Keeping semantic data outside the vault avoids syncing a few hundred MB of model/cache files through Obsidian or your vault sync tool.

## 1. Copy the project

Copy the project to:

```text
/mnt/Apps/AppsData/ObsidianChatGPT
```

Keep your existing `.env` file or copy `.env.truenas.example` to `.env` and insert your API key.

The modified project archive intentionally does not contain a real `.env` secret.

## 2. Create semantic data storage

Create this dataset/directory if it does not already exist:

```text
/mnt/Apps/AppsData/ObsidianChatGPTData
```

The container runs as UID/GID `568:568`, so both the vault and semantic-data location need read/write permission for the TrueNAS `apps` user/group.

## 3. Check `compose.truenas.yml`

The important mounts are:

```yaml
volumes:
  - "/mnt/Apps/AppsData/ObsidianVault:/vault"
  - "/mnt/Apps/AppsData/ObsidianChatGPTData:/data"
```

Semantic configuration:

```yaml
environment:
  VAULT_PATH: /vault
  SEMANTIC_DATA_PATH: /data
  SEMANTIC_MODEL: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
  SEMANTIC_CHUNK_CHARS: "600"
  SEMANTIC_CHUNK_OVERLAP: "100"
  SEMANTIC_INDEX_BATCH_SIZE: "25"
```

Adjust the host paths if your TrueNAS datasets differ.

## 4. Deploy/rebuild

For a source update, rebuild the custom app so the new Python dependencies are installed.

The Docker image now also installs `libgomp1`, which ONNX Runtime needs for CPU/OpenMP execution on Linux.

## 5. Health check

From another machine:

```powershell
curl.exe http://TRUENAS_IP:8765/health
```

During initial semantic indexing:

```json
{
  "ok": true,
  "vault_exists": true,
  "semantic_index_ready": false,
  "semantic_index_state": "indexing",
  "semantic_search_available": false,
  "semantic_indexer_running": true,
  "full_sync_required": true,
  "indexed_notes": 0,
  "semantic_chunks": 0,
  "vault_notes": 842,
  "last_successful_sync": null
}
```

The `/vault` mount contains the Obsidian Markdown source. The `/data` mount contains
`semantic-index.sqlite3` plus downloaded model/cache data. Health polling reads status and counts;
it does not start synchronization or load the model. `vault_notes` counts only Markdown files
eligible for semantic indexing after containment, internal-directory and maximum-size checks.

## 6. Verify the semantic index

Startup downloads the multilingual ONNX model when needed and synchronizes the existing vault in the background. Poll `/health` until `semantic_index_ready` is `true`, then verify a semantic request from your LAN:

While the first index is building, semantic requests return no results. If initial indexing fails,
semantic requests return HTTP `503`; restart the application after correcting the model, storage,
or vault-access problem to retry synchronization.

PowerShell:

```powershell
curl.exe -X POST http://TRUENAS_IP:8765/notes/related `
  -H "Authorization: Bearer YOUR_API_KEY" `
  -H "Content-Type: application/json" `
  -d '{"text":"TrueNAS home server storage","limit":3}'
```

When startup indexing finishes, `/health` should show `semantic_index_state: "ready"`,
`semantic_index_ready: true`, and `semantic_search_available: true`, for example:

```json
{
  "ok": true,
  "vault_exists": true,
  "semantic_index_ready": true,
  "semantic_index_state": "ready",
  "semantic_search_available": true,
  "semantic_indexer_running": false,
  "full_sync_required": false,
  "indexed_notes": 842,
  "semantic_chunks": 4610,
  "vault_notes": 842,
  "last_successful_sync": "2026-08-23T10:00:00+00:00"
}
```

An `indexing` or `error` lifecycle state can still have `semantic_search_available: true` when a
previous compatible completed index remains usable. `full_sync_required: true` reports process-local
full-sync work or recovery debt; it is not a claim that the whole application is recovering.

Later application startups synchronize only Markdown files whose timestamp/content changed. The current `0.1.0` public baseline includes hybrid reranking (semantic similarity + title/path/content overlap) and remains compatible with the existing semantic index.

## 7. Update the GPT Action

Replace the existing Action schema with the new `action_openapi.yaml` and keep your existing server hostname.

The new Action is:

```text
findRelatedNotes -> POST /notes/related
```

Your previous actions remain unchanged.
