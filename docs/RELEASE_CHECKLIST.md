# VaultBridge release checklist

This is the durable release gate for VaultBridge. The `v1.0.0` section records the evidence captured
by VB-056; later releases should replace commit, run, tag, and digest evidence. Completing VB-056
means this audit exists. It does not mean `v1.0.0` has shipped.

Do not create a tag, publish a GitHub Release, or publish to GHCR until every blocker is closed.

## v1.0.0 readiness audit

Audit date: 2026-08-25. Audited commit: `71f69141fb83ac2973474009043712a4bc66ff98`
(`main` and the initial `vb-056-v1-release-checklist` branch point).

VB-057 security addendum: 2026-08-25. The containment fix is present in merged commit
`1624e53096adad66126d10e67e3a8997aacb4c1a`; normal CI/release verification is still required on
the eventual RC commit.

VB-058 portability addendum: 2026-08-25. The native-Windows suite now has 291 passes, 10
privilege-dependent symlink skips, and zero failures. The path assertion changed only in the test;
runtime path serialization and API schemas are unchanged.

| ROADMAP acceptance criterion | Status | Evidence | Still required |
|---|---|---|---|
| Clean install succeeds from public documentation | **BLOCKED / REQUIRES LIVE VERIFICATION** | `README.md` documents clone, `.env`, API-key generation, vault mapping, source build, liveness, readiness, and authenticated `/api/v1` smoke testing. The repository was private during the audit and Docker was unavailable locally. | Make the source/docs public, then run the isolated clean-install procedure below with disposable data. |
| CI is green | **PASS** | `.github/workflows/ci.yml` represents pytest, compileall, Compose validation, and an image build. Checksum-verified actionlint 1.7.12 passed. [GitHub run 32857711831](https://github.com/mrtrollex/VaultBridge/actions/runs/32857711831) passed both `python` and `docker` on the audited `main` commit. | Require both jobs again on the exact RC/stable commit. Branch protection was unavailable on the private repository, so this remains a manual release gate. |
| Container image is published | **FAIL** | `.github/workflows/publish-ghcr.yml` is release-triggered and statically valid, but the repository had no GitHub Releases. The workflow alone is not a published image. Package enumeration also requires `read:packages`, so no separate private-package claim is made. | Publish and verify `v1.0.0-rc.1` through the RC procedure. Do not publish stable first. |
| Public API is versioned | **PASS** | `app/api/versioning.py` registers six protected legacy/v1 route pairs from shared handlers. `tests/test_api_versioning.py` passed 24 tests; `tests/test_api.py::test_router_registration_preserves_public_contract` checks all 15 public schema paths and operation IDs. Health and `/privacy` intentionally remain unversioned. | Re-run contract tests on the release commit. Keep legacy aliases until a separate migration decision. |
| Full semantic rebuild does not block ordinary request handling | **PASS** | `tests/test_indexer.py::test_application_startup_is_non_blocking_and_shutdown_waits_for_sync` proves lifespan startup completes while full sync is blocked. `test_ordinary_http_requests_continue_during_full_rebuild` proves liveness and authenticated non-semantic `/api/v1/notes/list` requests succeed during blocked full sync. Semantic tests prove an older compatible index remains searchable during and after a failed refresh. | Preserve the precise guarantee: initial semantic search waits for readiness, and shutdown can still wait on uninterruptible model/filesystem work. |
| Retrieval evaluation suite exists | **PASS** | `python -m pytest -q tests/eval` passed 9 tests. The deterministic production-pipeline fixture covers 13 cases: all 13/100% Hit@1/100% Hit@3/100% MRR; English 8/100%/100%/100%; Slovak 4/100%/100%/100%; cross-language 1/100%/100%/100%; heading context 2/100%/100%/100%. | Re-run without changing `tests/eval/baseline.json` merely to clear a gate. This does not measure real-model accuracy or latency. |
| No known authentication/path-traversal bypass | **PASS** | VB-057 centralizes resolved-target containment for literal search, listing, and semantic enumeration and uses the validated resolved path for stat/read. Real WSL/Linux service and legacy/v1 route tests cover external file and directory links, broken links, safe internal aliases, folder scope, direct reads, and semantic cleanup. The VB-058 focused Linux selection passed 52 tests with no symlink skips; native Windows passed the full suite with only privilege-dependent symlink skips. | Re-run Linux symlink regressions and the full suite in CI on the exact RC/stable commit. The proportionate path-based design does not claim an OS file-descriptor sandbox against hostile concurrent filesystem mutation. |
| Secrets are not committed or logged | **PASS** | Only example `.env` files are tracked; `.gitignore` and `.dockerignore` exclude secret/runtime data. HEAD and all reachable revisions had no high-confidence token/private-key pattern hits; no real `.env` appeared in history. Tracked `API_KEY=` assignments were placeholders/references. Logging/observability passed 43 tests and verify bodies, queries, credentials, headers, exception messages, and absolute paths are omitted. | Repeat on the final commit. This was a targeted history scan, not an exhaustive entropy-based external audit. Never paste resolved Compose output or environments. |
| Upgrade/rebuild procedure is documented | **PASS** | `README.md` covers source updates, automatic signature invalidation, stopped-service `index check`, explicit `index rebuild`, and Markdown as source of truth. `README_TRUENAS.md` covers managed/source updates, bundles, rollback, automatic incompatibility rebuilds, stopped-service inspection/rebuild, and recovery. Commands match current deployment files and CLI. | Recheck if deployment files change. |

ROADMAP checkboxes may be checked only for the seven criteria marked `PASS`. The failed and live-only
criteria remain unchecked.

## Release blockers and exact remaining work

1. Align the existing `pyproject.toml` version and `app.main.APP_VERSION` with `1.0.0` on the final
   release-preparation commit. Do not add another version source or cosmetic version endpoint.
2. Make the repository and release documentation publicly accessible.
3. Run the isolated clean-install smoke test below from the exact verified commit.
4. Publish and verify `v1.0.0-rc.1`: GHCR visibility/linkage, exact tag, digest, platform, runtime
   health/readiness, authenticated v1 behavior, and no prerelease `latest` update.
5. Repeat the public clean-install and pulled-image smoke checks against the RC tag/digest.
6. Fix every RC blocker, rerun all checks on the exact final `main` commit, then execute the stable
   release procedure.

## Supported platform statement for v1.0.0

- Production runtime: the repository Dockerfile running as a Linux container, built from source or
  pulled from GHCR after publication.
- TrueNAS: the documented source-built Custom App/shell Compose workflow on Docker-based TrueNAS
  SCALE 24.10 or later.
- Windows/macOS hosts: Docker Desktop may run the Linux container. Native Windows is a development
  and test environment, not a documented production deployment.
- Published architecture: the current workflow builds only the GitHub-hosted runner's native Linux
  architecture. Verify the RC manifest; do not claim ARM64/multi-architecture support before VB-055.

## Version and artifact contract

- The immutable release identity is the `v1.0.0` Git tag and GitHub Release from one verified commit.
- GHCR tags omit `v`. `1.0.0` must identify the image built from that release tag; `1.0`, `1`, and
  `latest` are mutable stable aliases to the same digest.
- A prerelease such as `v1.0.0-rc.1` publishes only `1.0.0-rc.1` and must not update stable aliases.
- Immutable deployments use `ghcr.io/<lowercase-owner>/vaultbridge@sha256:<digest>`.
- `pyproject.toml` and `app.main.APP_VERSION` both currently say `0.1.0`; align those existing fields
  during final release preparation. They do not replace tag/release and GHCR digest identity.

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

Use a disposable empty vault, never a production/TrueNAS vault. Set `RELEASE_REF` to the exact RC tag
or commit.

```bash
set -eu
RELEASE_REF=v1.0.0-rc.1
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

## `v1.0.0-rc.1` procedure

Do not execute this sequence until the remaining source gates are closed.

1. Verify clean `main` and green `python`/`docker` CI on the exact commit.
2. Record the current digest or absence of `latest` before publication.
3. Create tag `v1.0.0-rc.1` and publish a GitHub **prerelease** with the draft notes below.
4. Require `Publish GHCR image` jobs `Verify release source` and `Build and publish` to succeed.
5. Verify package `vaultbridge` exists, is linked to this repository, and has intended visibility.
   Deliberately make it public if anonymous pulls are intended.
6. Verify exact tag `1.0.0-rc.1`, inspect its manifest, and record digest/platform. Confirm `1.0.0`,
   `1.0`, `1`, and `latest` were not created or updated by the prerelease.
7. Pull the exact tag and digest; confirm both resolve to the recorded manifest.
8. Run the pulled image with disposable vault/data. Verify start, `/health/live`, initial and eventual
   `/health/ready`, and authenticated `/api/v1/notes/list`.
9. Inspect logs for safe lifecycle output and no secret/header/query/content/absolute-path exposure.
10. Record workflow/package URLs, visibility/linkage, tag, digest, platform, smoke evidence, and the
    unchanged/absent `latest`. Fix blockers before stable release.

## Stable `v1.0.0` procedure

1. Confirm every ROADMAP criterion and checklist blocker is closed with current evidence.
2. Verify the exact final `main` commit and successful `python`/`docker` CI checks.
3. Align existing version metadata, finalize `CHANGELOG.md`, and prepare final release notes.
4. Create immutable tag `v1.0.0` and publish the GitHub Release from that commit.
5. Require both GHCR publication jobs to succeed.
6. Verify GHCR tags `1.0.0`, `1.0`, `1`, and `latest` resolve to the stable digest.
7. Pull by digest and run the final disposable-vault smoke test.
8. Verify package linkage/visibility, manifest platform, health/readiness, authenticated v1 behavior,
   and safe logs.
9. Record release URL, commit, CI/publish runs, tags, digest, platform, visibility, and smoke evidence.
10. Only then mark the remaining ROADMAP v1.0.0 criteria complete.

## Draft `v1.0.0` release notes

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
