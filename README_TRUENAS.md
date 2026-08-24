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

Use the public liveness probe for a process/container check:

```powershell
curl.exe -fsS http://127.0.0.1:8765/health/live
```

It always returns HTTP `200` with `{"ok":true}` while the API process can serve requests. It does
not inspect the vault, model, semantic database, or background indexer.

Use the public readiness probe for load balancers and orchestration:

```powershell
curl.exe -fsS http://127.0.0.1:8765/health/ready
```

It returns HTTP `200` with `{"ready":true}` when the vault is an inspectable directory and semantic
search is available. It returns HTTP `503` with `{"ready":false}` during the first index build or when
the vault/index is unavailable, including expected permission, filesystem, or SQLite access failures.
An older compatible index remains ready while a refresh is running or after that refresh fails;
compatible legacy indexes with chunks also remain usable without probe-side mutation. Readiness does
not scan/count the vault, load the model, search, embed, or start indexing.

Use the richer operator endpoint for lifecycle details and counts:

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

### Check or rebuild from a TrueNAS SSH shell

VaultBridge has no cross-process index lock. Stop the API container before either command, then run
the check in an isolated Compose container with the same environment and mounts:

```bash
cd /mnt/Apps/AppsData/ObsidianChatGPT
docker compose -f compose.truenas.yml stop obsidian-api
docker compose -f compose.truenas.yml run --rm --no-deps obsidian-api python -m app.cli index check
check_status=$?
```

It reports persisted vault/database/schema/signature/lifecycle/standalone-searchability/physical-count/
last-sync status without loading the embedding model or changing SQLite, WAL, SHM, or other semantic
storage files. It refuses inspection when WAL/SHM sidecars are present. `/health` and `/health/ready`
remain authoritative for live process availability. Exit `0` means the stopped persisted index is
healthy; exit `1` means an integrity/readiness problem; exit `2` means CLI, configuration or
programming failure.

Rebuild is destructive only to the disposable semantic index. VaultBridge does not have a
cross-process index lock, so keep the API stopped and run one isolated Compose command with the same
environment and mounts. If `check_status` is nonzero, review its output before choosing to rebuild:

```bash
cd /mnt/Apps/AppsData/ObsidianChatGPT
docker compose -f compose.truenas.yml run --rm --no-deps obsidian-api python -m app.cli index rebuild
rebuild_status=$?
docker compose -f compose.truenas.yml up -d obsidian-api
exit $rebuild_status
```

Rebuild uses the configured local model and may download it if the `/data` model cache is empty. It
recreates chunks/embeddings through the production full-sync pipeline and never changes Markdown.
SQLite storage explicitly reported as corrupt/not-a-database is recreated; other database errors fail.
Do not run check or rebuild through `docker exec` against the running API container. If no rebuild is
needed, start the service with `docker compose -f compose.truenas.yml up -d obsidian-api` after check.

Later application startups synchronize only Markdown files whose timestamp/content changed. The
heading-aware chunker changes the semantic index signature, so the first startup after this upgrade
automatically discards old derived chunks and schedules a full rebuild from Markdown. No manual
SQLite migration is required. The ranking model and hybrid ranking weights are unchanged.

## Application logs

VaultBridge application events are written to the container's standard error stream as one JSON
object per line, so no persistent log directory or application-managed rotation is required. Inspect
them through the TrueNAS app log view or Docker, for example:

```bash
docker logs --tail 100 obsidian-chatgpt
```

A completed synchronization resembles:

```json
{"timestamp":"2026-08-24T05:52:00.123Z","level":"INFO","logger":"vaultbridge.semantic","event":"semantic_sync_completed","message":"Semantic synchronization completed","operation":"full","indexed_notes":12,"unchanged_notes":830,"removed_notes":0,"duration_ms":421.7,"index_state":"ready"}
```

Every HTTP response includes an internally generated `X-Request-ID`. The same value appears on
VaultBridge events emitted while that request is handled, for example:

```text
X-Request-ID: 9f48b4f6d3b24b02a6a82db8a415ce70
```

```json
{"timestamp":"2026-08-24T06:02:00.123Z","level":"INFO","logger":"vaultbridge.http","event":"request_completed","message":"HTTP request completed","request_id":"9f48b4f6d3b24b02a6a82db8a415ce70","method":"POST","route":"/notes","status_code":200,"duration_ms":5.271}
```

VaultBridge does not accept caller-selected request IDs: an incoming `X-Request-ID` is replaced.
Synchronous note-write and queue-scheduling events share the request ID, while later background
indexer execution does not inherit it. Durations use a monotonic clock and cover VaultBridge request
handling. Uvicorn access/server logs remain separate and may use their own format.

VaultBridge JSON records intentionally exclude API keys, Authorization headers, note content,
embedding/query text, exception messages, and absolute host paths. Vault-relative note paths may be
present. Uvicorn's server/access records retain their framework-managed format, so a container log
stream can contain both JSON VaultBridge records and ordinary Uvicorn records.

## 7. Update the GPT Action

Replace the existing Action schema with the new `action_openapi.yaml` and keep your existing server hostname.

The new Action is:

```text
findRelatedNotes -> POST /notes/related
```

Your previous actions remain unchanged.
