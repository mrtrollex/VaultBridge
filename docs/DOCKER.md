# Docker setup and operations

The [README quick start](../README.md#quick-start) uses the published stable image. This guide preserves the generic source-build Compose path and detailed operator settings. TrueNAS has a [separate runbook](../README_TRUENAS.md).

## Quick start

### Prerequisites

- Docker Engine or Docker Desktop with Docker Compose support
- Git, when cloning the repository
- an existing Obsidian vault directory
- enough free disk space for the local embedding model cache and semantic index
- internet access when the container image and embedding model are downloaded for the first time

TrueNAS, Kubernetes, Redis, an external vector database, and a cloud embedding API are not required.

The following is the canonical Linux/macOS shell workflow:

```bash
git clone https://github.com/mrtrollex/VaultBridge.git
cd VaultBridge
git checkout v1.5.0
cp .env.example .env
docker run --rm python:3.12-slim python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Copy the generated value into `API_KEY` in `.env`, then set the host path to your existing vault:

```env
API_KEY=YOUR_GENERATED_API_KEY
API_KEY_PREVIOUS=
OBSIDIAN_VAULT_PATH=/path/to/your/Obsidian/Vault
```

`OBSIDIAN_VAULT_PATH` is a path on the Docker host. Compose bind-mounts it at `/vault` in the
container; it is not a container path. Never commit `.env`. It is ignored by Git, but you should
still treat it as a secret-bearing file.

This generic Compose path does not forward `KNOWLEDGE_SPACES_JSON` from `.env`. Named mode needs
explicit environment injection and independent root/data mounts; see the
[named Docker setup](KNOWLEDGE_SPACES.md#docker-configuration).
Published v1.5.0 provides named composition as an explicit opt-in.

On Linux, set the container identity to the user that owns the vault:

```bash
id -u
id -g
```

Put those values in `PUID` and `PGID` in `.env`. The defaults are `1000:1000`. VaultBridge needs
read access for all note operations and write access for create/append operations and its derived
semantic-data directory. Fix ownership or access-control rules deliberately; do not use `chmod 777`.
Docker Desktop for macOS and Windows mediates bind-mount permissions through its VM, so host UID/GID
behavior is not identical to native Linux. Ensure the vault directory is shared with Docker Desktop;
leave the defaults unless your Docker Desktop setup requires different values.

Build and start VaultBridge:

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail 100
```

The source build uses Python 3.12, installs the Linux `libgomp1` runtime required by ONNX Runtime,
and starts Uvicorn on container port `8000`.

## GHCR release images

Published GitHub Releases also produce the same Dockerfile-based application image at:

```text
ghcr.io/<repository-owner>/vaultbridge:<version>
```

Use the lowercase repository owner shown on the package page. Published stable v1.5.0 uses
the image tag `1.5.0`:

```bash
docker pull ghcr.io/<repository-owner>/vaultbridge:1.5.0
```

The publication workflow accepts v-prefixed semantic release tags. Every release receives the exact
version tag without the leading `v`. A stable release also updates its `major.minor` alias and
`major` alias plus `latest`; a GitHub prerelease receives only its exact prerelease tag, such as
`1.0.0-rc.1`. Release tags must not be reused. For a cryptographically immutable deployment
reference, copy the published
digest from GHCR and pull `ghcr.io/<repository-owner>/vaultbridge@sha256:...`.

GHCR packages are private on first publication unless account/organization policy says otherwise.
After the first successful release, the repository owner should open the package's **Package
settings**, verify repository linkage, and deliberately change visibility to **Public** if anonymous
pulls are intended. Public visibility is not assumed by this workflow.

The checked-in source-build Compose workflow remains supported and unchanged. The GHCR image is an
additional distribution artifact, not an automatic migration of existing Docker or TrueNAS
installations. VB-054 publishes only the normal Linux architecture produced by the GitHub-hosted
runner; multi-architecture manifests remain VB-055.

### Supported deployment platforms

- Production is the repository Dockerfile running as a Linux container, built from source or pulled
  from GHCR after a release image is actually published and verified.
- Docker-based TrueNAS SCALE users can install the accepted Community App from Discover Apps. The
  source-built Custom App workflow remains available in [`README_TRUENAS.md`](../README_TRUENAS.md).
- Docker Desktop on Windows or macOS may run the Linux container. Native Windows is a development
  and test environment, not a documented production deployment.
- The current GHCR workflow is single-architecture. Do not assume ARM64 or multi-architecture image
  support before VB-055 and manifest verification.

Release gates, RC validation, and the stable release procedure are tracked in
[`docs/RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).

## Docker configuration

The generic Compose file reads `.env`, validates application settings at startup, and uses this
deployment contract:

```text
host OBSIDIAN_VAULT_PATH  ->  container /vault
/vault/.obsidian-chatgpt-data  ->  configured SEMANTIC_DATA_PATH
host 127.0.0.1:API_PORT  ->  container port 8000
host PUID:PGID  ->  container process user and group
```

| `.env` variable | Default/example | Purpose and constraint |
|---|---|---|
| `API_KEY` | placeholder only | Required by protected routes; replace with a long random secret |
| `API_KEY_PREVIOUS` | empty | Optional old key accepted only during an operator-controlled rotation window |
| `RATE_LIMIT_ENABLED` | `true` | Enable the process-local protected-route limiter |
| `RATE_LIMIT_REQUESTS` | `120` | Positive requests allowed per peer in one fixed window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Positive fixed-window duration in seconds |
| `RATE_LIMIT_MAX_CLIENTS` | `1024` | Positive hard cap on process-local peer state |
| `MCP_HTTP_ENABLED` | `false` | Opt in to Streamable HTTP at `/mcp` on the existing application port |
| `MCP_WRITE_ENABLED` | `false` | Add MCP `create_note` and `append_note`; the default nine-tool surface remains read-only |
| `MCP_HTTP_ALLOWED_HOSTS` | loopback hosts with any port | Comma-separated Host allowlist enforced by the MCP SDK; external hosts must be explicit |
| `MCP_HTTP_ALLOWED_ORIGINS` | loopback HTTP origins with any port | Comma-separated allowlist for a present Origin; external origins must be explicit |
| `OBSIDIAN_VAULT_PATH` | `/path/to/your/Obsidian/Vault` | Required absolute host path to the vault |
| `API_PORT` | `8765` | Host loopback port mapped to container port `8000` |
| `PUID` / `PGID` | `1000` / `1000` | Numeric container user/group used for bind-mounted files |
| `MAX_NOTE_BYTES` | `1000000` | Positive maximum note size |
| `SEMANTIC_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Non-empty local model name |
| `SEMANTIC_CHUNK_CHARS` | `600` | Integer of at least `250` |
| `SEMANTIC_CHUNK_OVERLAP` | `100` | Non-negative and at most half the chunk size |
| `SEMANTIC_INDEX_BATCH_SIZE` | `25` | Positive maximum notes committed per indexing transaction |
| `SEMANTIC_WATCH_ENABLED` | `false` | Opt in to recursive external Markdown change watching |
| `SEMANTIC_WATCH_DEBOUNCE_SECONDS` | `1.0` | Positive coalescing window for editor/synchronizer event bursts |

Compose fixes the application-only `VAULT_PATH` to `/vault` and `SEMANTIC_DATA_PATH` to
`/vault/.obsidian-chatgpt-data`; do not put host paths in either setting. Invalid numeric values or
empty paths/model names stop application startup. `OBSIDIAN_VAULT_PATH`, `API_PORT`, `PUID`, and
`PGID` are Compose inputs and are not read by the Python application.

Experimental application settings `SEMANTIC_EMBED_BATCH_SIZE` (default `4`) and
`SEMANTIC_ONNX_CPU_MEM_ARENA` (default `true`) control inference batch size and the ONNX CPU memory
arena. The checked-in generic Compose file does not forward them. Supply an explicit `environment`
override to change them; adding them only to `.env` has no effect.

### Rotate the API key safely

`API_KEY` is always the required current credential. `API_KEY_PREVIOUS` is optional; when it is
non-empty, protected legacy and `/api/v1` routes accept either key with the unchanged
`Authorization: Bearer <key>` header. It does not replace a missing `API_KEY`.

Use this operator-controlled rotation procedure:

1. Generate a new long random key without printing or storing it in repository files.
2. Edit the untracked `.env` so the new key is current and the old key is previous:

   ```env
   API_KEY=<new-current-key>
   API_KEY_PREVIOUS=<old-key-during-rotation>
   ```

3. Run `docker compose up -d` to recreate the service with both keys accepted.
4. Migrate every client to the new `API_KEY` and verify its protected requests.
5. Remove `API_KEY_PREVIOUS` or set it to empty, then run `docker compose up -d` again.

The application does not expire, generate, persist, or hot-reload keys. The previous key remains
valid until the operator removes it and recreates/restarts the service. Keep both values out of logs,
support output, shell history, and version control.

### Tune process-local rate limiting

VaultBridge limits protected legacy and `/api/v1` note/search requests by the ASGI peer address.
The default allows `120` requests per `60` seconds for each peer and retains at most `1024` peer
windows. `GET /health`, `GET /health/live`, `GET /health/ready`, and the public schema-hidden
`/privacy` route are exempt. A rejected request returns HTTP `429` with
`{"detail":"Rate limit exceeded"}` and an integer `Retry-After` value for the remaining fixed
window.

The limiter is deliberately in-process: state is not persisted, is reset by a restart, and is not
shared across multiple workers or application instances. It does not provide a distributed quota.
VaultBridge uses the direct ASGI peer address and does not trust `X-Forwarded-For`, `X-Real-IP`, or
`Forwarded`. Behind a reverse proxy the peer is commonly the proxy itself, so multiple external
clients may share one bucket. VB-043 does not add trusted-proxy parsing; size and tune the shared
bucket for that deployment or disable it with `RATE_LIMIT_ENABLED=false` only when equivalent
protection exists elsewhere.

### Semantic-data persistence

The generic Compose deployment stores these derived files inside the mounted vault directory:

```text
/vault/.obsidian-chatgpt-data/semantic-index.sqlite3
/vault/.obsidian-chatgpt-data/models/
/vault/.obsidian-chatgpt-data/huggingface/
```

On the host, they are under `${OBSIDIAN_VAULT_PATH}/.obsidian-chatgpt-data`. This directory therefore
persists across container replacement and requires write permission for `PUID:PGID`. The SQLite
index and downloaded model cache are derived/disposable; Markdown files remain the source of truth
and should be the focus of backups. Do not edit SQLite manually. Use the supported stopped-service
maintenance commands below to inspect or rebuild the index.

Compose fixes FastEmbed/Hugging Face's internal `HF_HOME` to the `huggingface/` directory above, so
the configured non-root `PUID:PGID` uses the same writable derived-data mount instead of an unwritable
root-home cache. This is an internal container path and requires no additional `.env` setting.

The separate `/data` mount described in the TrueNAS guide is not part of generic Compose.

### External Obsidian and synchronization edits

Filesystem watching is disabled by default, preserving the previous startup/full-sync behavior. Set
`SEMANTIC_WATCH_ENABLED=true` to observe the mounted vault recursively for external Markdown creates,
changes, deletes, and renames from tools such as Obsidian or Syncthing. The default one-second
debounce coalesces event bursts and atomic-replace patterns before paths enter the existing targeted
semantic-index queue. Non-Markdown files, Obsidian internals, temporary artifacts, the semantic data
directory, and paths that fail vault containment checks are ignored.

The watcher is incremental, process-local functionality for one VaultBridge process. It does not
replace the authoritative startup full synchronization, which still reconciles changes made while
VaultBridge was stopped. It also does not replace normal Markdown backups. Disable the watcher if
the mounted filesystem does not reliably deliver native change notifications.

## First startup and health verification

The HTTP application starts without waiting for the whole vault to be indexed. A background semantic
synchronization then scans the vault and downloads the local embedding model if it is not cached.
Consequently, liveness can succeed before semantic readiness:

```bash
curl -fsS http://127.0.0.1:8765/health/live
curl -i http://127.0.0.1:8765/health/ready
curl -fsS http://127.0.0.1:8765/health
```

- `/health/live` means the HTTP process is alive and returns `200` with `{"ok":true}`.
- `/health/ready` means VaultBridge can serve its intended vault and semantic workload. It returns
  `503` with `{"ready":false}` during an initial build and `200` with `{"ready":true}` when usable.
- `/health` returns richer operator diagnostics, including lifecycle state, availability, indexer
  activity, counts, and the last successful full synchronization.

Poll readiness and inspect logs while the first index is built. Semantic functionality becomes
available after a successful initial index. A later startup synchronizes changed Markdown; when the
stored index signature is incompatible with the running model/chunk configuration, VaultBridge
automatically rebuilds the derived index from Markdown.

After readiness succeeds, test the preferred versioned API with a non-destructive request:

```bash
curl -fsS 'http://127.0.0.1:8765/api/v1/notes/list?limit=5' \
  -H 'Authorization: Bearer YOUR_API_KEY'
```

Replace `YOUR_API_KEY` with the value in `.env`. The API key protects note and search routes; health
probes are intentionally public. Unversioned note/search routes remain compatibility aliases, but
new integrations should use `/api/v1`.

If you changed `API_PORT`, replace `8765` in these commands with that value.

## Logs and basic troubleshooting

VaultBridge writes structured application events to the container log stream. Useful commands are:

```bash
docker compose ps
docker compose logs --tail 100
docker compose logs -f
docker compose restart
docker compose down
```

Common first-start checks:

- Readiness remains `503` while initial indexing or model download is still running; inspect
  `/health` and follow the logs rather than assuming container startup is blocked.
- Permission errors usually mean `PUID:PGID` cannot read/write the host vault or create
  `.obsidian-chatgpt-data`.
- Protected requests return the existing server configuration error when `API_KEY` is missing; an
  `API_KEY_PREVIOUS` value cannot become the primary credential. Missing, malformed, or unknown
  Bearer credentials return the existing generic authentication error.
- Invalid typed environment values stop startup; the container logs identify the configuration
  validation failure without exposing the API key.
- HTTP `429` means the process-local peer bucket is exhausted; honor `Retry-After` and check for an
  accidental client loop before raising the configured allowance.
- A first model download requires network access. Network/download failures appear in container logs
  and leave initial semantic readiness unavailable. Correct the problem, then run
  `docker compose restart` to schedule a new startup synchronization.

Avoid pasting a resolved `docker compose config` into support requests: depending on the Compose
version, it can include the resolved `API_KEY` and `API_KEY_PREVIOUS`. Never dump the complete
container environment.

## Updating and stopping

This deployment builds from the checked-out source. Stop serving and CLI writers,
back up Markdown, configuration and artifacts, then select an explicitly reviewed
release. For example, when updating an older legacy installation to v1.5.0:

```bash
docker compose stop
git fetch --tags
git checkout v1.5.0
docker compose up -d --build
docker compose ps
```

This recreates the application container as needed and is not a zero-downtime procedure. Markdown
remains authoritative. Ordinary compatible updates reuse the persisted semantic data; a change to
the model/chunk/index signature triggers the existing automatic rebuild logic.

`docker compose stop` stops the existing container so it can be started again. `docker compose down`
stops and removes the Compose container and network. Neither command removes the bind-mounted host
vault or `.obsidian-chatgpt-data`; deleting host files is a separate destructive action. Do not use
`docker compose down -v` as a cleanup shortcut, and never point a removal command at the vault.

## Networking and security

Generic Compose publishes `127.0.0.1:${API_PORT:-8765}:8000`, so VaultBridge is reachable only from
the Docker host by default. Keep this security-positive binding. For access from another device or
the public internet, place an HTTPS reverse proxy or VPN in front rather than changing the binding to
`0.0.0.0` merely for convenience. VaultBridge does not terminate TLS itself.

- use a strong random API key and never commit `.env`;
- never expose or publish the vault directory itself;
- use HTTPS, a VPN, or both for remote access;
- back up the authoritative Markdown vault;
- treat the semantic database/model cache as rebuildable derived data;
- remember that the built-in rate limiter is process-local and may aggregate clients behind a
  reverse proxy; it is not a distributed edge-protection service.

See [`SECURITY.md`](../SECURITY.md) for the security invariants.
