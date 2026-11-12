# v1.5.0 RC preparation and qualification runbook

Status: **source preparation only; v1.5.0-rc.1 is not published**. This implements only task 1 of
[VB-153](VB153_RELEASE_READINESS.md). No command in this document grants publication or deployment
authority. [Release notes](V150_RELEASE_NOTES.md) describe scope and accepted limits. Preserve all
historical v1.4.0 records, including [its runbook](V140_RC_RUNBOOK.md).

## Preparation and exact source gate

Complete `agent_finish.py`, separate fresh acceptance and an authorized merge first. Record the
resulting full reviewed merge SHA; neither this worktree's baseline nor a branch name identifies
the future release. Require successful **push/main** CI on that exact SHA: `python`, `docker` and
`dashboard-e2e`, including Ruff, full non-E2E/evaluation, compile, Chromium, Compose/build and MCP
dependency/stdio/HTTP smoke. Do not substitute pre-merge CI or the VB-153 baseline run.

Run sequentially from the reviewed merged checkout in PowerShell. Placeholders deliberately fail:

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    $output = & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command failed with exit code $LASTEXITCODE" }
    return $output
}
$reviewedSha = '<exact-reviewed-merged-40-hex-sha>'
$ciRun = '<exact-main-push-CI-run-id>'
if ($reviewedSha -cnotmatch '^[0-9a-f]{40}$' -or $ciRun -cnotmatch '^[0-9]+$') {
    throw 'Observed reviewed SHA and CI run required'
}
$repository = 'mrtrollex/VaultBridge'
$remote = 'https://github.com/mrtrollex/VaultBridge.git'
$tag = 'v1.5.0-rc.1'
function Assert-ReviewedCheckout {
    if (@(Invoke-Checked git -Arguments @('status', '--porcelain')).Count -ne 0) { throw 'Dirty checkout' }
    $headSha = (Invoke-Checked git -Arguments @('rev-parse', '--verify', 'HEAD^{commit}')).Trim()
    if ($headSha -cne $reviewedSha) { throw 'Reviewed source changed' }
}
Assert-ReviewedCheckout
$ci = (Invoke-Checked gh -Arguments @('run', 'view', $ciRun, '--repo', $repository,
    '--json', 'headSha,headBranch,event,status,conclusion,workflowName,url,jobs')) | ConvertFrom-Json
if ($ci.headSha -cne $reviewedSha -or $ci.headBranch -cne 'main' -or $ci.event -cne 'push' -or
    $ci.status -cne 'completed' -or $ci.conclusion -cne 'success' -or $ci.workflowName -cne 'CI') {
    throw 'Successful exact-main push CI required'
}
foreach ($name in @('python', 'docker', 'dashboard-e2e')) {
    $jobs = @($ci.jobs | Where-Object name -CEQ $name)
    if ($jobs.Count -ne 1 -or $jobs[0].conclusion -cne 'success') { throw "Required CI job: $name" }
}
Invoke-Checked git -Arguments @('fetch', '--no-tags', $remote, 'refs/heads/main') | Out-Null
Invoke-Checked git -Arguments @('merge-base', '--is-ancestor', $reviewedSha, 'FETCH_HEAD') | Out-Null
Invoke-Checked python -Arguments @('-m', 'pytest', '-q', 'tests/test_release_preparation.py', 'tests/test_named_image.py')
Invoke-Checked gh -Arguments @('release', 'list', '--repo', $repository, '--limit', '100')
$existing = @(Invoke-Checked git -Arguments @('ls-remote', '--tags', $remote, "refs/tags/$tag", "refs/tags/$tag^{}"))
if ($existing.Count -ne 0) { throw 'RC tag exists; STOP and investigate without reusing it' }
```

Also require native source/dependency comparison with retained VB-152 manifests and affected actual
Windows/Linux metadata checks. Unexplained native drift requires affected native suites. Preserve
the 13-case reference hash `9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de`
and its v1 byte/digest checks. Compare dependencies, Dockerfile, semantic/native owners and tests;
source equality cannot certify floating base-image/dependency/model downloads.

## Disposable named-image smoke (executable now, no publication)

Requires Docker Linux/amd64, Python 3.12+ with `requirements-dev.txt`, MCP SDK 2.x, access to local
system temporary storage and loopback ports. The host directory must be shareable with Docker.
No user roots, existing credentials or arbitrary mount arguments are accepted. Run from this repo.

```powershell
Invoke-Checked docker -Arguments @('build', '-t', 'vaultbridge:vb154-local', '.')
Invoke-Checked python -Arguments @('scripts/verify_named_image.py', '--local-image', 'vaultbridge:vb154-local',
    '--expected-revision', $reviewedSha, '--expected-version', '1.5.0-rc.1')
```

For an uncommitted preparation dry-run, record HEAD **plus a complete changed-source manifest**;
`--expected-revision` is only a baseline annotation in local mode. The runner pins the inspected
image ID for every container. Record the Docker build/base output and source hashes with its
receipt. Local mode verifies mechanics/runtime versions and never attests OCI revision or a
published immutable image. A later digest run requires a previously anonymously pulled image:

```powershell
$image = 'ghcr.io/mrtrollex/vaultbridge@sha256:<observed-64-hex-index-digest>'
Invoke-Checked python -Arguments @('scripts/verify_named_image.py', '--image', $image,
    '--expected-revision', $reviewedSha, '--expected-version', '1.5.0-rc.1')
```

The runner creates `vaultbridge-named-*` under the system temporary directory, with `default`,
`research` and a denied sentinel root. Each has synthetic `same.md` with independent exact bytes;
each is configured with a different `*-data` path. Indexing is explicitly disabled, and
`semantic_retrieval` is absent. No model/index/cache/watcher may be created. A single explicit
read/write bind at `/qualification`, `--user 568:568`, dropped capabilities and loopback ephemeral
port are used. The runner passes strict `KNOWLEDGE_SPACES_JSON` directly with `--env`, with
matching `VAULT_PATH=/qualification/default` and `SEMANTIC_DATA_PATH=/qualification/default-data`.
Synthetic temporary directories alone receive disposable access permissions; never copy this
permission setup to a real vault. Docker Desktop permission mediation differs from native Linux.

Expected successful matrix:

| Phase | Required result |
| --- | --- |
| Runtime metadata | Actual application entry point and MCP constant equal the requested version; HTTP initialization returns the same server version. |
| Read-only MCP HTTP | Exactly nine tools; both current/previous keys; qualified read/resource/Query; denied read fails. |
| REST | Missing/bad key 401; omission reads default only; explicit default/research keep equal paths distinct; federated list/search/Query return both. Unknown/denied/mixed selection fails safely; denied create and disabled semantic capability fail without deltas. |
| Dashboard/health/MCP fencing | `/ui`, nested assets/session, GET/POST/DELETE: 404/no-store and no cookie. Named health minimal; disabled default ready=false/503. Bad MCP Host/Origin and credentials fail. |
| Writable HTTP | Eleven MCP tools; REST research create/append/dedupe and MCP default create/append. Generated timestamp format is validated; every other created byte and exact append suffix are independently expected. Complete peer inventories stay unchanged. |
| Sequential stdio | Serving is stopped and removed first. Actual `python -m app.mcp_server`, qualified read/resource/Query, disabled write rejection, EOF and zero exit. |
| Sequential CLI | No serving/stdio writer. Default-only and explicit/federated Query; named maintenance exit 2; deterministic omitted v1 and explicit research v2 capture, promotion create and replay, retained source, matching provenance and no peer mutation. |
| Restart/teardown | Same synthetic Markdown survives restart; normal stop exits zero; no model/index data; every owned container and the exact temporary root removed. |

PASS is printed only after cleanup. Every mounted HTTP/stdio/CLI/metadata invocation and permissions
cleanup worker has a unique per-run name registered before Docker dispatch, plus a run-ownership
label. Cleanup verifies ownership, stops running containers and removes their verified IDs, then
requires a successful daemon inventory confirming absence before deleting the host mount. Inventory
probes are bounded to 15 seconds; stop/removal commands to 45 seconds (30-second stop grace).
Timeout, unknown daemon state or failed cleanup is STOP/nonzero: the residual name and preserved
mount are reported, with no automatic retry or successful-teardown claim. Cleanup of failed runs
does not turn the original failure into clean shutdown evidence. After host interruption, inspect
only the recorded run's `vaultbridge-named-*` names, ownership labels and mounted paths. Stop/remove
only those verified IDs and require an authoritative absence check before manual root removal.
The resolved root must be a non-symlink direct child of system temporary storage with this prefix.
Never delete by wildcard or remove mounts while a writer may still exist. Keep sanitized logs/receipt outside
the disposable mount; no secret/environment dump. No automatic uncertain-write retries.

## Configuration delivery outside the runner

Dockerfile copies application code and launches Uvicorn; settings read process environment at
startup. Generic `docker-compose.yml` uses an explicit environment allowlist, mounts one host vault
at `/vault`, and stores derived state inside `/vault/.obsidian-chatgpt-data`. It has no
`KNOWLEDGE_SPACES_JSON` entry and no `env_file`: adding JSON to `.env` alone does not forward it.
This preparation changes neither Compose nor the TrueNAS catalog.

A concrete supported manual Docker layout uses a private env file and explicit mounts, rather than
combining generic Compose's `/vault` mount with incompatible named defaults. Provision disjoint
host directories and ownership/ACLs for the chosen non-root UID/GID first. For a **synthetic** Linux
qualification area only, with the placeholders replaced by separately provisioned absolute paths:

```bash
docker run --detach --name vb150-disposable --user 568:568 --cap-drop ALL \
  --publish 127.0.0.1:18765:8000 --env-file /absolute/disposable/named.env \
  --mount type=bind,src=/absolute/disposable/default,dst=/vault/default \
  --mount type=bind,src=/absolute/disposable/research,dst=/vault/research \
  --mount type=bind,src=/absolute/disposable/default-data,dst=/data/default \
  --mount type=bind,src=/absolute/disposable/research-data,dst=/data/research \
  --mount type=bind,src=/absolute/disposable/cache,dst=/cache \
  "$IMAGE"
```

Set `IMAGE` to the qualified immutable digest; local testing may explicitly use a recorded local
image ID. `named.env` must contain `API_KEY=<synthetic-key>`, `VAULT_PATH=/vault/default`,
`SEMANTIC_DATA_PATH=/data/default`, `HF_HOME=/cache`, `SEMANTIC_WATCH_ENABLED=false`, explicit MCP
HTTP/write/Host/Origin settings if needed, and the one-line JSON from
[KNOWLEDGE_SPACES.md](KNOWLEDGE_SPACES.md#configuration-and-composition). The guide's two-space
example exactly matches these container mounts. Protect the env file; do not log it. No shell
`export` prefix or extra quoting belongs in Docker env-file values. Stop/remove this instance
before stdio or CLI containers use these same mounts. TrueNAS manual `env_file` delivery has its
own paths/overrides; the Community App form does not imply named settings/mount support.

## Actual semantic/model/index qualification — separate required gate

The runner above intentionally cannot satisfy this gate. On the exact candidate digest, repeat
the manual disposable layout with **both** JSON records `indexing: enabled`, add
`semantic_retrieval` to both capability arrays and set `SEMANTIC_WATCH_ENABLED=true`. Record model
ID/revision/cache inputs, CPU/platform, installed package inventory and the resolved base image.
Use only synthetic `same.md` plus distinct domain text in each root. Bound every readiness/query
poll to 1,200 seconds; HTTP/command failure or deadline is STOP, never a skipped PASS.

1. Start the isolated container; require `/health/live` 200 and eventual `/health/ready` 200 with
   `{"ready":true}`. This establishes default readiness only.
2. With the synthetic Bearer key, POST `/api/v1/notes/related` with
   `{"spaces":["default","research"],"text":"Independent synthetic notes","min_score":-1,"limit":20}`.
   Require complete coverage of both spaces and qualified results from both roots. Repeat with
   each singular array; no peer identity may appear. POST `/api/v1/knowledge/query` with the same
   `spaces` and `semantic_text`, and require both identities/complete coverage. Record actual
   scores; do not claim cross-BLAS bitwise identity from a model run.
3. Externally create only `research/Watcher.md` with a unique synthetic phrase. Poll the same scoped
   semantic Query until `research/Watcher.md` appears. Require default-only results and all default
   Markdown hashes unchanged. Record observed watcher/reconciliation completion; watcher startup
   alone is insufficient. Include a denied/index-disabled sentinel record on a third disjoint
   mount and prove its derived path remains absent; it must never appear in results.
4. Stop cleanly (`docker stop --timeout 30`, inspect exit code 0), inventory derived SQLite/cache
   hashes, then start the **same digest/config/mounts**. Require readiness and the same scoped
   retrieval results, preserved Markdown and evidence of compatible index reuse. Run stopped-service
   persisted CLI related/Query only after removing the serving container. Run named stdio alone,
   verifying semantic Query on both owners and clean EOF. Preserve the native accepted fault suites
   by manifest comparison; do not simulate cleanup safety with process death.
5. Remove containers, then only the recorded disposable mounts/cache/env after verifying their
   boundaries and no live writers. Record zero-exit shutdown, absence/cleanup and any unexpected
   data/config changes. This container gate still does not certify TrueNAS lifecycle or a deployment.

Use the executable runner for nonsemantic requests/writes and exact file-delta assertions; retain
request/response JSON, poll deadlines, source/image identities and hashes for this additional gate.
The subsequent separately authorized task-3 canary must test actual stop/start persistence,
legacy-v1.4.0 upgrade on its own copy and demonstrated restore to an isolated prior layout.

## Future RC publication and immutable-image qualification (authorization required)

After task-1 acceptance and exact-source CI, present concrete SHA, tag, notes, expected alias
effects and rollback limits for explicit authorization covering GitHub **and** GHCR publication.
Only that later task may create a new annotated `v1.5.0-rc.1` at the frozen SHA and a non-draft
GitHub prerelease using `docs/V150_RELEASE_NOTES.md`. Recheck clean checkout/tag absence immediately
before mutation; independently fetch/dereference after tag push and after Release creation.
Never move/reuse a published identity. Workflow recovery is only for an already published matching
tag/SHA/`expected_prerelease=true` tuple, not an unpublished build mechanism.

The following later-task commands are **not authorized by VB-154**. After explicit RC publication
authority, continue the same checked PowerShell session and retain every output/readback:

```powershell
Assert-ReviewedCheckout
$localTags = @(Invoke-Checked git -Arguments @('for-each-ref', '--format=%(refname)', "refs/tags/$tag"))
$remoteTags = @(Invoke-Checked git -Arguments @('ls-remote', '--tags', $remote, "refs/tags/$tag", "refs/tags/$tag^{}"))
$releaseTags = @(Invoke-Checked gh -Arguments @('api', '--paginate', "repos/$repository/releases", '--jq', '.[].tag_name'))
if ($localTags.Count -ne 0 -or $remoteTags.Count -ne 0 -or $releaseTags -ccontains $tag) {
    throw 'Existing RC identity; STOP without moving or overwriting it'
}
Invoke-Checked git -Arguments @('tag', '-a', $tag, $reviewedSha, '-m', "VaultBridge $tag")
$localCommit = (Invoke-Checked git -Arguments @('rev-parse', '--verify', "$tag^{commit}")).Trim()
if ($localCommit -cne $reviewedSha) { throw 'Local tag mismatch' }
Invoke-Checked git -Arguments @('push', $remote, "refs/tags/$tag")
function Assert-RemoteTag {
    Invoke-Checked git -Arguments @('fetch', '--no-tags', $remote, "refs/tags/$tag") | Out-Null
    $type = (Invoke-Checked git -Arguments @('cat-file', '-t', 'FETCH_HEAD')).Trim()
    $commit = (Invoke-Checked git -Arguments @('rev-parse', '--verify', 'FETCH_HEAD^{commit}')).Trim()
    if ($type -cne 'tag' -or $commit -cne $reviewedSha) { throw 'Remote annotated tag mismatch' }
}
Assert-RemoteTag
Assert-ReviewedCheckout
Invoke-Checked gh -Arguments @('release', 'create', $tag, '--repo', $repository, '--verify-tag',
    '--prerelease', '--title', "VaultBridge $tag", '--notes-file', 'docs/V150_RELEASE_NOTES.md')
$release = (Invoke-Checked gh -Arguments @('release', 'view', $tag, '--repo', $repository,
    '--json', 'tagName,isDraft,isPrerelease,publishedAt,url')) | ConvertFrom-Json
if ($release.tagName -cne $tag -or $release.isDraft -ne $false -or $release.isPrerelease -ne $true -or
    [string]::IsNullOrWhiteSpace($release.publishedAt)) { throw 'Published prerelease mismatch' }
Assert-RemoteTag
```

Save observed alias digests before this block; after publication compare them as described below.
If any step fails, STOP without completing later mutations. Do not use a mutable `target_commitish`
or a workflow dispatch SHA as a substitute for the dereferenced release-source tag.

Require `Verify release source` and `Build and publish` success and `Publish stable aliases`
**skipped**. Save pre/post digests of `1.4.0`, `1.4`, `1`, `latest` and require no RC movement.
Record annotated tag object/commit, Release flags, workflow source/checkout, OCI index,
linux/amd64 runtime and attestation digests and source/revision/version/license labels. Anonymous
pull with an empty Docker credential config must succeed. Run on the observed index digest:

```powershell
Invoke-Checked docker -Arguments @('buildx', 'imagetools', 'inspect', $image)
Invoke-Checked python -Arguments @('scripts/verify_release_image.py', '--image', $image,
    '--expected-revision', $reviewedSha, '--expected-version', '1.5.0-rc.1')
Invoke-Checked python -Arguments @('scripts/smoke_mcp_http.py', '--image', $image)
Invoke-Checked python -Arguments @('scripts/verify_named_image.py', '--image', $image,
    '--expected-revision', $reviewedSha, '--expected-version', '1.5.0-rc.1')
```

The legacy verifier performs real model/index checks on its legacy layout; it does not qualify
named model/index behavior. Require the additional named semantic gate above. Record exact
inventory/runtime versions and all logs/cleanup receipts. A green build or local tag is insufficient.

## Rollback, stable transition and STOP boundaries

Before an authorized canary, save synthetic Markdown/config/approval artifacts and prior digest;
prove restore to a separate prior-image/layout copy with all writers stopped. Remove named config
for legacy rollback and preserve v2 artifacts separately; do not feed them to v1.4.0 as migration
input. Derived bound SQLite paths cannot be transplanted/rebound; rebuild compatible derived data
from preserved Markdown when appropriate. No in-place multi-root atomic rollback is promised.

Stable `1.5.0` requires a separate reviewed metadata/documentation transition unless new evidence
requires bounded repairs and requalification. After its exact-SHA CI and successful RC/image/canary
receipts, obtain separate stable publication authority. The stable workflow rebuilds and advances
`1.5`, `1`, `latest` before post-publication checks. Require all three publication jobs, exact stable
digest legacy/named/MCP gates and restart/data checks. Compare resolved base/dependencies/model
inputs; reuse an RC canary only with demonstrated source/runtime equivalence, otherwise perform a
bounded stable-digest canary. Preserve `1.4.0`/`1.4`. Never silently retag, republish or roll aliases
back; failed post-publication gates stop rollout/announcement pending authorized recovery.

At every phase STOP on identity/version disagreement, dirty/unreviewed release content, failed or
required-but-unavailable check, unexplained dependency/native drift, cross-space access, unexpected
bytes, uncertain write, leaked secret, missing permission, ambiguous target or incomplete cleanup.
Accepted ADR 0010 opaque-release limits remain; forced close/retry and universal stopping completion
are not release criteria. Public catalog install/edit/reboot/upgrade/rollback/uninstall and ixVolume
retention/removal remain separate external gates. No production or real-vault use is authorized.
