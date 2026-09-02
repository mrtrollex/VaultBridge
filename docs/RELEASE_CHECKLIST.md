# VaultBridge release checklist

This is the durable completed release evidence for VaultBridge `v1.0.0`. VB-056 established the
audit, and the final stable publication and runtime verification closed every v1.0 release gate on
2026-08-26. Later releases should add their own evidence without replacing the immutable `v1.0.0`
tag, GitHub Release, or artifact record below.

## Proposed `v1.1.0` release preparation — VB-075

Preparation baseline: 2026-09-02.

The latest published stable release remains `v1.0.0`, whose annotated tag resolves to source commit
`1a430996c9db331f448339d233e940d7aa7b3b6d`. This release-preparation branch starts from
`ca58de1426d2b83e040f882f09757d60e968a6aa`, the current `main` after PR #48 / VB-081. There are 35
commits, including 16 first-parent commits, after `v1.0.0`. The preparation base is not the release
candidate: the exact candidate will be the future reviewed commit containing this working-tree diff.

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
| A. Release preparation | **PASS — REVIEWABLE WORKING TREE** | Uncommitted version/changelog/checklist/project-state diff; local validation and secret audit recorded below. |
| B. Exact release candidate commit | **BLOCKED / REQUIRES LIVE VERIFICATION** | Full commit SHA on `main`, clean tree, and successful `CI` `python` and `docker` jobs for exactly that SHA. |
| C. Tag | **BLOCKED / REQUIRES APPROVAL** | Immutable `v1.1.0` tag and stable non-prerelease GitHub Release created from the exact candidate only after explicit approval. |
| D. GHCR workflow | **BLOCKED / REQUIRES LIVE VERIFICATION** | `Publish GHCR image` run URL/ID with both `Verify release source` and `Build and publish` successful. |
| E. OCI aliases and digests | **BLOCKED / REQUIRES LIVE VERIFICATION** | `1.1.0`, `1.1`, `1`, and `latest` mapped to one OCI index digest; record the index digest, `linux/amd64` runtime-manifest digest, provenance attestation where present, and all required labels. |
| F. Anonymous pull | **BLOCKED / REQUIRES LIVE VERIFICATION** | Fresh credential-free pull by exact digest plus proof that the exact tag resolves to the recorded digest. |
| G. TrueNAS immutable-image verification | **BLOCKED / REQUIRES LIVE VERIFICATION** | Exact-digest disposable runtime gate from `DASHBOARD_RELEASE_CHECKLIST.md`, including UI/API/CLI, semantic startup, restart persistence, safe logs, and cleanup. |
| H. Post-release evidence | **BLOCKED / REQUIRES LIVE VERIFICATION** | Sanitized release/workflow/package links, SHA, tag, aliases, digests, labels, platform, anonymous-pull result, runtime result, and downstream VB-081/VB-082 status. |

No tag, GitHub Release, workflow run, OCI digest, anonymous pull, or published-image runtime result is
recorded for `v1.1.0` yet. Placeholder values must never be replaced with guesses.

### A. Release preparation

1. Keep the release date as `TBD` until the actual stable publication step.
2. Align only the established version contract: `pyproject.toml`, `app/main.py`, and the regression
   assertion in `tests/test_api.py` must all read `1.1.0`.
3. Reconcile release notes against the exact `v1.0.0..HEAD` history, including dashboard polish,
   streamlined protected access, the safe full-note reader, and the release-neutral TrueNAS design
   and definition without claiming Community App availability.
4. Run the local checks and security scan below. Keep the working tree uncommitted for review.

### B. Exact release candidate commit

After review, record these values from the resulting clean `main` state:

```text
release commit SHA: <pending>
exact-source CI run: <pending>
CI python job: <pending>
CI docker job: <pending>
```

The release commit must contain the reviewed preparation and no unrelated changes. Local checks do
not substitute for exact-commit GitHub CI.

### C. Tag and GitHub Release

Only after explicit publication approval, create stable tag `v1.1.0` on the recorded release commit
and publish one non-prerelease GitHub Release. Confirm the tag peels to that exact commit and record
the release URL. Do not move or reuse the tag.

### D. Current GHCR workflow contract

The sole publisher is [`.github/workflows/publish-ghcr.yml`](../.github/workflows/publish-ghcr.yml):

- trigger: GitHub `release` event with type `published`;
- source: the workflow checks out `github.event.release.tag_name` in both jobs;
- repository: `ghcr.io/${{ github.repository_owner }}/vaultbridge`, normalized to lowercase by the
  metadata action;
- verification: pytest, application compilation, and Compose configuration must pass before publish;
- build: repository-root context and root `Dockerfile`, with no alternate TrueNAS image;
- stable aliases from `v1.1.0`: `1.1.0`, `1.1`, `1`, and `latest`;
- architecture: no `platforms` matrix is declared, so the current Ubuntu runner builds the native
  Linux/amd64 runtime; multi-architecture support remains out of scope;
- OCI labels: `org.opencontainers.image.source`, `.revision`, `.version`, and `.licenses` are set to
  repository URL, release-event SHA, release tag, and `MIT`;
- artifact form: BuildKit minimal provenance is enabled and the build output digest is inspected.
  The prior stable publication produced an OCI index containing the runtime manifest and
  attestation; the `v1.1.0` OCI index, runtime-manifest digest, and attestation must still be verified
  from the actual published artifact.

This audit found no release-blocking workflow defect, so the workflow remains unchanged.

### E. OCI alias, digest, and label verification

Record without abbreviation:

```text
GHCR workflow run: <pending>
OCI index digest: <pending>
linux/amd64 runtime-manifest digest: <pending>
BuildKit provenance attestation digest, if present: <pending>
org.opencontainers.image.source: <pending; require https://github.com/mrtrollex/VaultBridge>
org.opencontainers.image.revision: <pending; require exact release commit SHA>
org.opencontainers.image.version: <pending; require v1.1.0>
org.opencontainers.image.licenses: <pending; require MIT>
```

Inspect all four stable aliases and require the same OCI index digest. An alias match is not enough:
inspect the runtime manifest and labels from the exact digest.

### F. Anonymous pull verification

Use a fresh temporary Docker configuration with no saved GHCR credentials. Pull
`ghcr.io/mrtrollex/vaultbridge@sha256:<verified-index-digest>`, then prove the image is usable by
digest and that `1.1.0` resolves to the same index digest. Record only sanitized output; never retain
the temporary auth directory or resolved environment.

### G. TrueNAS immutable-image verification

Run the future VB-075 gate in [`DASHBOARD_RELEASE_CHECKLIST.md`](DASHBOARD_RELEASE_CHECKLIST.md)
against the anonymously pulled exact digest. The completed VB-074 source-built Phase B evidence is
historical validation and does not prove the published `v1.1.0` image.

### H. Post-release evidence and downstream handoff

After every prior phase passes, add the immutable release facts to this document and update
`PROJECT_STATE.md`, `ROADMAP.md`, `BACKLOG.md`, and the dashboard checklist. Only then may VB-075 be
marked complete. Hand the verified release tag/digest to VB-081 production finalization; VB-082
remains a separate real TrueNAS Community App lifecycle task, and VB-083 remains the separate
upstream submission task.

### TrueNAS Community App field boundary

The current `ix-dev/community/vaultbridge/` source is intentionally safe before publication:

- release-neutral metadata/questions/template and test fixtures are prepared and statically checked;
- `app.yaml` keeps `app_version: unreleased`;
- `ix_values.yaml` deliberately has no production `images` map;
- all test-values files keep `0.0.0-vb081-development-placeholder` and explicitly prohibit submission;
- `app.yaml` `version: 1.0.0` is the staged catalog-package revision, not the VaultBridge runtime
  version, and is not changed merely because the application release target is `1.1.0`.

No production Community App field can safely claim `1.1.0` before the image exists and passes the
publication gate. After publication and immutable verification, VB-081 production finalization must
set the production image repository/tag, set matching `app_version: 1.1.0`, choose the next catalog
package `version` under then-current upstream rules, generate the final library/hash/catalog
metadata and approved icon reference, and rerun upstream schema/render/deployable-image checks. The
actual image digest and VB-082 evidence must be recorded rather than inferred. This release
preparation therefore leaves every file under `ix-dev/community/vaultbridge/` unchanged.

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
