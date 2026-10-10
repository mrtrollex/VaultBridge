# v1.4.0-rc.2 source freeze and O1 runbook

This rc.2 task prepares source only. Published stable remains **v1.3.0**. Publication of rc.2 is a
separate authorized operational step after review/merge. Nothing here records a future PASS or
authorizes a tag, Release, registry push, upstream mutation or live TrueNAS execution.

## Historical rc.1 evidence and reason for rc.2

`v1.4.0-rc.1` was published and its exact OCI image verified. Its bounded runtime/image canary on
TrueNAS 25.10.7 passed for the checks actually tested. That live work exposed a separate package
rendering bug: watcher disabled could render `SEMANTIC_WATCH_DEBOUNCE_SECONDS=""`. The scoped
package-preparation repair was reviewed, passed CI and merged in PR #128 as `c98c70c`. It does not
change VaultBridge runtime image behavior. rc.2 gives the repaired source a new candidate identity;
never replace the published rc.1 tag/image or relabel its evidence as rc.2.

Real catalog package / Edit App migration remains pending: no supported pre-merge candidate-package
catalog workflow exists. Neither the rc.1 canary nor static package tests prove host reboot, actual
generated-catalog upgrade, Edit App migration, rollback or ixVolume lifecycle gates. P4 stable,
P5 catalog and O3 lifecycle remain pending. No step requires touching the production TrueNAS app;
any future authorized canary must use an isolated disposable app with synthetic data.

## PREPARED — inspect and review source

Review the rc.2 preparation diff and `.agent/review_packet.md` in a separate fresh session. Candidate runtime
metadata is `1.4.0-rc.2` in `pyproject.toml`, `app/main.py:APP_VERSION` and
`app/mcp_server.py:MCP_SERVER_VERSION`. The Git tag and OCI version label are **v1.4.0-rc.2**;
the exact GHCR image tag is **1.4.0-rc.2**, without `v`.

Release-note inventory must come from the actual range, including P1/P2, not the older planning
snapshot:

```bash
git log --no-merges --oneline v1.3.0..HEAD
git diff --stat v1.3.0..HEAD
git diff --name-only v1.3.0..HEAD
git diff v1.3.0..HEAD -- app scripts tests .github/workflows
```

The candidate adds browser Hygiene and cookie-session restoration, Hygiene REST/MCP/CLI,
Query CLI and portable Capture/Promotion CLI. Query/Capture/Promotion network/browser adapters,
Hygiene mutations and public named multi-space serving remain deferred. P2 prepares MCP form and
legacy migration source; it does not deliver public Edit App controls. Historical
[`verify-vb075-image.sh`](../scripts/verify-vb075-image.sh) retains v1.1.0 identities and assumptions;
never use it as RC evidence.

## REQUIRED BEFORE RC PUBLICATION — freeze exact reviewed source

After review and merge, resolve and record the immutable commit containing the reviewed diff.
Do not substitute `main`, another mutable branch, or a guessed future SHA:

Run the following blocks in the same PowerShell session from the reviewed merged checkout.
Record `$reviewedSha` in the private execution record. **STOP if any prerequisite command fails.**
`$ErrorActionPreference` alone does not stop on native non-zero exits; every native command below
uses `Invoke-Checked`, which checks `$LASTEXITCODE` before returning output.

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    $output = & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command failed with exit code $LASTEXITCODE" }
    return $output
}
$reviewedSha = (Invoke-Checked git -Arguments @('rev-parse', '--verify', 'HEAD^{commit}')).Trim()
if ($reviewedSha -cnotmatch '^[0-9a-f]{40}$') { throw 'Invalid reviewed source SHA' }
function Assert-ReviewedCheckout {
    if (@(Invoke-Checked git -Arguments @('status', '--porcelain')).Count -ne 0) {
        throw 'Working tree must be clean'
    }
    $headSha = (Invoke-Checked git -Arguments @('rev-parse', '--verify', 'HEAD^{commit}')).Trim()
    if ($headSha -cne $reviewedSha) { throw 'HEAD changed from reviewed source' }
}
Assert-ReviewedCheckout
Invoke-Checked python -Arguments @('-m', 'pytest', '-q', 'tests/test_release_preparation.py', 'tests/test_smoke_mcp_http.py')
Invoke-Checked python -Arguments @('scripts/agent_check.py')
```

Require independent GitHub CI on that exact SHA: Ruff, full non-E2E, compileall, Chromium E2E,
Compose, image build, MCP dependency/stdio/HTTP smoke. Local checks do not prove CI or a published
image. Retain CI run URL and checked-out source SHA. A review/merge that changes source needs checks
on the final source. Stop on any failed or unavailable required gate.

Local mechanics, with no published labels fabricated:

```powershell
Invoke-Checked docker -Arguments @('build', '-t', 'vaultbridge:v140-rc-prep', '.')
Invoke-Checked python -Arguments @('scripts/smoke_mcp_http.py', '--image', 'vaultbridge:v140-rc-prep')
Invoke-Checked python -Arguments @('scripts/verify_release_image.py', '--local-image', 'vaultbridge:v140-rc-prep',
    '--expected-revision', $reviewedSha, '--expected-version', '1.4.0-rc.2')
```

Local mode checks runtime version and Linux/amd64, but deliberately skips anonymous registry pull,
RepoDigest and OCI source/revision/version/license assertions. Its output is **LOCAL MECHANICS**,
never release-artifact PASS. `--expected-revision` is validated syntax only in this mode; an
uncommitted local build has no reviewed release source identity.

## REQUIRED AFTER AUTHORIZATION — publish GitHub prerelease

The following actions belong to O1 and require explicit publication authorization; **do not run in
P3**. Continue in the PowerShell session above using the recorded reviewed SHA, never a mutable
branch. Prepare the reviewed candidate notes file before starting. An existing local tag is a STOP
requiring investigation. An existing remote tag may be used only after independently fetching and
dereferencing it to the same reviewed commit; it is never moved or replaced.

```powershell
$tag = 'v1.4.0-rc.2'
$releaseRepository = 'mrtrollex/VaultBridge'
$releaseRemote = "https://github.com/$releaseRepository.git"
$notesFile = '<reviewed-candidate-notes-file>' # Replace with the prepared local notes file.
if (-not (Test-Path -LiteralPath $notesFile -PathType Leaf)) { throw 'Reviewed notes file missing' }
if ($reviewedSha -cnotmatch '^[0-9a-f]{40}$') { throw 'Invalid reviewed source SHA' }

function Resolve-RemoteReleaseCommit {
    $refs = @(Invoke-Checked git -Arguments @('ls-remote', '--tags', $releaseRemote,
        "refs/tags/$tag", "refs/tags/$tag^{}"))
    if ($refs.Count -eq 0) { return $null }
    # Exact remote ref only; --no-tags avoids overwriting/creating a local release tag.
    Invoke-Checked git -Arguments @('fetch', '--no-tags', $releaseRemote, "refs/tags/$tag") | Out-Null
    $commit = (Invoke-Checked git -Arguments @('rev-parse', '--verify', 'FETCH_HEAD^{commit}')).Trim()
    if ($commit -cnotmatch '^[0-9a-f]{40}$') { throw 'Invalid remote tag commit' }
    return $commit
}

$localRefs = @(Invoke-Checked git -Arguments @('for-each-ref', '--format=%(refname)', "refs/tags/$tag"))
if ($localRefs.Count -ne 0) { throw 'Local candidate tag already exists; investigate before continuing' }
$remoteBefore = Resolve-RemoteReleaseCommit
if ($null -ne $remoteBefore -and $remoteBefore -cne $reviewedSha) {
    throw 'Existing remote tag differs from reviewed source'
}
Assert-ReviewedCheckout # Clean worktree and exact HEAD immediately before tag creation.
if ($null -eq $remoteBefore) {
    Invoke-Checked git -Arguments @('tag', '-a', $tag, $reviewedSha, '-m', "VaultBridge $tag")
    $localCommit = (Invoke-Checked git -Arguments @('rev-parse', '--verify', "$tag^{commit}")).Trim()
    if ($localCommit -cne $reviewedSha) { throw 'Local tag differs from reviewed source' }
    Invoke-Checked git -Arguments @('push', $releaseRemote, "refs/tags/$tag")
}
# Always fetch/dereference independently after push (or when the matching remote tag existed).
$remoteAfter = Resolve-RemoteReleaseCommit
if ($remoteAfter -cne $reviewedSha) { throw 'Remote tag differs from reviewed source' }

Invoke-Checked gh -Arguments @('release', 'create', $tag, '--repo', $releaseRepository,
    '--verify-tag', '--prerelease', '--title', "VaultBridge $tag", '--notes-file', $notesFile)
$release = (Invoke-Checked gh -Arguments @('release', 'view', $tag, '--repo', $releaseRepository,
    '--json', 'tagName,isDraft,isPrerelease,publishedAt,url')) | ConvertFrom-Json
if ($release.tagName -cne $tag -or $release.isPrerelease -ne $true -or
    $release.isDraft -ne $false -or [string]::IsNullOrWhiteSpace($release.publishedAt)) {
    throw 'Published GitHub Release identity/state mismatch'
}
$publishedCommit = Resolve-RemoteReleaseCommit
if ($publishedCommit -cne $reviewedSha) { throw 'Published release tag differs from reviewed source' }
```

Any tag creation, comparison, fetch, push or GitHub command failure terminates the procedure.
Do not continue to GHCR/runtime gates after a failed post-creation read-back; stop and investigate
without retagging or replacing the Release. The explicit GitHub repository and remote URL ensure
tag inspection, push and Release read-back address the same repository. The Release source is the
verified tag commit, not GitHub's potentially mutable target-branch display field.

GitHub Release must be published, non-draft and `prerelease=true`. Observe
`.github/workflows/publish-ghcr.yml`: release API validation, tag dereference and checked-out SHA
must match `$reviewedSha`; publish checks out that verified SHA and labels the image with it.
The prerelease SemVer guard rejects `v1.4.0-rc.2` with `prerelease=false`.
Metadata generates only exact tag `1.4.0-rc.2`, with `latest=false`. The `Publish stable aliases`
job has `if: needs.verify.outputs.release_is_prerelease == 'false'` and must be **skipped**.
Do not move `1.4`, `1`, `latest` or any stable rolling alias. Existing v1.3.0 stable aliases stay
untouched by this RC. Record the skipped job and exact published tag/digest, not only build success.

### Recovery dispatch is for an existing published Release only

Recovery first queries the existing GitHub Release and rejects drafts/unpublished or mismatched
prerelease state. It cannot build an unpublished candidate. If authorized recovery is necessary:

```powershell
Invoke-Checked gh -Arguments @('workflow', 'run', 'publish-ghcr.yml', '--repo', $releaseRepository,
    '-f', 'release_tag=v1.4.0-rc.2', '-f', "expected_source_sha=$reviewedSha", '-f', 'expected_prerelease=true')
```

All three inputs are required: `release_tag`, `expected_source_sha`, `expected_prerelease`.
Keep `expected_prerelease=true` for RC recovery. A different workflow commit is not the release
source; verified checkout and OCI revision must still identify the frozen reviewed source.

## REQUIRED AFTER RC PUBLICATION — exact artifact gates

Obtain the exact OCI index digest from the successful workflow output and independently inspect it:

```powershell
$image = 'ghcr.io/mrtrollex/vaultbridge@sha256:<published-64-hex-digest>'
Invoke-Checked docker -Arguments @('buildx', 'imagetools', 'inspect', $image)
Invoke-Checked python -Arguments @('scripts/verify_release_image.py', '--image', $image,
    '--expected-revision', $reviewedSha, '--expected-version', '1.4.0-rc.2')
Invoke-Checked python -Arguments @('scripts/smoke_mcp_http.py', '--image', $image)
```

Replace the obvious digest placeholder only with observed publication evidence. Require agreement
among RC tag commit, GitHub prerelease source, workflow verification/publish checkout and OCI
`org.opencontainers.image.revision`. No mutable image tag substitutes for the digest.

The reusable Python gate requires an empty Docker credential configuration for anonymous pull,
exact RepoDigest, Linux/amd64 and OCI labels: source `https://github.com/mrtrollex/VaultBridge`,
revision exact reviewed SHA, version `v1.4.0-rc.2`, licenses `MIT`. Runtime APP/MCP/FastAPI version
must instead be `1.4.0-rc.2` before functional assertions.

It uses a unique system-temporary root, isolated synthetic vault/data and generated credentials,
loopback port, UID/GID 568 and all capabilities dropped. It tests health/live/ready, bounded real
semantic startup, rich safe health/Overview, explicit UI/assets/CSP/no catch-all, cookie
unlock/restore/wrong key/logout/invalid cookie, packaged Hygiene findings with unchanged vault
hashes, authenticated list/read/literal/semantic/relationships/backlinks and auth denial.
After clean stop it runs offline CLI status/index check/search/related/query/hygiene plus
Capture/Promotion parser help, then restarts against the same derived-data mount and checks search,
authoritative bytes and synchronization counters proving two unchanged notes and zero reindexed notes. It audits at most 5,000 log lines (2 MB maximum), rejects credential,
cookie, confidential marker/header/host-root leaks and requires clean stop/removal and safe cleanup.
Cleanup checks exact root/parent/prefix and absence; any failure fails the gate.

MCP smoke independently checks HTTP disabled, exact eight read-only names:
`knowledge_hygiene_scan`, `list_notes`, `read_note`, `search_notes`, `related_notes`,
`duplicate_candidates`, `note_links`, `note_backlinks`; current/previous keys; missing/invalid Bearer;
invalid Host/present Origin; Hygiene and official-client server version. Opt-in writes add exactly
`create_note`/`append_note` (ten total), synthetic create/append/dedupe/read-back and note Resource,
then clean shutdown/cleanup. Shutdown preparation waits at most 1,200 seconds for the
indexer to settle: initial compatibility-fingerprint work may download model artifacts even for
an empty vault. The zero-exit stop assertion remains strict. Version is checked through an explicit
official legacy initialization handshake; the existing 2026 tool connection adopts its protocol
without initialization. Query/Capture/Promotion MCP tools do not exist.

Full browser interaction, protected-data cleanup and key-rotation regression evidence comes from
the exact-source Chromium suite. The image gate proves packaged assets/session/API functionality,
not every browser transition. Synthetic Capture/Promotion mutations are covered separately by
`tests/test_capture.py`, `tests/test_promotion.py` and CLI tests, never concurrent with an index writer.

Record source/tag/Release URL, CI/publish/recovery run URLs, OCI index and runtime manifest,
platform/labels, anonymous pull and both smoke outputs, clean cleanup and execution UTC. Keep only
sanitized outcomes: no complete environment, cookies, Authorization headers, keys, private paths,
vault bytes or review output. **STOP before live TrueNAS on any artifact identity/runtime failure.**

## REQUIRED BEFORE STABLE — authorized O1 TrueNAS canary

Refresh actual current Community package immediately before execution. P2 pinned development
package **1.0.3 / image 1.3.0**; 1.0.2 is historical retained-state compatibility only. Follow
[P2 preparation](../ix-dev/preparations/vaultbridge-mcp/README.md), the approved
[productization plan](V140_RELEASE_PRODUCTIZATION_PLAN.md) O1 gates and
[lifecycle runbook](VB_082_TRUENAS_LIFECYCLE_RUNBOOK.md). Do not mutate upstream in O1.

The rc.1 bounded runtime/image canary passed only its tested checks. rc.2 runtime/image checks
must be executed against its own published digest. The following remain **NOT YET VERIFIED**:
current-package to candidate migration, saved non-default
Host and Origin migration, accepted Origin and denied Origin HTTP 403 after migration, Edit App
persistence, host reboot, actual generated catalog upgrade, rollback/recovery and new-package
ixVolume retain/remove behavior. The candidate-package catalog path is unavailable before upstream
merge; record migration gates as pending rather than substituting hand-merged environments or
claiming PASS. For a separately authorized disposable runtime canary, prove copied synthetic
Markdown hashes and derived-state reuse; never use production notes/keys or touch the production
app. Docker/custom-package proof cannot close actual public-catalog gates. Failures require a
separately scoped reviewed fix and new RC.

## POST-CATALOG — P5/O3 remain pending

P4 stable preparation/publication requires successful O1 and explicit authorization. P5 requires
a real verified stable image and fresh upstream identity. O3 must then repeat migration, saved
allowlists/runtime denial, Edit App/lifecycle/rollback and retain/remove proof on the actual
generated catalog upgrade. None of these stages is complete from source preparation.
