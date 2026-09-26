# VaultBridge release checklist

This is the durable completed release evidence for VaultBridge. VB-056 established the original
`v1.0.0` audit, and each later release adds its own immutable record without replacing earlier tags,
GitHub Releases, or artifact evidence.

## `v1.3.0` release evidence

Release date: 2026-09-24. Independent verification date: 2026-09-25.

VaultBridge `v1.3.0` is a published stable, non-prerelease GitHub Release from source commit
`a7e14ece0de74632d1d9be599d53678931dc64b3`. The release workflow, immutable GHCR image,
stable aliases, OCI metadata, anonymous exact-digest pull, MCP smoke, and full functional gate were
independently verified after publication.

### Release scope

- VB-100 through VB-104 add contained Obsidian wikilink parsing/resolution, verified outgoing links
  and backlinks, shared REST/MCP relationship tools, and the dashboard Relationships UI.
- VB-105 adds separate graph-aware retrieval evaluation. Its evidence did not authorize or change
  production semantic ranking, model, chunking, or index/storage format.
- VB-106 adds default-off MCP `create_note` and `append_note` parity and first-class TrueNAS MCP
  configuration source.
- Existing REST compatibility and release-workflow behavior remain unchanged.

### Release gate ledger

| Gate | Status | Evidence |
|---|---|---|
| Source version alignment | **PASS** | `pyproject.toml`, FastAPI metadata, MCP server metadata, and the intentional application-version regression assertion all target `1.3.0`. |
| Stable GitHub Release | **PASS** | [`v1.3.0`](https://github.com/mrtrollex/VaultBridge/releases/tag/v1.3.0) is published, non-draft, non-prerelease, and its annotated tag resolves to the exact release source. |
| Release workflow | **PASS** | Run [`36019163750`](https://github.com/mrtrollex/VaultBridge/actions/runs/36019163750) completed successfully at the release source; `Verify release source`, `Build and publish`, and `Publish stable aliases` all passed. |
| OCI identity and aliases | **PASS** | Exact OCI index, `linux/amd64` runtime manifest, BuildKit attestation, platform, and required OCI labels match the release. Tags `1.3.0`, `1.3`, `1`, and `latest` all resolve to the recorded index. |
| Anonymous pull | **PASS** | The exact digest was pulled successfully with a new empty Docker configuration, without operator credentials. |
| Immutable-image MCP verification | **PASS** | The exact digest passed disabled HTTP, the seven-tool read-only surface, official-client Streamable HTTP sessions with both current and previous keys, invalid Bearer/Host/Origin rejection, structured outgoing-link/backlink calls, the write-enabled nine-tool surface, disposable create/append/deduplication, clean shutdown, and fail-visible disposable-root cleanup with an explicit absence assertion. |
| Immutable-image functional gate | **PASS** | The exact digest passed health, dashboard/assets/security headers, authenticated list/read/literal/semantic retrieval, real semantic readiness, relationship endpoints, CLI, restart persistence, privacy-safe logs, clean stop, and disposable cleanup. |
| Upstream TrueNAS delivery | **PARTIAL / SEPARATE LIFECYCLE** | At upstream `truenas/apps` master commit `f39f282e8a58fe57372ca0f005123c84b2ecee31`, package `1.0.2` selects application image `1.3.0`. The upstream source/generated form still lacks the four first-class MCP fields, so that UI delivery remains separate. No live TrueNAS upgrade/runtime validation is claimed here. |

### Exact release source and workflow

```text
GitHub Release: https://github.com/mrtrollex/VaultBridge/releases/tag/v1.3.0
annotated tag object: 72fdecb09790b1399773de53a0192cf878f13440
release source commit: a7e14ece0de74632d1d9be599d53678931dc64b3
release workflow: 36019163750
release workflow URL: https://github.com/mrtrollex/VaultBridge/actions/runs/36019163750
release workflow conclusion: success
```

The public GitHub API reported `draft=false`, `prerelease=false`, publication at
`2026-09-24T15:18:06Z`, and an annotated tag whose target object is the exact commit above. The
workflow run used that same `head_sha`; all three release jobs completed successfully.

### Published OCI evidence

```text
repository: ghcr.io/mrtrollex/vaultbridge
verified aliases: 1.3.0, 1.3, 1, latest
OCI index digest: sha256:5a1709c279c3731f891b59026adb7e8f5497c299687596b74b49ffd64a9f5a0e
linux/amd64 runtime-manifest digest: sha256:13d41a301b9a43a460227c55ecc2e3244bd7ca3036301a1f405d60cbd08ebec3
BuildKit provenance attestation digest: sha256:e4a25b57865abfea0d39264c018036fbc571666e25145fa2a86662641b794094
platform: linux/amd64
org.opencontainers.image.revision: a7e14ece0de74632d1d9be599d53678931dc64b3
org.opencontainers.image.version: v1.3.0
org.opencontainers.image.source: https://github.com/mrtrollex/VaultBridge
org.opencontainers.image.licenses: MIT
```

All four tags resolved to the same OCI index. The exact immutable reference below was anonymously
pulled with an empty Docker configuration and used for every container verification:

```text
ghcr.io/mrtrollex/vaultbridge@sha256:5a1709c279c3731f891b59026adb7e8f5497c299687596b74b49ffd64a9f5a0e
```

### Immutable-image MCP verification

`scripts/smoke_mcp_http.py --image <exact digest>` passed. With MCP disabled, `/mcp` remained
unavailable. With it enabled, the official Streamable HTTP client saw exactly seven read-only tools.
Separate official-client sessions authenticated with the current key and `API_KEY_PREVIOUS`; each
successfully called `list_notes` and asserted the structured note list contained the disposable
`Smoke.md`. Missing and invalid Bearer credentials, invalid Host, invalid present Origin, REST
compatibility, and clean shutdown behaved as required.

With writes enabled, the surface added exactly `create_note` and `append_note`. The client created
sanitized `MCP Smoke/Relationship Target.md` and `MCP Smoke/Relationship Source.md` notes. A
`note_links` call on the source asserted structured content containing one resolved relationship
whose target was `MCP Smoke/Relationship Target`, heading was `Verified Section`, alias was
`Synthetic target`, and canonical `resolved_path` was `MCP Smoke/Relationship Target.md`. A
`note_backlinks` call on the target asserted structured content containing the canonical
`MCP Smoke/Relationship Source.md` source path with the same target, heading, and alias metadata.
The disposable create, append, duplicate-append suppression, read-back, and clean shutdown checks
also passed. The corrected verification run completed at `2026-09-25T16:25:31Z` against the exact
digest above. It created only
`C:\Users\619ri\AppData\Local\Temp\vaultbridge-vb093-7jevvbpx`, restored removal permissions for
the deliberately restricted `Smoke.md` when required, removed that root, and passed the harness's
explicit post-removal absence assertion. An independent `Test-Path -LiteralPath` check after the
process exited returned `False`. Earlier MCP smoke runs are not used as successful cleanup evidence;
their unrelated historical temporary roots were left untouched.

### Immutable-image full functional verification

A disposable local stream adaptation of the historical `scripts/verify-vb075-image.sh` behavior was
used without modifying that tracked evidence script. The final gate ran the VaultBridge container
as UID:GID `568:568` with all capabilities dropped; WSL root was used only so the harness could
remove model-cache files owned through the bind mount. The gate passed at `2026-09-25T15:15:02Z`
with Docker `29.8.0` on `linux/amd64`.

```text
initial semantic readiness: 22 seconds
restart semantic readiness: 4 seconds
liveness/readiness/rich health: PASS
dashboard/assets/security headers: PASS
authenticated API/list/read/literal/semantic: PASS
real semantic retrieval: PASS
outgoing links/backlinks on sanitized synthetic wikilinks: PASS
CLI availability: PASS
derived semantic persistence/restart: PASS
privacy-safe logs: PASS
clean stop: PASS
disposable cleanup: PASS
```

Only disposable synthetic Markdown, credentials, and derived data were used. No operator vault,
credential, semantic-data directory, or running production instance was accessed.

### Current TrueNAS boundary

The packet's task-start expectation was package `1.0.1` with image `1.2.1`, but upstream changed
independently before this verification completed. On 2026-09-25, `truenas/apps` master commit
`f39f282e8a58fe57372ca0f005123c84b2ecee31` and generated `catalog.json` reported Community
package `1.0.2`, application image `1.3.0`, and library `2.3.11`. Both upstream source and generated
package forms still omit `MCP_HTTP_ENABLED`, `MCP_WRITE_ENABLED`, `MCP_HTTP_ALLOWED_HOSTS`, and
`MCP_HTTP_ALLOWED_ORIGINS`. Thus the catalog now selects the v1.3.0 image, but first-class MCP form
delivery and live TrueNAS lifecycle verification remain separate from this completed application
release evidence.

## `v1.2.1` release evidence

Release date: 2026-09-20.

VaultBridge `v1.2.1` is a published stable, non-prerelease GitHub Release from source commit
`59666d75b6ec1d4ca01430d61669f212d46180b5`. Release-triggered workflow run
[`35507290152`](https://github.com/mrtrollex/VaultBridge/actions/runs/35507290152) passed release-source
verification, image publication, and stable-alias publication. The release contains the dashboard
favicon and sanitized TrueNAS canary screenshots prepared in the source release diff.

Publishing `v1.2.1` did not update the accepted TrueNAS Community App. At that release point, its
catalog package was `1.0.0` and its application image was `1.1.0`.

## `v1.2.0` release evidence

Release date: 2026-09-20.

VaultBridge `v1.2.0` is a published stable, non-prerelease GitHub Release from source commit
`375bf484fbe6a302424951d33c701f6fd9773e3f`. Exact-source CI and independent inspection of the
published immutable image passed. This section records both the initial pre-publication workflow
failure and the successful recovery without confusing the release image source with the later
workflow implementation commit.

### Release gate ledger

| Phase | Status | Evidence |
|---|---|---|
| Exact-source CI | **PASS** | CI run [`35497273752`](https://github.com/mrtrollex/VaultBridge/actions/runs/35497273752) passed on release source `375bf484fbe6a302424951d33c701f6fd9773e3f`. |
| Stable GitHub Release | **PASS** | Published [`v1.2.0`](https://github.com/mrtrollex/VaultBridge/releases/tag/v1.2.0), prerelease false, at the recorded source commit. |
| Initial GHCR publication | **FAIL — no image published** | Release-triggered run `35497653952` failed during source verification before image publication. |
| Recovery publication | **PASS** | Manual recovery run [`35501442667`](https://github.com/mrtrollex/VaultBridge/actions/runs/35501442667) passed release verification, exact-image publication, and stable-alias publication. |
| OCI identity and anonymous pull | **PASS** | All four aliases resolve to the recorded index; runtime manifest, provenance manifest, labels, platform, and anonymous exact-digest pull were independently verified. |
| Immutable-image MCP verification | **PASS** | MCP 2.2.0 import, stdio lifecycle, disabled/enabled Streamable HTTP, REST compatibility, authentication and Host/Origin rejection, official client, exact five tools, synthetic-note access, and clean shutdown passed. |
| Immutable-image functional gate | **PASS** | Exact digest passed dashboard, health, API, retrieval, CLI, persistence, privacy-safe logging, clean-stop, and cleanup checks at `2026-09-20T09:35:30Z`. |

### Exact release source and publication recovery

```text
GitHub Release: https://github.com/mrtrollex/VaultBridge/releases/tag/v1.2.0
release source commit: 375bf484fbe6a302424951d33c701f6fd9773e3f
exact-source CI run: 35497273752
exact-source CI URL: https://github.com/mrtrollex/VaultBridge/actions/runs/35497273752
exact-source CI conclusion: success
```

The first release-triggered Publish GHCR image run, `35497653952`, failed before publishing any
`v1.2.0` image. Its source-verification job ran the complete pytest tree in one process, which both
collected Playwright E2E without installed Chromium and mixed that E2E runtime with tests using
`asyncio.run()`.

PR [#75](https://github.com/mrtrollex/VaultBridge/pull/75), merged as workflow implementation commit
`604417732ee98b95c46a5a1de6e486d284a18c8d`, separated unit/integration tests from Chromium
Playwright E2E and added explicit safe `workflow_dispatch` recovery. Recovery validates the existing
published release, expected immutable source SHA, and prerelease state; publication checks out the
verified release-source SHA. Exact-image publication is independent of rolling aliases, which
converge on GitHub's current latest stable exact image.

Recovery run `35501442667` used workflow source/main commit
`604417732ee98b95c46a5a1de6e486d284a18c8d`, but verified and built release source
`375bf484fbe6a302424951d33c701f6fd9773e3f`. `Verify release source`, `Build and publish`, and
`Publish stable aliases` all passed. The former is the workflow hotfix commit; the latter is the
immutable `v1.2.0` application/image source.

### Published OCI evidence

```text
repository: ghcr.io/mrtrollex/vaultbridge
verified aliases: 1.2.0, 1.2, 1, latest
OCI index digest: sha256:b130399ddaafc0f8132febcf9a9209eb36ede287b911ffdf1e8a653fd5666102
linux/amd64 runtime-manifest digest: sha256:f21cb016d3ccebc9df9b5ee33c9006396a1003db0d769721a495204bb34e2495
BuildKit provenance attestation digest: sha256:5d9007af52b3101719be83686082293bb2481b16cf3cf27de9c6af40fc51687c
platform: linux/amd64
org.opencontainers.image.revision: 375bf484fbe6a302424951d33c701f6fd9773e3f
org.opencontainers.image.version: v1.2.0
org.opencontainers.image.source: https://github.com/mrtrollex/VaultBridge
org.opencontainers.image.licenses: MIT
```

All four aliases resolved to the same OCI index digest. Anonymous pull passed with a temporary empty
Docker configuration using this exact immutable reference:

```text
ghcr.io/mrtrollex/vaultbridge@sha256:b130399ddaafc0f8132febcf9a9209eb36ede287b911ffdf1e8a653fd5666102
```

### Immutable-image MCP verification

Every check used the exact digest above, never a mutable tag. The image contained MCP package
`2.2.0`, and importing `app.mcp_server` passed. `python -m app.mcp_server </dev/null` emitted clean
start/stop events and exited successfully.

`scripts/smoke_mcp_http.py --image <exact digest>` passed the MCP-disabled path, MCP-enabled path,
REST compatibility, current and previous API keys, invalid Bearer/Host/present-Origin rejection,
official MCP client, exact five read-only tools, `list_notes` access to a synthetic note, and clean
shutdown.

### Immutable-image full functional verification

A disposable local adaptation of `scripts/verify-vb075-image.sh` was used as the verification
harness and was not added to tracked repository state. The final successful gate used Docker
`29.8.0` on `linux/amd64` and completed at `2026-09-20T09:35:30Z`.

```text
exact runtime reference: ghcr.io/mrtrollex/vaultbridge@sha256:b130399ddaafc0f8132febcf9a9209eb36ede287b911ffdf1e8a653fd5666102
initial semantic readiness: 10 seconds
restart semantic readiness: 2 seconds
dashboard/assets/security headers: PASS
liveness/readiness/rich health: PASS
authenticated API/literal/semantic/read: PASS
CLI availability: PASS
derived semantic persistence/restart: PASS
privacy-safe logs: PASS
clean stop: PASS
disposable cleanup: PASS
```

The final WSL harness ran as root only to remove model-cache files created through the bind mount by
runtime UID 568. The VaultBridge container itself continued to run as UID:GID `568:568` with
`--cap-drop ALL`; the application did not run as root.

### Upgrade and TrueNAS boundaries

Markdown remains authoritative and unchanged. No SQLite schema migration is required. Upgrading
from `v1.1.0` to `v1.2.0` triggers one automatic rebuild of the derived semantic index because the
effective embedding/backend-artifact fingerprint changed; this is not a manual data migration.

Publishing `v1.2.0` did not update the accepted TrueNAS Community App and did not close VB-082. Its
contract at that release point was catalog package `1.0.0`, application/image `1.1.0`, library
`2.3.11`, and default Web UI port `30491`. No `v1.2.0` Community App lifecycle validation is claimed.

## `v1.1.0` release evidence — VB-075

Preparation baseline: 2026-09-02.

VaultBridge `v1.1.0` is published from source commit
`e39ed91db75f912f390c7ec915dea73369bb9252`. GitHub Actions publish run `33641163374` generated and
pushed aliases `1.1.0`, `1.1`, `1`, and `latest` for `ghcr.io/mrtrollex/vaultbridge`. Exact-source
CI and the disposable exact-image functional gate were verified on 2026-09-07; the immutable
evidence is recorded below.

`v1.1.0` is the correct Semantic Versioning target because the post-v1 source adds backward-compatible
API-key rotation, verified related-note and duplicate-candidate operations, rate limiting, expanded
CLI/index maintenance, an optional filesystem watcher, non-root model-cache improvements, and a
bundled dashboard with Overview, Literal/Semantic Search, protected access, and complete read-only
note reading. Existing endpoints, operation IDs, request/response contracts, Markdown authority,
semantic-index format/ranking, and the one-process/one-image architecture remain compatible. A patch
would understate additive features; no breaking change justifies `v2.0.0`.

### Release gate ledger

| Phase | Status | Required evidence |
|---|---|---|
| A. Release preparation | **PASS** | Reviewed version/changelog/checklist/project-state preparation is included in the released source. |
| B. Exact release candidate commit | **PASS** | Exact-source `CI` run `33640580398` passed its `python` and `docker` jobs on release commit `e39ed91db75f912f390c7ec915dea73369bb9252`. |
| C. Tag and GitHub Release | **PASS** | Stable release `v1.1.0` exists at the recorded released source commit; it was not moved or recreated by this task. |
| D. GHCR workflow | **PASS** | Successful GitHub Actions publish run `33641163374`. |
| E. OCI aliases and digests | **PASS** | Four published aliases, OCI index, `linux/amd64` runtime manifest, BuildKit attestation, and required OCI labels are recorded below. |
| F. Anonymous pull | **PASS** | Anonymous pull of the exact OCI index digest succeeded with a temporary empty Docker config. |
| G. Immutable-image functional verification | **PASS** | The exact digest passed the disposable dashboard/API/CLI/semantic/persistence/privacy/cleanup gate at `2026-09-07T17:12:10Z`. |
| H. Post-release evidence | **PASS** | Release, workflow, image, digest, platform, labels, anonymous pull, exact-source CI, and exact-image runtime evidence are recorded. |

VB-075 is complete. Publication evidence, exact-source CI, and the reproducible exact-image runtime
gate all pass without changing the published image or conflating this release gate with VB-082's
separate TrueNAS Community App lifecycle work.

### A. Release preparation

1. Finalize the changelog date as `2026-09-02` in the final source-preparation diff without treating
   that date as tag, publication, or runtime evidence.
2. Align only the established version contract: `pyproject.toml`, `app/main.py`, and the regression
   assertion in `tests/test_api.py` must all read `1.1.0`.
3. Reconcile release notes against the exact `v1.0.0..HEAD` history, including dashboard polish,
   streamlined protected access, the safe full-note reader, and the release-neutral TrueNAS design
   and definition without claiming Community App availability.
4. Run the local checks and security scan below. Keep the working tree uncommitted for review.

### B. Exact release candidate commit

Current evidence:

```text
release commit SHA: e39ed91db75f912f390c7ec915dea73369bb9252
CI workflow: CI
CI event: push
CI run: 33640580398
CI URL: https://github.com/mrtrollex/VaultBridge/actions/runs/33640580398
CI commit SHA: e39ed91db75f912f390c7ec915dea73369bb9252
CI python job: success (tests and compile check passed)
CI docker job: success (Compose validation and image build passed)
CI conclusion: success
CI completed: 2026-09-02T14:13:24Z (before the release workflow started at 2026-09-02T14:18:13Z)
```

The release commit must contain the reviewed preparation and no unrelated changes. Local checks do
not substitute for exact-commit GitHub CI.

### C. Tag and GitHub Release

Stable release `v1.1.0` has been published from
`e39ed91db75f912f390c7ec915dea73369bb9252`. This task did not create, move, or recreate it.

### D. Current GHCR workflow contract

The sole publisher is [`.github/workflows/publish-ghcr.yml`](../.github/workflows/publish-ghcr.yml):

- trigger: GitHub `release` event with type `published`, plus a manual recovery dispatch that requires
  an existing published release tag, its expected full source SHA, and matching prerelease state;
- source: verification checks out the validated release tag and records its commit SHA; publication
  checks out that verified SHA, including during manual recovery;
- repository: `ghcr.io/${{ github.repository_owner }}/vaultbridge`, normalized to lowercase by the
  metadata action;
- verification: Ruff, isolated unit/integration pytest, separately installed Chromium Playwright E2E,
  application compilation, and Compose configuration must pass before publish;
- build: repository-root context and root `Dockerfile`, with no alternate TrueNAS image;
- stable aliases from `v1.1.0`: `1.1.0`, `1.1`, `1`, and `latest`;
- architecture: no `platforms` matrix is declared, so the current Ubuntu runner builds the native
  Linux/amd64 runtime; multi-architecture support remains out of scope;
- OCI labels: `org.opencontainers.image.source`, `.revision`, `.version`, and `.licenses` are set to
  repository URL, verified release-source SHA, release tag, and `MIT`;
- artifact form: BuildKit minimal provenance is enabled and the build output digest is inspected.
  The prior stable publication produced an OCI index containing the runtime manifest and
  attestation; the verified `v1.1.0` values are recorded below.

The manual recovery path reuses the same publish job and tag policy. Exact-version publication stays
independent across release tags, while duplicate runs for one tag are serialized as one transaction.
The separately serialized rolling-alias job resolves GitHub's current latest stable release
immediately before pointing major/minor, major, and `latest` at that release's exact image. Pending
alias work may coalesce safely because every surviving job converges on the current latest stable
release. Prereleases publish only their exact version tag and skip rolling aliases.

### E. OCI alias, digest, and label verification

Record without abbreviation:

```text
GHCR workflow run: 33641163374
GitHub Release URL: https://github.com/mrtrollex/VaultBridge/releases/tag/v1.1.0
GHCR workflow URL: https://github.com/mrtrollex/VaultBridge/actions/runs/33641163374
published image repository: ghcr.io/mrtrollex/vaultbridge
published aliases: 1.1.0, 1.1, 1, latest
OCI index digest: sha256:753e613617d221c3dac311600a36cab3f2727b09f630321664eaa7b7ad6eb48c
linux/amd64 runtime-manifest digest: sha256:62ddad69cf3e4af632d40c3bcdb8f9e601fa7c04009616350f9ab706a4171e92
BuildKit provenance attestation digest: sha256:744fe630075b91cc576a18b37012c4d37d0b551b17a74cab8088a1448e5fc4a3
org.opencontainers.image.source: https://github.com/mrtrollex/VaultBridge
org.opencontainers.image.revision: e39ed91db75f912f390c7ec915dea73369bb9252
org.opencontainers.image.version: v1.1.0
org.opencontainers.image.licenses: MIT
```

Inspect all four stable aliases and require the same OCI index digest. An alias match is not enough:
inspect the runtime manifest and labels from the exact digest.

### F. Anonymous pull verification

Anonymous pull with a temporary empty Docker config: **PASS**.

Pulled reference:

```text
ghcr.io/mrtrollex/vaultbridge@sha256:753e613617d221c3dac311600a36cab3f2727b09f630321664eaa7b7ad6eb48c
```

Runtime inspection reported `OS=linux` and `ARCH=amd64`.

### G. Immutable-image functional verification

The reproducible gate in [`scripts/verify-vb075-image.sh`](../scripts/verify-vb075-image.sh) passed at
`2026-09-07T17:12:10Z` against the anonymously pulled immutable OCI index on a disposable Ubuntu
WSL2 `linux/amd64` runner with Docker `28.3.3`. It used generated one-run credentials, two synthetic
notes, a unique container, an unused loopback port, separate disposable `/vault` and `/data` mounts,
and a temporary empty Docker auth directory.

Recorded results:

```text
exact runtime reference: ghcr.io/mrtrollex/vaultbridge@sha256:753e613617d221c3dac311600a36cab3f2727b09f630321664eaa7b7ad6eb48c
anonymous pull and OCI metadata: PASS
dashboard shell/assets/CSP/nosniff/no-referrer: PASS
liveness/readiness/rich health: PASS
authenticated list/read and invalid-key rejection: PASS
literal retrieval: PASS
real semantic retrieval: PASS
CLI availability: PASS
initial semantic readiness: 5 seconds
post-restart semantic readiness: 2 seconds
derived-data restart persistence: PASS
privacy-safe application logs and dashboard asset scan: PASS
clean stops with exit code zero: PASS
container/auth/vault/data/evidence cleanup: PASS
```

The validation host lacked a normal bridge/iptables setup, so this run used the script's explicit
host-network compatibility mode and selected an unused loopback port for Uvicorn. The earlier exact-
digest TrueNAS session independently retained the normal isolated-bridge/default-container startup,
health, API, semantic, and UI evidence. Together these prove the VB-075 image criteria without
turning host-network mode into a deployment recommendation.

Later VB-082 sessions partially validated this same tag and OCI index through a disposable TrueNAS
`25.10.6` Custom App. In addition to the core runtime/API/UI record, sanitized 2026-09-08 evidence
covers API-key rotation/removal, port edit/persistence, occupied-port rejection/recovery,
permission-denied/recovery behavior, and external host-path preservation on uninstall. Every
executable pre-upstream VB-082 gate is complete, while catalog-only and eventual rollback gates keep
VB-082 partial. The exact record is in
[`TRUENAS_COMMUNITY_APP_DESIGN.md`](TRUENAS_COMMUNITY_APP_DESIGN.md#vb-082-pre-upstream-lifecycle-evidence--2026-09-08).
This downstream evidence does not alter the already-complete VB-075 release gate.

### H. Post-release evidence and downstream handoff

The complete immutable release, exact-source CI, and exact-image runtime facts are recorded here and
handed to VB-081/VB-082. VB-083 is complete after upstream PR #5805 review/merge, accepted
source/generated-entry verification, and Discover Apps availability confirmation. VB-082 remains the
separate partial TrueNAS Community App lifecycle task; release evidence and catalog acceptance do not
close its unresolved post-merge gates.

### TrueNAS Community App field boundary

The historical local `ix-dev/community/vaultbridge/` source represented the published image through
the upstream repository-plus-tag convention during release preparation:

- `app.yaml` has `app_version: 1.1.0`;
- `ix_values.yaml` uses repository `ghcr.io/mrtrollex/vaultbridge` and exact tag `1.1.0`;
- fixtures inherit that production image map and keep synthetic API keys and disposable paths;
- `app.yaml` `version: 1.0.0` is independent of the VaultBridge runtime version;
- current upstream has no dedicated digest field in this image map, so the verified OCI index digest
  remains release/live-validation evidence rather than fabricated package syntax.

After this release gate completed, upstream PR #5805 supplied the TrueNAS CDN icon, retained catalog
package `1.0.0` / app `1.1.0` / library `2.3.11`, selected default Web UI port `30491`, and merged the
accepted source plus generated catalog entry. Initial real-catalog install/form/masking/Portal, Host
Path, ixVolume configuration, healthy vault visibility, and rotation migration are
operator-confirmed. The separate VB-082 lifecycle task remains partial because edit-form persistence,
ixVolume uninstall semantics, a valid prior-state upgrade, and rollback are unresolved.

### Local preparation validation evidence

Executed on native Windows with the checked-in Python 3.12 virtual environment on 2026-09-02:

| Check | Result |
|---|---|
| `.venv\Scripts\pytest.exe -q -ra` | **PASS:** 426 passed; 15 expected privilege-dependent symlink tests skipped |
| focused application metadata, route/operation-ID, versioning, and UI contracts | **PASS:** 61 passed |
| `.venv\Scripts\python.exe -m compileall -q app` | **PASS** |
| `.venv\Scripts\ruff.exe check .` | **PASS** |
| `.venv\Scripts\python.exe -m app.cli --help` | **PASS** |
| `git diff --check` | **PASS:** no whitespace errors; normal Windows LF-to-CRLF notices only |
| high-confidence credential/private-key scan of all nine changed files | **PASS:** no credential or private-key pattern found; the existing v1.0 Compose example contains only the intentional `ci-placeholder-secret` fixture |

These checks validate the working tree only. They do not prove the future exact release commit,
GitHub CI, GHCR workflow, image pull, OCI metadata, or TrueNAS runtime gates.

### `v1.1.0` draft release notes

VaultBridge 1.1 is a backward-compatible feature release built around one platform-neutral runtime:
one FastAPI process, one normal Docker image, and independently usable API, CLI, and bundled `/ui/`
dashboard. Markdown remains authoritative; semantic data stays local and rebuildable.

Highlights:

- controlled API-key rotation with one optional previous key;
- live-verified related-note suggestions and conservative read-only duplicate candidates;
- dependency-free process-local rate limiting for protected legacy and `/api/v1` routes;
- expanded local CLI for status, literal/semantic retrieval, incremental indexing, clean reindexing,
  and stopped-service index inspection/rebuild;
- optional disabled-by-default filesystem watching through the existing indexer;
- writable local model caching in the existing derived-data location for non-root containers;
- bundled same-origin dashboard with a health-backed Overview, protected Literal and Semantic Search,
  streamlined browser-tab access, final product/accessibility hardening, and a complete safe read-only
  note reader using the existing API;
- version-neutral TrueNAS Community App design and release-neutral/static definition work that is
  ready for production image finalization after publication.

The dashboard adds no editing, index mutation, accounts/multi-user system, graph visualization, or
NAS administration. This release does not claim multi-architecture images, TrueNAS Discover
availability, upstream `truenas/apps` acceptance, live Community App validation, or a TrueNAS-specific
runtime fork.

## v1.0.0 readiness audit

Audit baseline: 2026-08-26.

VB-056 through VB-060 established the source, CI, security, deployment, and distribution gates.
A subsequent public-history rewrite intentionally changed commit identities and retired the old
release candidate artifacts. Pre-rewrite commit SHAs, workflow runs, and RC1 digests are therefore
historical evidence only and must not be used as final `v1.0.0` release evidence.

The exact rewritten source and `v1.0.0-rc.2` prerelease gates completed first. Stable `v1.0.0` then
passed exact-main CI, GitHub/GHCR publication, alias/digest verification, and the final anonymous
exact-digest TrueNAS runtime smoke test. All v1.0 release gates are complete.

| ROADMAP acceptance criterion | Status | Evidence | Post-release note |
|---|---|---|---|
| Clean install succeeds from public documentation | **PASS** | On 2026-08-25 VaultBridge was anonymously cloned from the public GitHub repository onto TrueNAS SCALE / Linux amd64 with Docker Engine 28.3.1. The generic Docker Compose deployment built successfully from source against a disposable empty vault using isolated host port `8876`. The container started successfully; `GET /health/live` returned HTTP 200 `{"ok":true}`, `GET /health/ready` returned HTTP 200 `{"ready":true}`, and authenticated `GET /api/v1/notes/list?limit=5` returned `{"folder":"","notes":[]}`. Final stable verification also anonymously pulled the public image by exact OCI digest and passed the disposable-vault runtime checks. | Repeat for future releases or when deployment behavior changes. |
| CI is green | **PASS** | Exact-main [CI run 32932765995](https://github.com/mrtrollex/VaultBridge/actions/runs/32932765995) passed both `python` and `docker` on stable source commit `1a430996c9db331f448339d233e940d7aa7b3b6d`. | Branch protection is not enabled; retain exact-commit CI as a manual gate for future releases. |
| Container image is published | **PASS** | Stable publish workflow run `32932955416` passed `Verify release source` and `Build and publish`. Public package `ghcr.io/mrtrollex/vaultbridge` is linked to `mrtrollex/VaultBridge`. Aliases `1.0.0`, `1.0`, `1`, and `latest` all resolve to OCI index `sha256:0b6734d485df83326cac0ea394b17aaa86fde0be4f7ad7dca9ca49cb531ead71`, with `linux/amd64` runtime manifest `sha256:577522d36484888de6620cd168d9129c7c9ecbb44d1be35260956ca54ecc6791`. Anonymous exact-digest pull and the final disposable-vault smoke test passed. | VB-055 is optional post-v1 multi-architecture work. |
| Public API is versioned | **PASS** | `app/api/versioning.py` registers six protected legacy/v1 route pairs from shared handlers. `tests/test_api_versioning.py` passed 24 tests; `tests/test_api.py::test_router_registration_preserves_public_contract` checks all 15 public schema paths and operation IDs. Health and `/privacy` intentionally remain unversioned. | Keep legacy aliases until a separate migration decision. |
| Full semantic rebuild does not block ordinary request handling | **PASS** | `tests/test_indexer.py::test_application_startup_is_non_blocking_and_shutdown_waits_for_sync` proves lifespan startup completes while full sync is blocked. `test_ordinary_http_requests_continue_during_full_rebuild` proves liveness and authenticated non-semantic `/api/v1/notes/list` requests succeed during blocked full sync. Semantic tests prove an older compatible index remains searchable during and after a failed refresh. | Preserve the precise guarantee: initial semantic search waits for readiness, and shutdown can still wait on uninterruptible model/filesystem work. |
| Retrieval evaluation suite exists | **PASS** | `python -m pytest -q tests/eval` passed 9 tests. The deterministic production-pipeline fixture covers 13 cases: all 13/100% Hit@1/100% Hit@3/100% MRR; English 8/100%/100%/100%; Slovak 4/100%/100%/100%; cross-language 1/100%/100%/100%; heading context 2/100%/100%/100%. | Do not change `tests/eval/baseline.json` merely to clear a future gate. This does not measure real-model accuracy or latency. |
| No known authentication/path-traversal bypass | **PASS** | VB-057 centralizes resolved-target containment for literal search, listing, and semantic enumeration and uses the validated resolved path for stat/read. Real WSL/Linux service and legacy/v1 route tests cover external file and directory links, broken links, safe internal aliases, folder scope, direct reads, and semantic cleanup. The VB-058 focused Linux selection passed 52 tests with no symlink skips; native Windows passed the full suite with only privilege-dependent symlink skips. | Continue Linux symlink coverage for future path-sensitive changes. The proportionate path-based design does not claim an OS file-descriptor sandbox against hostile concurrent filesystem mutation. |
| Secrets are not committed or logged | **PASS** | VB-060 repeated targeted scans across 85 tracked files, 328 reachable unique blobs, and the historical source archive with zero high-confidence secret hits. Only example `.env` files are tracked; generated bundles are no longer tracked; reachable author/committer metadata uses the approved noreply/service identities. Logging/observability tests verify bodies, queries, credentials, headers, exception messages, and absolute paths are omitted. | Continue targeted scanning for future releases. This was not exhaustive entropy-based secret detection. Never paste resolved Compose output or environments. |
| Upgrade/rebuild procedure is documented | **PASS** | `README.md` covers source updates, automatic signature invalidation, stopped-service `index check`, explicit `index rebuild`, and Markdown as source of truth. `README_TRUENAS.md` covers managed/source updates, bundles, rollback, automatic incompatibility rebuilds, stopped-service inspection/rebuild, and recovery. Commands match current deployment files and CLI. | Recheck if deployment files change. |

The source-level ROADMAP acceptance criteria, rewritten-source/RC2 gates, and final stable
publication plus post-publication checks all have current supporting evidence. VaultBridge
`v1.0.0` is released and every v1.0 release gate is complete.

## v1.0.0 release conclusion

- Stable `v1.0.0` is released; there are no open v1.0 release blockers.
- The Git tag, GitHub Release, and published GHCR artifact are immutable release evidence.
- VB-042, VB-043, VB-051, VB-055, and other optional/future tasks are post-v1 work, not incomplete
  v1.0 release gates.

## Supported platform statement for v1.0.0

- Production runtime: the repository Dockerfile running as a Linux container, built from source or
  pulled from GHCR after publication.
- TrueNAS: the documented source-built Custom App/shell Compose workflow on Docker-based TrueNAS
  SCALE 24.10 or later.
- Windows/macOS hosts: Docker Desktop may run the Linux container. Native Windows is a development
  and test environment, not a documented production deployment.
- Published architecture: stable `v1.0.0` verified the workflow's `linux/amd64` runtime manifest.
  Do not claim ARM64/multi-architecture support before VB-055.

## Version and artifact contract

- The immutable release identity is the `v1.0.0` Git tag and GitHub Release from one verified commit.
- GHCR tags omit `v`. `1.0.0` must identify the image built from that release tag; `1.0`, `1`, and
  `latest` are mutable stable aliases to the same digest.
- A prerelease such as `v1.0.0-rc.2` publishes only `1.0.0-rc.2` and must not update stable aliases.
- Immutable deployments use `ghcr.io/<lowercase-owner>/vaultbridge@sha256:<digest>`.
- `pyproject.toml` and `app.main.APP_VERSION` are aligned to the stable target `1.0.0`. They do not
  replace tag/release and GHCR digest identity.

## Reusable source validation

Run from the repository root. On Windows:

```powershell
$env:PYTHONPATH = "."
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q tests\eval
.\.venv\Scripts\python.exe -m pytest -q tests\test_cli.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_api_versioning.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_health.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_logging.py tests\test_request_observability.py
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m compileall -q app
git diff --check
```

Also run focused authentication and path-containment tests, including Linux symlink tests. With
Docker available:

```bash
mkdir -p /tmp/vault
API_KEY=ci-placeholder-secret OBSIDIAN_VAULT_PATH=/tmp/vault docker compose config >/dev/null
docker build -t vaultbridge:release-check .
```

Do not paste resolved Compose output because it can contain `API_KEY`.

## Isolated clean-install smoke test

Use a disposable empty vault, never a production/TrueNAS vault. Set `RELEASE_REF` to the exact
release tag or commit.

```bash
set -eu
RELEASE_REF=v1.0.0
SMOKE_ROOT="$(mktemp -d)"
git clone --branch "$RELEASE_REF" --depth 1 https://github.com/mrtrollex/VaultBridge.git "$SMOKE_ROOT/VaultBridge"
mkdir "$SMOKE_ROOT/vault"
cd "$SMOKE_ROOT/VaultBridge"
cp .env.example .env
SMOKE_API_KEY="$(docker run --rm python:3.12-slim python -c 'import secrets; print(secrets.token_urlsafe(48))')"
{
  printf 'API_KEY=%s\n' "$SMOKE_API_KEY"
  printf 'OBSIDIAN_VAULT_PATH=%s\n' "$SMOKE_ROOT/vault"
  printf 'API_PORT=8765\nPUID=%s\nPGID=%s\n' "$(id -u)" "$(id -g)"
  printf 'MAX_NOTE_BYTES=1000000\n'
  printf 'SEMANTIC_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2\n'
  printf 'SEMANTIC_CHUNK_CHARS=600\nSEMANTIC_CHUNK_OVERLAP=100\nSEMANTIC_INDEX_BATCH_SIZE=25\n'
} > .env
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8765/health/live
until curl -fsS http://127.0.0.1:8765/health/ready; do sleep 5; done
curl -fsS 'http://127.0.0.1:8765/api/v1/notes/list?limit=5' \
  -H "Authorization: Bearer $SMOKE_API_KEY"
docker compose logs --tail 100
docker compose down
```

Record the source ref, host OS/architecture, Docker/Compose versions, readiness time, and result.
Inspect logs without copying secrets. Remove `SMOKE_ROOT` only after confirming it is disposable.

## CI release-time check

1. Confirm the exact release commit is on `main` and the worktree is clean.
2. Open the `CI` workflow run for that commit.
3. Require completed/success for both check runs named `python` and `docker`.
4. Confirm `python` ran dependency installation, full pytest, and compileall.
5. Confirm `docker` ran Compose validation and the root Dockerfile build.
6. Record the commit SHA and workflow-run URL in release evidence.

Local pytest supports the audit; it does not prove GitHub CI is green.

## `v1.0.0-rc.2` procedure

This sequence completed successfully for `v1.0.0-rc.2`; the durable evidence is recorded below.
Retain these steps as the verification procedure for future release candidates.

1. Verify clean `main` and green `python`/`docker` CI on the exact commit.
2. Record the current digest or absence of `latest` before publication.
3. Create tag `v1.0.0-rc.2` and publish a GitHub **prerelease** with the draft notes below.
4. Require `Publish GHCR image` jobs `Verify release source` and `Build and publish` to succeed.
5. Verify package `vaultbridge` exists, is linked to this repository, and has intended visibility.
   Deliberately make it public if anonymous pulls are intended.
6. Verify exact tag `1.0.0-rc.2`, inspect its manifest, and record digest/platform. Confirm `1.0.0`,
   `1.0`, `1`, and `latest` were not created or updated by the prerelease.
7. Pull the exact tag and digest; confirm both resolve to the recorded manifest.
8. Run the pulled image with disposable vault/data. Verify start, `/health/live`, initial and eventual
   `/health/ready`, and authenticated `/api/v1/notes/list`.
9. Inspect logs for safe lifecycle output and no secret/header/query/content/absolute-path exposure.
10. Record workflow/package URLs, visibility/linkage, tag, digest, platform, smoke evidence, and the
    unchanged/absent `latest`. Fix blockers before stable release.

## Verified `v1.0.0-rc.2` evidence

- Source commit: `f1131ceec2c003d94af7f9c0a67a802ed067902d`.
- Exact-source main CI: run `32880752547`; `python` and `docker` passed.
- GitHub prerelease: `v1.0.0-rc.2`.
- GHCR publish workflow: run `32881004757`; `Verify release source` and `Build and publish` passed.
- Package: public `ghcr.io/mrtrollex/vaultbridge`, linked to `mrtrollex/VaultBridge`.
- Published prerelease tag: `1.0.0-rc.2`; stable aliases `1.0.0`, `1.0`, `1`, and `latest` were absent.
- OCI index digest: `sha256:ce5c9c75e8757389fca06e3706379f4801e03312b3852d1338d82624af0a45b2`.
- `linux/amd64` runtime manifest:
  `sha256:dbc23ff9b921ce3e0502e2f4d3713c359c190e16ca289e21261888a6566d3d20`.
- BuildKit provenance attestation:
  `sha256:81a78834c34d26563ad0d52497e1cef6e19fb247805c7035bee9c064ca7ab0f2`.
- Anonymous pulls by tag and exact OCI digest passed; the tag resolved to the expected digest.
- TrueNAS disposable-vault/data runtime smoke passed for `linux/amd64`; OCI revision/version matched
  the source commit and `v1.0.0-rc.2`; `/health/live`, `/health/ready`, and authenticated
  `/api/v1/notes/list` passed; logs contained neither the API key nor disposable host path; and the
  disposable container/data were removed.
- This is historical prerelease evidence; the final stable evidence is recorded separately below.

## Stable `v1.0.0` procedure

This sequence completed successfully for stable `v1.0.0`; the durable evidence follows the
procedure.

1. Confirm every ROADMAP criterion and checklist blocker is closed with current evidence.
2. Verify the exact final `main` commit and successful `python`/`docker` CI checks.
3. Verify existing version metadata remains `1.0.0`, finalize `CHANGELOG.md`, and prepare final
   release notes.
4. Create immutable tag `v1.0.0` and publish the GitHub Release from that commit.
5. Require both GHCR publication jobs to succeed.
6. Verify GHCR tags `1.0.0`, `1.0`, `1`, and `latest` resolve to the stable digest.
7. Pull by digest and run the final disposable-vault smoke test.
8. Verify package linkage/visibility, manifest platform, health/readiness, authenticated v1 behavior,
   and safe logs.
9. Record release URL, commit, CI/publish runs, tags, digest, platform, visibility, and smoke evidence.
10. Record completion of every ROADMAP v1.0.0 criterion.

## Verified stable `v1.0.0` evidence

- Stable source commit: `1a430996c9db331f448339d233e940d7aa7b3b6d`.
- Exact-main CI: run `32932765995`; `python` and `docker` passed.
- Git tag and stable, non-prerelease GitHub Release: [`v1.0.0`](https://github.com/mrtrollex/VaultBridge/releases/tag/v1.0.0).
- GHCR publish workflow: run `32932955416`; `Verify release source` and `Build and publish` passed.
- Package: public `ghcr.io/mrtrollex/vaultbridge`, linked to `mrtrollex/VaultBridge`.
- Stable aliases `1.0.0`, `1.0`, `1`, and `latest` all resolve to OCI index
  `sha256:0b6734d485df83326cac0ea394b17aaa86fde0be4f7ad7dca9ca49cb531ead71`.
- `linux/amd64` runtime manifest:
  `sha256:577522d36484888de6620cd168d9129c7c9ecbb44d1be35260956ca54ecc6791`.
- BuildKit provenance attestation:
  `sha256:1b1c0596963f5c1ab56e628b9faecf39b8943d304ce46263ed47ff2e5ffe8aec`.
- OCI metadata verified revision `1a430996c9db331f448339d233e940d7aa7b3b6d`, version `v1.0.0`, source
  `https://github.com/mrtrollex/VaultBridge`, and license `MIT`.
- Published runtime platform: `linux/amd64`.
- Final TrueNAS stable verification: **PASS**. The public image was anonymously pulled by exact OCI
  digest using disposable vault/data; platform and revision/version checks passed; the container
  started; `/health/live`, `/health/ready`, and authenticated `/api/v1/notes/list` passed; the
  generated API key and disposable host path were absent from application logs; and the disposable
  test container and data were removed.
- All stable `v1.0.0` release gates are complete.

## `v1.0.0` release notes

VaultBridge 1.0 is a small self-hosted REST API for safe operations and local semantic retrieval over
an Obsidian Markdown vault. Markdown remains the source of truth.

Highlights:

- stable protected `/api/v1` note/search routes with compatibility aliases;
- local multilingual semantic search with heading-aware retrieval and hybrid ranking;
- background, batched semantic indexing with health/readiness visibility;
- Docker and TrueNAS SCALE deployment documentation;
- stopped-service semantic index inspection and rebuild commands;
- safe structured application logging and request correlation;
- GHCR distribution after release publication is verified.

This release does not claim multi-architecture images, rate limiting, API-key rotation, MCP, a
filesystem watcher, or native Windows production deployment.

## Non-blocking P1 follow-ups

VB-042 API-key rotation, VB-043 rate limiting, VB-051 CLI expansion, and VB-055 multi-architecture
remain high-value post-v1 work. They are not blockers unless a separate review finds a concrete
violation of a release criterion. The confirmed literal search/list containment defect is different:
it is P0 because it directly violates the security criterion.
