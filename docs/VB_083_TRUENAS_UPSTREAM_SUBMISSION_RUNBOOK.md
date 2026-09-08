# VB-083 TrueNAS upstream submission runbook

**Status:** Submission-readiness audit prepared on 2026-09-08. No issue, fork, branch, commit, push,
pull request, reviewer contact, release change, or TrueNAS mutation was performed.

## Current readiness decision

VB-083 is **BLOCKED before PR opening** on two contributor-controlled actions:

1. the current app-addition PR template requires an issue to be opened before the pull request, and
   no VaultBridge issue or pull request exists in `truenas/apps` as of this audit;
2. the package default changed from now-occupied port `30486` to free port `30488`, so the full
   current-upstream Docker-backed generation, dev-catalog, render, deploy, health, cleanup, and
   middleware negative-schema validation must be rerun before opening the PR.

The final `media.sys.truenas.net` icon URL is **REQUIRES UPSTREAM REVIEWER ACTION**. It does not block
opening the issue or PR: the current contributor guide and app-addition template direct the
contributor to attach or link the icon, after which a reviewer uploads it and supplies the CDN URL.
The raw immutable GitHub icon URL remains a truthful review placeholder until that action. It will
not satisfy final metadata validation or merge readiness.

VB-082 remains **IN PROGRESS / PARTIAL VALIDATION**. VB-083 is not submitted or complete.

## Contract snapshot

- Pinned validated upstream commit: `906a20a22ee885add8c620660eba3d6ed51289da`.
- Live upstream commit audited: `a61f1bf5a63c7bdf39df50ead789a98e24cdb6d5`.
- Current upstream base/default branch: `master`.
- Destination: `ix-dev/community/vaultbridge/` in `truenas/apps`.
- Latest non-v1 library: `2.3.11`; generated directory `templates/library/base_v2_3_11/`.
- The current `CONTRIBUTIONS.md` is content-equivalent to the pinned guide for this audit.
- Changes since the pinned commit did not modify the contributor guide, validation scripts, or
  library, but newly merged apps allocated ports `30486` and `30487`.
- Current port validator result before adding VaultBridge: next free ports `30488` through `30492`.
- Upstream currently has no `ix-dev/community/vaultbridge/` entry and no issue or PR matching
  `VaultBridge`.

The upstream guide does not prescribe a contributor branch name. `add-vaultbridge` below is a
recommended local/fork branch name, not an upstream requirement.

## Package audit

The proposed source tree contains all current required categories:

```text
ix-dev/community/vaultbridge/
├── app.yaml
├── item.yaml
├── ix_values.yaml
├── questions.yaml
├── README.md
└── templates/
    ├── docker-compose.yaml
    ├── library/base_v2_3_11/       # 78 generated files
    └── test_values/
        ├── basic-values.yaml
        ├── host-path-data-values.yaml
        └── watcher-enabled-values.yaml
```

No `templates/rendered/`, migration, second service, or alternate runtime tree is retained.
`app_migrations.yaml` and `migrations/` are not required for an initial package.

Audited invariants:

- `app.yaml`: slug/name `vaultbridge`, train `community`, package `1.0.0`, app `1.1.0`, date added
  `2026-09-07`, library `2.3.11`, generated library hash
  `874636814efb275e5276ea9d709b7cd665fed42bb1d50328e853d9253a2e1229`, factual title,
  description, home, changelog, source, category, keywords, TrueNAS maintainer, and non-root
  `568:568` context.
- Current metadata/schema conventions do not require a separate minimum-TrueNAS-version field for
  this package; the unchanged metadata passed the pinned official validator without one.
- `item.yaml`: present and consistent with categories, keywords/tags, screenshots, and the current
  review icon. Its Git/LF-normalized SHA-256 is
  `c567702b80a7141e1f821eaa7ee4da3270f23206e98e90f3ff975334064da233`; Windows CRLF working-tree
  bytes naturally produce a different raw hash.
- `questions.yaml`: current key required/private/minimum 32 characters; previous key optional/private;
  watcher false; debounce `1`; selected non-root UID/GID `568:568`; vault host path; default ixVolume
  and host-path `/data` alternative; explicit ACL controls; Community App port `30488`; CPU `2`;
  memory `4096` MB; optional host IPs, networks, and labels.
- template: published `ghcr.io/mrtrollex/vaultbridge:1.1.0`; one VaultBridge service plus only the
  conditional standard permissions helper; non-root selected identity; `/vault` read/write by
  default; selected `/data`; no `/vault` permissions action; bridge networking; no host networking;
  library-default `cap_drop: [ALL]`; `/health/live`; Portal `/ui/`; watcher values passed through;
  no runtime fork or second application service.
- fixtures: basic, watcher-enabled, and host-path-data exist; all use port `30488`, UID/GID `568:568`,
  CPU `2`, memory `4096`, synthetic placeholder credentials, and `/opt/tests` fixture paths. Neither
  obsolete Community App port `30000` nor generic-only default `8765` appears in the package.
- generated artifacts: `item.yaml` and the 78-file `base_v2_3_11` library are present; no rendered
  Compose output is tracked. Regeneration after the port-only change is still required as a
  current-upstream proof, even though neither generated artifact is expected to change.
- public references: repository, releases, `v1.1.0` release, TrueNAS guide, immutable-source icon,
  and anonymous `ghcr.io/mrtrollex/vaultbridge:1.1.0` are reachable. The tag still resolves to OCI
  index `sha256:753e613617d221c3dac311600a36cab3f2727b09f630321664eaa7b7ad6eb48c`.

The source scan found no private credentials, Authorization headers, private hosts/addresses,
private user paths, VB-082 values, or production data. The fixture credential and `/opt/tests` paths
are deliberate upstream test placeholders. Test-only examples inside the generated upstream library
are generated third-party validation artifacts, not VaultBridge operator data.

Current audit checks:

- non-template YAML parsing: **PASS**;
- package metadata/questions/template/fixture invariant assertions: **PASS**;
- generated library comparison against current upstream `library/2.3.11`: **PASS**, 78 matching files;
- Git/LF-normalized `item.yaml` SHA-256: **PASS**;
- current upstream port validator with the exact staged package at `30488`: **PASS**; next free ports
  then became `30489` through `30493`;
- package privacy scan: **PASS**;
- public repository/release/documentation/icon references: HTTP `200`;
- anonymous GHCR tag lookup: HTTP `200`, expected OCI index digest;
- full Docker-backed generation/dev-catalog/render/deploy/health/cleanup and middleware negative
  matrix: **NOT RUN in this audit**, because Docker and the upstream validation executables were not
  available locally. The pinned VB-081 run remains historical evidence, not a substitute for this
  required current-package rerun.

## Submission phases and acceptance boundary

| Phase | Truthful state |
|---|---|
| Local package preparation | Complete after port correction and repository-local audit |
| Required app-addition issue | Contributor action; not opened |
| Current-upstream full validation | Contributor action; must be rerun with Docker at `30488` |
| PR opening | Blocked until the issue exists and current validation passes |
| Icon CDN replacement | Reviewer action during PR review; does not block PR opening |
| PR review changes and green upstream CI | Requires an open PR and contributor/reviewer iteration |
| Upstream merge | Requires maintainer approval; not implied by an open PR |
| Catalog/Discover availability | Requires merged/generated/distributed catalog state |
| VB-082 catalog-only validation | Requires the real delivered catalog surface |
| VB-083 completion | Only after its backlog acceptance criteria and upstream state are satisfied |

## Next operator workflow

### 1. Open the required app-addition issue

Search issues and PRs again immediately before creating anything:

```bash
gh search issues --repo truenas/apps --state open --match title,body 'VaultBridge'
gh search prs --repo truenas/apps --state open --match title,body 'VaultBridge'
```

Suggested issue title:

```text
App request: VaultBridge
```

Suggested issue body:

```text
Please add VaultBridge to the community train.

Upstream: https://github.com/mrtrollex/VaultBridge
Documentation: https://github.com/mrtrollex/VaultBridge/blob/main/README.md
TrueNAS deployment notes: https://github.com/mrtrollex/VaultBridge/blob/main/README_TRUENAS.md
License: MIT
Application version: 1.1.0
Image: ghcr.io/mrtrollex/vaultbridge:1.1.0

VaultBridge is a self-hosted REST and semantic-search bridge for Obsidian Markdown vaults. The
proposed package runs as a configurable non-root user, mounts the authoritative vault separately
from rebuildable derived data, exposes one Web Portal at /ui/, and uses /health/live for container
health.
```

Retain the resulting issue URL. Do not claim this is submission or acceptance.

### 2. Fork, clone, and branch from current upstream master

After explicit authorization to mutate GitHub:

```bash
gh repo fork truenas/apps --clone=false
git clone https://github.com/<GITHUB_USER>/apps.git truenas-apps
cd truenas-apps
git remote add upstream https://github.com/truenas/apps.git
git fetch upstream master
git switch -c add-vaultbridge upstream/master
test ! -e ix-dev/community/vaultbridge
cp -a <VAULTBRIDGE_CHECKOUT>/ix-dev/community/vaultbridge ix-dev/community/
```

If `ix-dev/community/vaultbridge` already exists, stop and reconcile upstream rather than replacing
it. Do not copy VaultBridge project-state documents into the upstream PR.

### 3. Generate and validate in the established Docker-backed upstream environment

Run from the `truenas/apps` root using the current validation environment. Generate first, then
review the exact source diff:

```bash
apps_catalog_hash_generate --path "$PWD" --train community --app vaultbridge
apps_dev_charts_validate validate --path "$PWD"
python3 .github/scripts/port_validation.py
python3 .github/scripts/ci.py --app vaultbridge --train community --test-file basic-values.yaml
python3 .github/scripts/ci.py --app vaultbridge --train community --test-file watcher-enabled-values.yaml
python3 .github/scripts/ci.py --app vaultbridge --train community --test-file host-path-data-values.yaml
git status --short
git diff --check
```

Also rerun the established current TrueNAS middleware question-schema negative matrix. Require all 13
invalid cases to be rejected. Do not retain rendered Compose containing a populated API key.

Expected generated state:

- `lib_version_hash` remains
  `874636814efb275e5276ea9d709b7cd665fed42bb1d50328e853d9253a2e1229` unless upstream library
  `2.3.11` itself changed;
- `templates/library/base_v2_3_11/` contains the generator-owned library;
- `item.yaml` is generator-owned and matches `app.yaml` metadata;
- no `templates/rendered/` output is committed;
- the port validator reports no duplicate and does not reassign `30488` before the PR opens;
- all three fixtures render, deploy, become healthy, and clean up using `30488:8000`.

The current guide's generic “no auto-generated files” checklist conflicts with the current source
validator and accepted app tree, which require generated `item.yaml` and `templates/library/` in
`ix-dev`. Follow the validator-backed package structure: commit those two generated source artifacts,
but never commit generated `trains/`, `catalog.json`, or `templates/rendered/` output.

### 4. Prepare the PR

Suggested title:

```text
Add VaultBridge to the community train
```

Draft body matching the current app-addition template:

```markdown
# App Addition

- [x] I opened <ISSUE_URL> to discuss this app addition before submitting this pull request.

# AI

- [x] Part or all of this PR was generated by an LLM.

## Description

Adds VaultBridge to the community train. VaultBridge is a self-hosted REST and semantic-search
bridge for Obsidian Markdown vaults.

## App Information

- **Upstream**: https://github.com/mrtrollex/VaultBridge
- **Documentation**: https://github.com/mrtrollex/VaultBridge/blob/main/README.md
- **App Version**: 1.1.0

## Testing

Tested locally with:

- [x] basic-values.yaml
- [x] watcher-enabled-values.yaml
- [x] host-path-data-values.yaml

All current upstream generation, dev-catalog, port, schema, render, deploy, health, and cleanup tests
passed. The package uses Community App port 30488; VaultBridge's generic Docker default remains 8765.

## Icons and Screenshots

Please upload the icon to the TrueNAS CDN:

- Icon: https://raw.githubusercontent.com/mrtrollex/VaultBridge/e39ed91db75f912f390c7ec915dea73369bb9252/docs/assets/vaultbridge-logo.webp
- Screenshots: none

## Special Notes

- The selected Obsidian vault is authoritative external data and is never sent to the permissions
  helper.
- `/data` contains rebuildable semantic metadata/model cache and defaults to an ixVolume.
- API-key fields are private UI fields; this is masking, not encrypted secret storage against a
  privileged TrueNAS administrator.
- The Web Portal targets `/ui/`; container health uses `/health/live`.

## Checklist

- [x] App runs successfully locally
- [x] Only modified files under /ix-dev/community/vaultbridge
- [x] README.md included
- [x] Multiple test scenarios tested
- [x] questions.yaml has clear descriptions and follows existing-app structure
- [x] All pre-PR automated checks pass
```

Do not check a validation item until the corresponding current-upstream run has actually passed.
Do not open the PR in this preparation pass.

## Review and post-PR actions

After the PR is opened:

1. record the PR URL and distinguish open, review, approved, merged, and delivered states;
2. ask the assigned reviewer to upload the linked WebP icon and provide the final
   `https://media.sys.truenas.net/apps/...` URL;
3. replace the icon in `app.yaml`, regenerate `item.yaml`, and rerun all required checks;
4. address review feedback without changing VaultBridge runtime/API/UI behavior or adding another
   service;
5. rerun the port validator immediately before each push if upstream master moved;
6. after merge, wait for the package to be generated and delivered through the real catalog before
   claiming availability.

## VB-082 validation after upstream delivery

The supported real catalog surface makes these VB-082 gates testable:

- generated install/question form;
- edit form and stored-value behavior;
- real UI masking of current and previous API-key fields;
- generated Web UI / Portal button targeting `/ui/` on the configured port;
- catalog-only storage UI behavior;
- ixVolume uninstall with **Remove iXVolumes** cleared and selected.

Rollback remains blocked until a second real VaultBridge catalog revision exists. Upgrade remains
**UNSUPPORTED / NO VALID PRIOR PACKAGE STATE** for the initial package and must not be fabricated.

## Evidence and privacy rules

Retain only commit IDs, tool versions, package/app versions, fixture names, status results, bounded
errors, public URLs, generated hashes, and PR/issue state. Never retain API keys, Authorization
headers, populated environments, private hosts/IPs/paths, vault content, rendered secret-bearing
Compose, Docker credentials, or unrelated application logs.
