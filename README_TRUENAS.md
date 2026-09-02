<p align="center">
  <a href="README.md"><img src="docs/assets/vaultbridge-logo.webp" alt="VaultBridge logo" width="180"></a>
</p>

# VaultBridge on TrueNAS SCALE

This runbook documents the existing source-built deployment on the Docker-based Apps implementation
in TrueNAS SCALE 24.10 or later. It is independent of the generic Docker workflow in
the [main VaultBridge README](README.md).

Versioned GHCR images are an additional distribution artifact. VB-054 does not migrate or replace
this known-working TrueNAS source-build deployment.

> **Current availability:** this guide remains the supported Docker/source-built TrueNAS Custom App
> path. A release-gated Community App definition is being developed under
> `ix-dev/community/vaultbridge/`, but it has not been submitted or accepted upstream and does not
> replace this workflow. VaultBridge is not currently available as an upstream catalog app; do not
> search Discover Apps for it yet.

The installation intentionally retains older identifiers. `ObsidianChatGPT*`, `obsidian-api`, and
`obsidian-chatgpt` are compatibility names from the working installation, not the public product
name. Do not rename them merely to match VaultBridge.

## Deployment contract

```text
/mnt/Apps/AppsData/ObsidianChatGPT      -> Docker build context
/mnt/Apps/AppsData/ObsidianVault       -> /vault (authoritative Markdown)
/mnt/Apps/AppsData/ObsidianChatGPTData -> /data  (derived semantic state/cache)
TrueNAS host port 8765                 -> container port 8000
container process                      -> UID:GID 568:568
Compose service                        -> obsidian-api
container                              -> obsidian-chatgpt
```

`/vault` is the source of truth. `/data` holds `semantic-index.sqlite3`, FastEmbed's model files, and
the Hugging Face cache under `/data/huggingface`; it is disposable and rebuildable from Markdown. The
container fixes `HF_HOME` to that writable derived-data path for UID/GID `568:568`. Do not sync
`/data` through Obsidian.

The primary lifecycle owner in this guide is a TrueNAS **Custom App installed via YAML**. The small
[`truenas-install.yml`](truenas-install.yml) file points the TrueNAS-managed app at
[`compose.truenas.yml`](compose.truenas.yml):

```yaml
include:
  - /mnt/Apps/AppsData/ObsidianChatGPT/compose.truenas.yml
```

Current TrueNAS documentation calls this path **Apps > Discover > Install via YAML** and calls the
YAML field **Custom Config**. See the
[TrueNAS Custom App screen reference](https://www.truenas.com/docs/scale/apps/installcustomappscreens/).
UI wording can vary by TrueNAS release; this guide does not invent version-specific click sequences.

Prerequisites:

- a Docker-based TrueNAS SCALE release with an Apps pool configured;
- shell or SSH access for the reproducible commands below;
- enough space for the source, Docker image, semantic index, and local model cache;
- internet access for the initial image build and first model download.

## Fresh installation

### 1. Prepare storage and source

Create datasets where that is your normal TrueNAS storage policy, or create the directories from a
TrueNAS shell:

```bash
mkdir -p /mnt/Apps/AppsData/ObsidianChatGPT
mkdir -p /mnt/Apps/AppsData/ObsidianVault
mkdir -p /mnt/Apps/AppsData/ObsidianChatGPTData
```

The vault directory can already contain an Obsidian vault. Do not copy VaultBridge files into it.

Install the source at the compatibility path. If Git is intentionally available on the NAS:

```bash
git clone https://github.com/mrtrollex/VaultBridge.git \
  /mnt/Apps/AppsData/ObsidianChatGPT
cd /mnt/Apps/AppsData/ObsidianChatGPT
```

If the production NAS does not use Git, use the supported bundle workflow described in
[Bundle and no-Git updates](#bundle-and-no-git-updates). Git is not required on TrueNAS.

### 2. Create `.env` and the API key

The TrueNAS Compose file loads `/mnt/Apps/AppsData/ObsidianChatGPT/.env` through `env_file`. The file
contains the secret `API_KEY` and may temporarily contain `API_KEY_PREVIOUS`; it must not be
committed, included in an update archive, pasted into support logs, or exposed through an
environment dump.

```bash
cd /mnt/Apps/AppsData/ObsidianChatGPT
cp .env.truenas.example .env
docker run --rm python:3.12-slim python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Copy the generated value into `API_KEY` in `.env` using a local editor. Do not put a real key in
Compose or documentation. Restrict access to `.env` according to the ACL model used on the source
dataset.

`API_KEY`, optional `API_KEY_PREVIOUS`, and the process-local rate-limit settings come from `.env`
in this deployment. The example defaults are:

```text
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS=120
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_MAX_CLIENTS=1024
SEMANTIC_WATCH_ENABLED=false
SEMANTIC_WATCH_DEBOUNCE_SECONDS=1.0
```

`compose.truenas.yml` directly fixes the application settings that must match the mounts and current
semantic index:

```text
VAULT_PATH=/vault
MAX_NOTE_BYTES=1000000
SEMANTIC_DATA_PATH=/data
SEMANTIC_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
SEMANTIC_CHUNK_CHARS=600
SEMANTIC_CHUNK_OVERLAP=100
SEMANTIC_INDEX_BATCH_SIZE=25
```

Avoid casual use of `docker compose config`: resolved output can contain both API keys from `.env`.
Never paste resolved Compose output or the complete container environment into a support request.

#### Rotate the API key safely

The current `API_KEY` remains mandatory. A non-empty `API_KEY_PREVIOUS` temporarily allows the old
credential on the same protected legacy and `/api/v1` routes; both use
`Authorization: Bearer <key>`. The previous key cannot replace a missing current key.

1. Generate a new long random key.
2. Edit the protected, untracked `.env` with placeholders representing the new and old values:

   ```env
   API_KEY=<new-current-key>
   API_KEY_PREVIOUS=<old-key-during-rotation>
   ```

3. Redeploy/restart the TrueNAS Custom App so both keys are loaded.
4. Migrate every client to the new key and verify a protected request.
5. Remove `API_KEY_PREVIOUS` or leave it empty, then redeploy/restart again.

There is no automatic expiry or hot reload. The old key remains accepted until the final
redeploy/restart, so keep the overlap as short as practical.

#### Tune process-local rate limiting

Protected legacy and `/api/v1` note/search traffic uses a fixed-window bucket keyed by the direct
ASGI peer address. Health probes and `/privacy` are exempt. Exceeding the allowance returns HTTP
`429`, `{"detail":"Rate limit exceeded"}`, and `Retry-After` for the remaining window.

The limiter is held only in one VaultBridge process; restart clears it and multiple workers do not
share it. Forwarded client-address headers are deliberately ignored. With the documented reverse
proxy pattern, the proxy may be the peer, so multiple external clients can consume the same bucket.
Tune `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS`, and `RATE_LIMIT_MAX_CLIENTS` for that shared
traffic, or set `RATE_LIMIT_ENABLED=false` only when equivalent upstream protection is intentional.

#### Watch external vault edits

`SEMANTIC_WATCH_ENABLED=false` preserves the existing disabled behavior. Set it to `true` in `.env`
and redeploy/restart the Custom App to observe recursive Markdown creates, changes, deletes, and
renames made by Obsidian or Syncthing. `SEMANTIC_WATCH_DEBOUNCE_SECONDS=1.0` coalesces their common
event bursts before using the existing targeted semantic queue. The watcher ignores non-Markdown,
Obsidian-internal, temporary, semantic-data, escaping, and unsafe symlink paths.

This is one-process, local incremental freshness. Startup full synchronization still reconciles
changes made during downtime, and watching is not a substitute for backing up the authoritative
Markdown dataset. Leave it disabled if the dataset/mount does not deliver reliable native filesystem
notifications to the container.

### 3. Grant deliberate dataset access

Compose runs the container as numeric UID/GID `568:568`. Every parent directory and ACL on the two
mount paths must let that identity traverse the path, and:

- `/mnt/Apps/AppsData/ObsidianVault` needs read access for indexing and read/write access for enabled
  create/append operations;
- `/mnt/Apps/AppsData/ObsidianChatGPTData` needs read/write access for SQLite and the model cache.

TrueNAS installations can use POSIX ownership, NFSv4 ACLs, or both. The repository does not encode a
universal ACL entry, so use the dataset's existing ACL model and verify the effective access for
numeric `568:568`. Do not use `chmod 777`, and do not recursively strip or replace an existing vault
ACL merely to make the container start.

Typical permission symptoms are:

- `/health/ready` remains `503` and `/health` reports the vault unavailable;
- create/append requests fail even though reads work;
- initial indexing fails or the semantic SQLite index cannot persist under `/data`.

### 4. Audit the deployment file

The checked-in `compose.truenas.yml` must retain this contract unless the local datasets genuinely
differ:

| Compose field | Required value |
|---|---|
| service | `obsidian-api` |
| build context | `.` relative to the included Compose file |
| container | `obsidian-chatgpt` |
| restart policy | `unless-stopped` |
| runtime user | `568:568` |
| environment file | `.env` |
| vault mount | `/mnt/Apps/AppsData/ObsidianVault:/vault` |
| semantic mount | `/mnt/Apps/AppsData/ObsidianChatGPTData:/data` |
| port | `8765:8000` |

If local host paths are different, change only the host side of the two volume mappings and the
absolute path in `truenas-install.yml`. Keep `/vault`, `/data`, the service name, and the container
name stable.

The Dockerfile builds from `python:3.12-slim`, installs `libgomp1` for ONNX Runtime, uses `/app` as
its work directory, exposes port `8000`, and starts
`uvicorn app.main:app --host 0.0.0.0 --port 8000`.

### 5. Install the TrueNAS-managed Custom App

In the Docker-based TrueNAS Apps UI:

1. Open **Apps > Discover > Install via YAML**.
2. Choose a valid local app name. This UI name is separate from the required Compose service and
   container identifiers.
3. Put the contents of `truenas-install.yml` in **Custom Config**.
4. Save to deploy the app.

TrueNAS reads the external Compose file, builds the source at
`/mnt/Apps/AppsData/ObsidianChatGPT`, and owns the resulting application lifecycle. Do not also run
`docker compose up` for the same deployment; that can create a duplicate stack/container outside
TrueNAS management.

## First startup and verification

Startup is asynchronous:

```text
container starts
  -> FastAPI/Uvicorn begins serving
  -> background full synchronization starts
  -> FastEmbed may download the local model into /data
  -> semantic search becomes ready later
```

Container startup does not wait for the model download or full vault indexing. An empty model cache
needs internet access on first use. Follow logs and poll readiness rather than waiting for the
container start action itself to finish indexing.

From the TrueNAS host:

```bash
curl -fsS http://127.0.0.1:8765/health/live
curl -i http://127.0.0.1:8765/health/ready
curl -fsS http://127.0.0.1:8765/health
```

- `/health/live` returns `200` with `{"ok":true}` when the API process is alive. It does not inspect
  the vault, model, database, or indexer.
- `/health/ready` returns `200` with `{"ready":true}` only when the vault is usable and semantic
  search is available. Initial indexing, missing storage, or expected permission/SQLite failures
  return `503` with `{"ready":false}`.
- `/health` returns richer vault, lifecycle, search-availability, background-work, count, and
  last-successful-sync diagnostics.

Health routes are public and unversioned. Do not use `/api/v1/health`.

After readiness succeeds, make a non-destructive authenticated request from the TrueNAS host:

```bash
(
  set -a
  . /mnt/Apps/AppsData/ObsidianChatGPT/.env
  set +a
  curl -fsS 'http://127.0.0.1:8765/api/v1/notes/list?limit=5' \
    -H "Authorization: Bearer $API_KEY"
)
```

For a LAN client, replace `127.0.0.1` with `TRUENAS_IP`. The subshell loads the key without placing
its value in shell history and discards the variable afterward. New integrations should prefer
`/api/v1`. Unversioned note/search routes remain compatibility aliases for existing clients.

## Logs and first-response troubleshooting

Two operator paths are supported:

- in TrueNAS, select the installed app and use the Workloads **View Logs** action where available;
- from a TrueNAS shell, follow the actual container name:

```bash
docker logs --tail 100 obsidian-chatgpt
docker logs --follow obsidian-chatgpt
```

VaultBridge-owned events are UTF-8 JSON objects, one per line. Uvicorn server/access records are
separate and can remain plain text, so a combined container stream can contain both formats. Every
HTTP response carries a server-generated `X-Request-ID`; the same ID appears on VaultBridge events
emitted while that request is handled. Background index work does not inherit a request ID.

VaultBridge JSON events intentionally exclude API keys, Authorization headers, note content,
embedding/query text, exception messages, and absolute host paths. Do not dump environment variables
while troubleshooting.

When readiness is `503`:

1. Inspect `/health` and logs; first startup may simply still be downloading/indexing.
2. If the vault is unavailable, correct its mount, path traversal, or `568:568` access.
3. If note writes fail, verify write access to `/vault`.
4. If semantic persistence/model setup fails, verify write access and free space on `/data`, plus
   internet access for an empty model cache.
5. Use offline `index check` only after the app is stopped if diagnostics indicate an index problem.

Do not assume every `503` needs a rebuild, and do not delete SQLite files manually.

## Backup before an update

Back up according to what each location means:

| Data | Role | Backup priority |
|---|---|---|
| `/mnt/Apps/AppsData/ObsidianVault` | authoritative Markdown | required by the normal vault backup policy |
| source `.env` and deployment YAML | secret and operational configuration | preserve securely before update |
| source directory | application rollback convenience | optional but recommended before a risky source update |
| `/mnt/Apps/AppsData/ObsidianChatGPTData` | derived SQLite index/model cache | not required for Markdown safety; rebuildable |

The external `/vault` and `/data` datasets must never be overwritten by source extraction. A source
backup can make runtime rollback faster, but it is not a substitute for backing up Markdown.

## Updating the TrueNAS-managed app

This sequence updates source/image without starting a second Compose stack. Run the image lookup
while the app is still running:

```bash
cd /mnt/Apps/AppsData/ObsidianChatGPT
app_image=$(docker inspect obsidian-chatgpt --format '{{.Config.Image}}')
test -n "$app_image"
printf 'Current managed image: %s\n' "$app_image"
```

Then:

1. Back up the items described above.
2. Stop the app from the TrueNAS Apps UI. Confirm that
   `docker ps --filter name=obsidian-chatgpt --format '{{.Names}}'` prints nothing.
3. Update the source with the Git command below or the no-Git bundle procedure, preserving `.env`:

   ```bash
   cd /mnt/Apps/AppsData/ObsidianChatGPT
   git pull --ff-only
   ```

   Skip this command when the NAS intentionally has no Git repository.
4. Rebuild the exact image tag already referenced by the TrueNAS-managed app:

   ```bash
   cd /mnt/Apps/AppsData/ObsidianChatGPT
   docker build --tag "$app_image" .
   ```

5. If `compose.truenas.yml` or `truenas-install.yml` changed, edit the installed app YAML and save so
   TrueNAS rereads the include. For an application-source-only update, keep the existing YAML.
6. Start the app from the TrueNAS Apps UI.
7. Verify `/health/live`, then poll `/health/ready` and inspect `/health` and logs.

The image lookup variable exists only in that shell session. Record the non-secret image tag before
stopping if the session will be closed. Do not use `docker compose up` to restart a TrueNAS-managed
app.

Ordinary compatible updates reuse `/data`. If model, chunking, or other semantic signature settings
become incompatible, startup automatically invalidates and rebuilds the derived semantic data from
Markdown. This is different from an operator-requested `index rebuild`; do not rebuild manually after
every application upgrade.

## Bundle and no-Git updates

[`make-bundle.ps1`](make-bundle.ps1) is the established source-bundle helper. Run it on a Windows
workstation from a clean Git checkout:

```powershell
.\make-bundle.ps1
```

It uses `git archive HEAD` and creates `dist/VaultBridge-BRANCH-COMMIT.zip`. Only the committed
revision is exported; uncommitted changes are deliberately omitted after a warning. The archive has
no `.git/` metadata and normally excludes ignored/untracked local state such as:

```text
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
.env
```

Transfer the ZIP to a temporary location on TrueNAS. With the TrueNAS-managed app stopped, stage and
verify it before replacing the source directory:

```bash
stamp=$(date +%Y%m%d-%H%M%S)
stage="/mnt/Apps/AppsData/VaultBridge-stage-$stamp"
rollback="/mnt/Apps/AppsData/ObsidianChatGPT.rollback-$stamp"
mkdir -p "$stage"
unzip /path/to/VaultBridge-BRANCH-COMMIT.zip -d "$stage"
test -f "$stage/Dockerfile"
test -f "$stage/compose.truenas.yml"
test -f "$stage/truenas-install.yml"
test -f /mnt/Apps/AppsData/ObsidianChatGPT/.env
mv /mnt/Apps/AppsData/ObsidianChatGPT "$rollback"
mv "$stage" /mnt/Apps/AppsData/ObsidianChatGPT
cp -p "$rollback/.env" /mnt/Apps/AppsData/ObsidianChatGPT/.env
```

This leaves the previous source as a timestamped rollback copy and explicitly restores `.env`.
`/mnt/Apps/AppsData/ObsidianVault` and `/mnt/Apps/AppsData/ObsidianChatGPTData` are external and are
not part of the extraction target. Rebuild the managed image and start the app using the update
workflow above. Remove a rollback source copy only after the new deployment is accepted and its
contents have been checked.

For a fresh no-Git installation, extract the verified bundle directly into the empty compatibility
source directory, then create `.env` as described earlier:

```bash
unzip /path/to/VaultBridge-BRANCH-COMMIT.zip \
  -d /mnt/Apps/AppsData/ObsidianChatGPT
```

## Shell-managed Compose alternative

Use this only when the deployment was intentionally created and is managed entirely from the
TrueNAS shell. Do not run it alongside the TrueNAS-managed Custom App.

Fresh deploy or source/image update:

```bash
cd /mnt/Apps/AppsData/ObsidianChatGPT
docker compose -f compose.truenas.yml up -d --build obsidian-api
docker compose -f compose.truenas.yml ps
docker logs --tail 100 obsidian-chatgpt
```

Lifecycle commands for this shell-managed mode are:

```bash
docker compose -f compose.truenas.yml stop obsidian-api
docker compose -f compose.truenas.yml start obsidian-api
docker compose -f compose.truenas.yml restart obsidian-api
```

After an update, verify liveness and readiness exactly as in the primary workflow. Do not mix these
commands into the TrueNAS-managed app sequence.

## Offline semantic-index maintenance

VaultBridge has no cross-process semantic-index lock. Stop the API service before `status`, `index`,
`reindex`, `index check`, or `index rebuild`, and do not use `docker exec` against the running API
container.

For a TrueNAS-managed app, stop it in the Apps UI and confirm that the serving container is absent:

```bash
docker ps --filter name=obsidian-chatgpt --format '{{.Names}}'
```

Then run an isolated maintenance container. `--build` ensures the command uses the current source;
Compose supplies the same `.env`, `/vault`, `/data`, and semantic settings as the API service:

```bash
cd /mnt/Apps/AppsData/ObsidianChatGPT
docker compose -f compose.truenas.yml run --rm --no-deps --build \
  obsidian-api python -m app.cli index check
check_status=$?
printf 'index check exit: %s\n' "$check_status"
```

To bring a compatible derived index up to date without resetting it:

```bash
docker compose -f compose.truenas.yml run --rm --no-deps --build \
  obsidian-api python -m app.cli index
```

Review the check result before choosing a clean rebuild. If the issue is a corrupt/incompatible
derived index and the vault and permissions are healthy:

```bash
docker compose -f compose.truenas.yml run --rm --no-deps --build \
  obsidian-api python -m app.cli reindex
rebuild_status=$?
printf 'reindex exit: %s\n' "$rebuild_status"
```

`index` updates a compatible derived index without resetting it. `reindex` is equivalent to the
still-supported `index rebuild` command and performs a clean derived-data rebuild. Both use the
production synchronization path and leave Markdown unchanged.

Exit codes are stable:

| Code | Meaning |
|---|---|
| `0` | check is healthy or rebuild succeeded |
| `1` | integrity/readiness or operational rebuild problem |
| `2` | CLI, configuration, or programming failure |

Start the app again from the TrueNAS UI and verify liveness/readiness. For shell-managed Compose,
stop/start `obsidian-api` with the shell lifecycle commands instead.

`index check` is a stopped-service persisted view and does not load the model or modify semantic
storage. It refuses inspection when SQLite WAL/SHM sidecars indicate the storage is not safely
isolated. `/health` and `/health/ready` remain authoritative for the running process.

`index rebuild` explicitly clears and regenerates only derived embeddings/chunks through the
production full-sync path. It can download the model when `/data` has no cache, and it never changes
Markdown.

## Recovery decision flow

```text
/health/ready returns 503
  -> inspect /health and logs
  -> first model download/index still active? wait and monitor
  -> vault/mount/568:568 access problem? fix access, then restart
  -> /data access/free-space/network problem? fix it, then restart
  -> persisted semantic-index problem? stop the service, then run index check
  -> check confirms corrupt/incompatible derived state? optionally run index rebuild
  -> start the service and verify live, ready, and authenticated list
```

Do not treat every readiness failure as corruption, do not delete SQLite manually, and do not run
maintenance concurrently with the API.

## Restart, image rebuild, and index rebuild

These operations solve different problems:

| Operation | What changes | Use when |
|---|---|---|
| app restart | restarts the same code/image | retry startup after correcting external access/network issues |
| Docker image rebuild | packages new source and dependencies | source, requirements, or Dockerfile changed |
| semantic index rebuild | regenerates derived embeddings/chunks from Markdown | offline inspection justifies explicit semantic recovery |

A restart does not package new source. An image rebuild does not inherently erase `/data`. An index
rebuild does not update application code and does not modify `/vault` Markdown.

## Conservative rollback

There is no automatic VaultBridge rollback subsystem. For a source/image deployment failure:

1. Keep the TrueNAS-managed app stopped.
2. Leave `/mnt/Apps/AppsData/ObsidianVault` untouched.
3. Restore or point the compatibility source path back to the previous verified source and its
   preserved `.env`/deployment YAML.
4. Rebuild the managed image tag from that source.
5. Start the app through TrueNAS and verify health and the authenticated list request.

`/data` is derived. A rollback to code with a different semantic signature may trigger the normal
compatibility rebuild or require an explicit stopped-service rebuild after inspection. Do not promise
or assume an automatic application rollback.

## Networking, security, and ChatGPT compatibility

TrueNAS Compose publishes `8765:8000` without a loopback-only host address. VaultBridge is therefore
reachable according to the TrueNAS host interfaces, routing, and firewall configuration. Use
`127.0.0.1` only from the TrueNAS host; use `TRUENAS_IP` for LAN examples.

The API key authenticates protected routes but does not provide TLS. VaultBridge does not terminate
HTTPS. For remote or public access, put an HTTPS reverse proxy in front of it or use a VPN/private
network. This runbook does not add proxy configuration.

- protect `.env` and use a long random API key;
- never expose or share the `/vault` dataset as an API substitute;
- treat `/data` as derived state, not an authoritative backup;
- do not expose resolved secrets through logs, environment dumps, or Compose output;
- treat the built-in limiter as process-local loop/abuse protection, not a distributed or
  trusted-proxy-aware edge limiter.

Existing ChatGPT Actions continue to work through the unversioned compatibility routes and stable
operation IDs. New integrations should prefer `/api/v1`. HTTPS and public/private connectivity are
deployment concerns outside VaultBridge; this guide does not redesign the Action schema.

## Operational quick reference

Primary TrueNAS-managed Custom App:

| Task | Action |
|---|---|
| logs | Apps UI **View Logs**, or `docker logs --tail 100 obsidian-chatgpt` |
| liveness | `curl -fsS http://127.0.0.1:8765/health/live` |
| readiness | `curl -i http://127.0.0.1:8765/health/ready` |
| diagnostics | `curl -fsS http://127.0.0.1:8765/health` |
| stop/start/restart | use the Apps UI; restart means Stop, then Start |
| image rebuild | stop in UI, `docker build --tag "$app_image" .`, start in UI |
| index check | stop in UI, run the isolated `docker compose ... index check`, start in UI |
| index rebuild | stop in UI, rebuild only after diagnosis, start in UI |
| source update | backup, stop, update Git/bundle source, rebuild managed image, start, verify |

Shell-managed Compose only:

| Task | Command |
|---|---|
| stop | `docker compose -f compose.truenas.yml stop obsidian-api` |
| start | `docker compose -f compose.truenas.yml start obsidian-api` |
| restart | `docker compose -f compose.truenas.yml restart obsidian-api` |
| rebuild image/start | `docker compose -f compose.truenas.yml up -d --build obsidian-api` |

Always run commands from `/mnt/Apps/AppsData/ObsidianChatGPT`. Never start the shell-managed stack
when the TrueNAS-managed Custom App exists.
