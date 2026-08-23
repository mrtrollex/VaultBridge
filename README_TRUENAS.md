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

Before semantic search is initialized:

```json
{"ok":true,"vault_exists":true,"semantic_index_ready":false}
```

## 6. Warm up the semantic index

Do this once from your LAN after deployment. The first call downloads the multilingual ONNX model and embeds the existing vault.

PowerShell:

```powershell
curl.exe -X POST http://TRUENAS_IP:8765/notes/related `
  -H "Authorization: Bearer YOUR_API_KEY" `
  -H "Content-Type: application/json" `
  -d '{"text":"TrueNAS home server storage","limit":3}'
```

When it finishes, `/health` should show:

```json
{"ok":true,"vault_exists":true,"semantic_index_ready":true}
```

Later semantic searches only re-index Markdown files whose timestamp/content changed. The current `0.1.0` public baseline includes hybrid reranking (semantic similarity + title/path/content overlap) and remains compatible with the existing semantic index.

## 7. Update the GPT Action

Replace the existing Action schema with the new `action_openapi.yaml` and keep your existing server hostname.

The new Action is:

```text
findRelatedNotes -> POST /notes/related
```

Your previous actions remain unchanged.
