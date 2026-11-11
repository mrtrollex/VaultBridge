# VB-153 — Post-Knowledge-Spaces release readiness

Verified on 2026-11-11. Documentation and planning only; no version, runtime, release, tag,
image, alias, catalog or deployment was changed. The corrected task-owner instruction preserves
published v1.4.0 and plans the next Knowledge Spaces release provisionally as **v1.5.0**.

## Verified repository and release state

| Item | Observation and source |
| --- | --- |
| Worktree baseline | Clean `docs/vb-153-release-readiness`, tracking `origin/main`; HEAD and live remote main both `246178eb679b0393c7dda3e07fc7cc613115810c` before task artifacts/edits. No fetch/merge/reset needed. |
| Published stable | [v1.4.0](https://github.com/mrtrollex/VaultBridge/releases/tag/v1.4.0), published `2026-10-10T18:13:27Z`, non-draft, non-prerelease; also returned by GitHub's latest-release endpoint. |
| Stable source identity | Annotated tag object `714c849c53afa7f0747f2e377d80d17d790c3d46` resolves to commit `16488735e7a19a6283707a1b6995249432916ca8`. `target_commitish=main` is not an immutable source identity. |
| Stable publication | [38074909687](https://github.com/mrtrollex/VaultBridge/actions/runs/38074909687): `Verify release source`, `Build and publish`, `Publish stable aliases` all succeeded on that stable source. This verifies publication, not a newly executed digest pull or deployment. |
| Historical RC | [v1.4.0-rc.2](https://github.com/mrtrollex/VaultBridge/releases/tag/v1.4.0-rc.2), source `01f3c105b9e5b787e91914ea89879881dd988fb4`; workflow [38059164718](https://github.com/mrtrollex/VaultBridge/actions/runs/38059164718). Recorded OCI index `sha256:6be3f86fd01b4b337704a17f8b8b8da57d6fb5ab62281344e18638acbf16a02f`. Its functional/MCP/canary receipts apply only to that image. |
| VB-151 / VB-152 | Task owner confirms independent acceptance of VB-151 A–G and VB-152 A–D. [PR #150](https://github.com/mrtrollex/VaultBridge/pull/150) is merged as `a35fb060d61f5d6db837a54aeba90467df6b3983`; [PR #151](https://github.com/mrtrollex/VaultBridge/pull/151) is merged as current main. Earlier accepted PRs #145–149 are ancestors of main. Acceptance is supplied by the task owner; merge state is independently inspected. |
| CI #320 | [42512587414](https://github.com/mrtrollex/VaultBridge/actions/runs/42512587414), repair PR head `8e7610f421c08cdbda97b6fca673982eb00b1fd3`, `pull_request`: python/docker/dashboard-e2e succeeded. |
| Exact-main CI #321 | [42513741045](https://github.com/mrtrollex/VaultBridge/actions/runs/42513741045), `push`, head `246178eb679b0393c7dda3e07fc7cc613115810c`: all three jobs succeeded. This is stronger exact-main evidence than #320. |
| Release/tag inventory | GitHub releases and tags include v1.0.0-rc.2, v1.0.0, v1.1.0, v1.2.0, v1.2.1, v1.3.0, v1.4.0-rc.1, v1.4.0-rc.2 and v1.4.0. No v1.5.0 release/tag was observed. No GitHub issue matching VB-153 was found; the corrected user task and local task file bound this work. |
| Version metadata | `pyproject.toml:project.version`, `app/main.py:APP_VERSION`, `app/mcp_server.py:MCP_SERVER_VERSION` all remain `1.4.0`. No bump is authorized here. |

Read-only checks used `git status`, `rev-parse`, `ls-remote`, tag/ancestry/diff inspection,
GitHub releases/tags/annotated-tag/PR/run APIs and pinned upstream catalog files. Local machine
artifacts are retained under ignored `.agent/`; durable identities and links are above. No GHCR
pull, model download, live client, container or TrueNAS execution was performed in VB-153.

## Source/version discrepancy and recommendation

**Recommend one new v1.5.0 track based on the accepted current-main functionality.** Preserve all
v1.4.0 identities and historical evidence. The old proposal to finish a pending v1.4.0 is obsolete:
publication already happened. Version strings in current main identify neither the published
v1.4.0 source nor its image. Between that stable tag and main, `app/` changes span 55 files,
including scoped adapters, public composition, native ownership, scheduling and watchers.
This is materially more than the historical metadata-only rc.2-to-stable transition.

| Strategy | Version/user expectations and compatibility | Identity, upgrade risk and duplicate work | Decision |
| --- | --- | --- | --- |
| A — Publish historical v1.4.0, Knowledge Spaces later | Historically coherent when stable was pending; v1.4.0 has CLI-only Query/Capture/Promotion and no public named serving. | Already executed. Repeating publication, moving its tag or rebuilding/replacing its exact image would confuse immutable provenance and cannot qualify new code. Continuing to offer the existing baseline is valid but does not deliver Knowledge Spaces. | Preserve the existing stable baseline; no further v1.4.0 publication task. |
| B — Qualify current functionality as provisional v1.5.0 | A minor feature release communicates additive scoped REST/MCP/CLI behavior. Legacy omission/default wire contracts, aliases/IDs, authentication and v1 bytes remain; named mode is explicit opt-in. No mandatory legacy migration is selected. | New source and image need qualification. Reuse accepted native regression evidence with verified source equivalence, then test the actual immutable image and bounded deployment. This avoids another historical-track release cycle. | Recommended, on the corrected basis that v1.4.0 is already stable. |
| Put current main under v1.4.0 / bypass RC | Same version would describe distinct public behavior; a patch also understates the new feature surface. | Old canary cannot identify or validate new native/container behavior. Retagging or alias-only promotion hides source/build differences. | Reject. |

The recommendation is a planning decision, not a version bump or publication authorization.
Named Dashboard/session, maintenance/status, registry discovery, root/ID rebind and artifact
migration remain fenced or deferred. Deployment keys authenticate the deployment, not tenants;
space policy/capabilities grant operations. Scope selection is not a new account/RBAC system.
See the accepted [adapter contract](../BACKLOG.md#vb-152--permission-aware-query-and-write-adapter-integration--p1-slices-a-b-and-c-private-activation-blocked)
and [operator guide](KNOWLEDGE_SPACES.md).

## Release evidence ledger

Classes: **R** = previously verified evidence still applicable within its stated boundary;
**H** = evidence tied to an older source/image; **N** = required on the selected new source or
immutable image; **E** = external operational evidence unavailable here. A passing source suite
is not deployment qualification. Reports are retained evidence, not tests rerun by this task.

| Requirement | R — reusable evidence | H — older identity / limits | N — new source/image gate | E — unavailable operational proof |
| --- | --- | --- | --- | --- |
| Python CI | #321 python passed exact main: Ruff, full non-E2E including evaluation, compileall. | #320 is PR evidence; earlier reports are not final release-SHA CI. | Successful exact preparation/stable merge-SHA CI and release-source verifier; inspect new failures rather than retrying away assertions. | Future release runs do not exist yet. |
| Docker CI | #321 validates generic Compose, builds normal Dockerfile, imports MCP, runs stdio EOF and HTTP smoke. | Local `vaultbridge:ci` is not a published immutable candidate. | RC/stable publication workflow, exact OCI identity and image-specific functional/MCP gates. | No Knowledge Spaces GHCR image qualification receipt. |
| Dashboard CI | #321 Chromium job passed; legacy UI and named fencing have source coverage. | Historical browser/canary screenshots do not prove a new container's assets/session behavior. | Release verifier's Chromium suite; legacy UI/session/assets smoke and named 404/no-store/cookie denial on exact image. | No new-image browser/deployment result. |
| Exact-source Windows/native Linux | [CI repair report](VB152_D_CI_REPAIR_VERIFICATION.md): Windows 3890/0/316; native WSL/Linux 4114/0/92 (pass/fail/skip), 4206 collected; exact reference parity. Repair head and main have no diff under app/tests/scripts. | Executed pre-merge source manifests/logs are reported, not re-audited raw logs here. Windows 3.12.10 and Linux 3.12.15 are not hosted 3.12.16. | Bind retained manifests to release files/dependencies; execute affected native version/composition checks. Rerun relevant full native suites if manifests cannot establish equivalence or runtime/test/dependency changes invalidate evidence. No artificial skips. | No future release-source native receipt yet. |
| Legacy single-vault compatibility | A–D and repair suites cover default omission, old bounds/wire shapes, route aliases/IDs, legacy startup/UI, exact v1 exports; #321 includes them. | v1.4.0/rc.2 image behavior is baseline only. | Exact candidate with no named JSON: health/readiness, authenticated aliases/read/search/Query, legacy UI and stopped-service CLI, preserved Markdown/index behavior. | Actual operator upgrade is not supplied. |
| Named REST/MCP/CLI end to end | [D report](VB152_D_VERIFICATION.md): 146 composition cases each platform, real stdio/CLI and HTTP transports; accepted A/B/C parsing/policy suites. | Test backend/subprocess coverage is not a built-image mount/config/model proof. | Candidate with default+research, same relative paths, explicit/omitted/default/federated selection, scoped Query, resources, CLI profiles and deliberate fences. Existing image scripts alone do not cover this named matrix. | Real external client acceptance is not claimed. |
| Authentication and scoped authorization | A raw-input/cancellation repairs, B strict Query/UTF-8 repair, C write gates and D parent limiter/auth; current/previous Bearer, Host/Origin and denial-before-owner tests. | Stdio trust is local-process/OS permissions; an HTTP key gives no per-user policy. | Image denies missing/bad credentials, invalid Host/Origin, unknown/denied/mixed selections and forged scope; disabled MCP/writes stay absent; named cookies grant no auth. Redact all credentials and paths. | Reverse-proxy/client operational configuration remains operator evidence. |
| Scoped writes and v1/v2 provenance | [C report](VB152_C_VERIFICATION.md), D and repair full suites cover authorized one-space create/append/capture/promotion, exact v1 bytes, v2 approval/replay/conflict and qualified enqueue. | `committed=false` with uncertain outcome does not prove no physical write. Existing image script CLI write checks are parser-only. | Synthetic exact-image legacy v1 and explicit v2 round trips; same IDs/paths in two roots, deny/copy/stale approval/retry cases, exact expected file/byte deltas and no peer mutation. CLI writes only while serving/stdio stopped. | No production write/migration test is authorized. |
| Startup reconciliation/indexing/watcher | [G](VB151_G_VERIFICATION.md), D and repair reports cover actual native watching, shared model/executor, disabled inert owners, policy-authorized startup work, binding/isolation and coverage fencing. | Readiness is asynchronous; watcher start alone is not coverage-restored proof. Synthetic backend scores do not certify real model/BLAS bit identity. | Candidate actual local CPU model/cache/mounts: two enabled owners, disabled peer, external edit isolation, coverage/reconciliation completion, restart persistence and derived-state reuse/rebuild as appropriate. | No new deployment/model/cache receipt. |
| Shutdown and ambiguous cleanup | Native Windows HANDLE and Linux descriptor/reuse/fault tests under accepted ADR 0010; D public shutdown retains same generation. | Accepted opaque cleanup may remain permanently stopping with bounded ownership; process death is not successful in-process release. | Image normal ingress fence/drain/stop and startup-failure evidence; retain native fault coverage via manifest. Unexpected cleanup regression stops release; do not require universal liveness or retry numeric handles. | Operator/container stop/restart receipts are pending. |
| Version/changelog/docs | v1.4.0 source metadata agrees; VB-153 reconciles current status without editing historical outcomes. | Current main still reports 1.4.0 despite new runtime; old release notes correctly describe old source. | Align package/APP/MCP, metadata assertions, changelog, operator/release docs to RC then stable; inspect version labels and runtime versions. | No v1.5.0 metadata/preparation acceptance yet. |
| Container build and OCI digest | Existing workflow pins checked-out SHA, labels source/revision/version/license, publishes exact SemVer and BuildKit provenance; RC skips aliases. | Dockerfile uses `python:3.12-slim`, apt and ranged dependencies: rebuilt identical source need not yield identical bits. v1.4.0 publish success is not a fresh anonymous digest verification. | Record index, linux/amd64 runtime and attestation digests, installed dependency/base/model inventories; anonymous pull and label/runtime verification on exact digest. Explain differences from qualified candidate; no unverified multi-arch claim. | Stable v1.4.0 digest/alias/functional readback was not rerun here; no new candidate digest exists. |
| Container MCP smoke | #321 dependency/stdio EOF/HTTP read-only+opt-in-write smoke succeeds. | [existing smoke](../scripts/smoke_mcp_http.py) uses legacy/default configuration, not full named composition. | Run existing smoke against RC/stable digest and bounded named stdio+HTTP matrix with protocol/resource/auth/write checks and exact synthetic deltas. | External MCP client/production acceptance remains unexecuted. |
| Disposable deployment/restart | Historical rc.2 O1 method and synthetic hash/persistence assertions can be reused as a procedure. | The rc.2 TrueNAS Custom App canary qualifies only its recorded image; not Knowledge Spaces, catalog upgrades or the stable rebuild. | New immutable candidate on disposable supported Linux/container environment: legacy baseline upgrade, explicit named configuration, startup/readiness, edits, stop/start and persistent mounted data; candidate-specific receipts. | No new canary, host reboot or catalog lifecycle proof. |
| Data safety/rollback/recovery | Markdown remains authoritative; independent derived SQLite is rebuildable; cooperating writers and stopped-service rules remain. | No cross-process index lock, atomic multi-root snapshot, hostile-writer/hardlink isolation, forced cleanup or automatic uncertain-write retry. v1.4.0 cannot interpret named mode/v2 as a migration tool. | Back up synthetic Markdown/config/approvals and record prior digest; prove restore to isolated prior layout, named config removal for legacy rollback, no reuse of incompatible bound databases, rebuild when needed, exact expected byte preservation. | Production backup validity/restore, real catalog rollback and storage retention are not verified. |

Historical [A](VB152_A_VERIFICATION.md), [B](VB152_B_VERIFICATION.md), [C](VB152_C_VERIFICATION.md),
[D](VB152_D_VERIFICATION.md) and [G](VB151_G_VERIFICATION.md) reports retain their original
implementation-session review/publication exclusions. Task-owner acceptance, merged PRs and
#321 supersede those pending-current-status phrases; no historical test result is rewritten.
The repair report supersedes B's inherited scheduler-test timing limitation and makes the
synthetic evidence reduction deterministic without changing production scoring or the fixture.

## TrueNAS image/catalog separation

Read-only inspection pinned [truenas/apps commit c85431d5f6ad13eb234bb67a1fc5dc908e05d112](https://github.com/truenas/apps/tree/c85431d5f6ad13eb234bb67a1fc5dc908e05d112)
on 2026-11-11: development `ix-dev/community/vaultbridge` and generated
`trains/community/vaultbridge/1.0.11` metadata report package **1.0.11**, app **1.4.0**.
Both questions files still omit dedicated MCP fields; the generated template forwards
`additional_envs`. Generated `ix_values.yaml` selects
`1.4.0@sha256:48dc17d501cab2ee0fc29da57f6194cd9e08e4215c04a81a88c7c37ab3faa4ee`.
That is an observed catalog reference, not a fresh registry/digest or live installation/migration
verification. No existing artifact was rebuilt or changed.
The checked-in historical `ix-dev` package is 1.0.0/app 1.1.0; the older P2 pin was
1.0.3/app 1.3.0. Neither identifies current upstream. Refresh the upstream pin before delivery.

Application RC/stable image publication does not require a catalog PR or package version bump.
Catalog delivery is a separate task after a verified stable image, with its own explicit authority:

- Deliver dedicated MCP HTTP/write/Host/Origin fields from refreshed upstream; test legacy
  `additional_envs` carry-forward, non-default values, reserved-name conflicts and no duplicates.
- Verify Edit App persistence, accepted/denied Origin behavior and host reboot on a disposable
  TrueNAS installation; a generic container restart is not either of these gates.
- Execute the actual generated public-catalog upgrade, rollback/recovery and ixVolume retention
  and removal choices. Record unsupported paths honestly; do not remove authoritative Markdown.

These remain **NOT YET VERIFIED**. Named settings/mount delivery is not implied by the existing
single-vault catalog form. There was no production TrueNAS access. Historical Custom App O1
does not satisfy any new-image or Community App migration gate.

## Minimal ordered execution plan

Each numbered task is independently reviewable using the normal task/finish/separate-acceptance
workflow. Do not add a second architecture/design review or repeat accepted slice suites merely
because a documentation task completed. Review again when a material repair changes the result.

### 1. Exact release preparation and version alignment

**Scope:** prepare provisional `1.5.0-rc.1` from the accepted baseline plus reviewed documentation;
align package/APP/MCP constants, version assertions (including the current 1.4.0 assertion in
`tests/test_release_preparation.py`), changelog/release notes and a v1.5 runbook. Specify stable
`1.5.0` metadata-only transition. Supply a bounded disposable named-image verification runner or
executable procedure to fill the existing legacy-image-script gap; no new runtime capability.
Document container environment/mount injection explicitly: generic Compose does not forward
`KNOWLEDGE_SPACES_JSON`; TrueNAS env-file/manual overrides are different contracts. Do not
silently promise that putting named JSON in generic `.env` enables named deployment.

**Dependencies:** accepted VB-153 and latest-main/tag inventory refresh. Fix the exact candidate
SHA after normal separate review/authorized merge; never use a mutable branch as release identity.
**Checks:** workflow-selected checks, exact-SHA independent python/docker/dashboard CI; source and
dependency comparison to retained native manifests; focused native metadata/entry-point validation
on Windows and actual Linux, full affected native suites only when equivalence is unavailable or
changes require them. Keep 13-case/v1 reference unchanged. Dry-run the proposed image procedure
against a disposable local build if needed to make it executable, without publication.
**STOP:** mismatched versions/source/dependencies, unexplained failed/skipped required checks,
unavailable native equivalence, missing named-image procedure, or new protected-contract change.
**Evidence:** task/review packet, changed-file/source manifest, CI URLs, platform/dependency
inventories, parity hashes, release notes, exact release authorization checklist.
**Exclusions:** tags, published GitHub Releases, GHCR pushes/aliases, catalog changes and deployment.

### 2. Authorized RC publication and immutable-image qualification

**Scope:** after explicit RC authorization, create new annotated `v1.5.0-rc.1` on the exact reviewed
merged SHA and publish a non-draft GitHub prerelease. Existing workflow then verifies source and
builds/pushes exact `1.5.0-rc.1`; stable aliases must be skipped. Read back tag dereference, Release
flags, source SHA and jobs. Never recover a mismatch by moving a tag or overwriting an identity.
**Dependencies:** task 1 accepted, exact-source CI, concrete SHA/tag/notes and permission to publish
both GitHub prerelease and resulting GHCR artifact. A local draft plan is not this permission.
**Checks:** anonymous immutable pull; index/runtime/attestation digests and OCI labels; runtime
APP/MCP version; existing `verify_release_image.py` and `smoke_mcp_http.py` on the digest; task 1's
named REST/HTTP MCP/stdio/CLI matrix, actual local model/indexing/watcher behavior, auth/denial,
v1/v2 bytes and fence checks. Record the stable alias digests before/after; require no RC movement.
**STOP:** any identity mismatch, alias job running for RC, missing/failed image or named gate,
unexpected vault delta, secret exposure, unexplained build/dependency change or incomplete cleanup.
**Evidence:** tag object/source, Release URL/state, workflow/jobs, OCI and dependency inventory,
immutable verifier/MCP/named logs, hash manifests and disposable resource cleanup receipts.
**Exclusions:** stable Release/aliases, TrueNAS catalog, production mounts and real user content.

### 3. Bounded disposable deployment canary

**Scope:** deploy only the qualified RC digest on an explicitly authorized disposable environment,
using synthetic isolated roots/data and existing non-root ownership. Exercise a baseline-v1.4.0
legacy upgrade separately from explicit named activation, local CPU semantic readiness, policy
denial, equal-path isolation, scoped writes/v2 and unchanged v1; external edit/index isolation,
normal shutdown, actual stop/start and persistent Markdown/config/index behavior. Test recovery
to a separate prior-image/layout copy. Do not run HTTP, named stdio and CLI writes concurrently
against writable derived state. No new framework or broad performance campaign is required.
**Dependencies:** task 2 image gates pass; operator supplies disposable target, mount boundaries,
snapshot/restore method and permission. TrueNAS Custom App may be used only if explicitly chosen;
a Linux Docker canary is sufficient for the application-image gate, without TrueNAS lifecycle claims.
**Checks:** before/after full synthetic path+byte hashes; only approved write deltas; no peer or
disabled-space work; readiness after reconciliation, watcher coverage receipt, saved settings and
data after restart; normal cleanup and demonstrated restore. Compare build/runtime inventories.
**STOP:** non-disposable target, unclear backup/restore, unexpected data/config changes, cross-space
access, failed persistence, unexpected shutdown regression or unresolved new ownership failure.
ADR 0010's already accepted opaque-failure limit remains a documented recovery boundary, not a
demand for forced close or universal eventual stopping completion.
**Evidence:** immutable image/host inventory, redacted config/mounts, start/stop/restart receipts,
hash deltas, readiness/reconciliation/watch observations, recovery and cleanup report.
**Exclusions:** production, catalog install/edit/reboot/upgrade/uninstall, live user backup claims.

### 4. Stable preparation, explicit authorization and publication

**Scope:** prepare/review/merge only the approved RC-to-stable `1.5.0` metadata/notes delta first;
record exact stable SHA and successful independent CI. Then present the concrete SHA, tag,
notes, RC digest/canary receipts, rollback boundaries and expected alias effects for explicit
stable authorization. Only after that authority create `v1.5.0` and its non-prerelease Release.
**Dependencies:** tasks 1–3 successful, stable preparation accepted, no unexplained source changes,
and permission explicitly covering GitHub publication, GHCR and rolling aliases.
**Checks:** all three publication jobs must pass (aliases skipped is failure for stable); record
stable index/runtime/attestation identity, anonymous pull, OCI labels/runtime version and repeat
exact-image legacy+named/MCP functional gates on the **stable digest**. The workflow rebuilds;
it does not promote RC bytes. Compare resolved base/dependencies/model inputs to the canary.
Reuse the RC deployment receipt only for a demonstrated version-only source delta and equivalent
runtime inputs; otherwise stop final acceptance and run a bounded stable-digest canary before
announcing qualification. Always record stable-digest restart/data checks from its functional gate.
Read back exact tag and `1.5`, `1`, `latest` against the new digest; preserve `1.4.0` and `1.4`.
**STOP:** publication/source/artifact/alias failure, conflicting latest-release state, unexplained
build difference, changed runtime behavior or failed stable image checks. Aliases are advanced by
the workflow before post-publication verification; do not describe these checks as a pre-alias gate.
Stop rollout/announcement and obtain a separately authorized recovery decision; do not silently
republish, retag or roll aliases back. Workflow recovery requires the same tag/SHA/prerelease tuple.
**Evidence:** stable-preparation packet/CI; explicit authority; Release/tag/source/workflow/jobs;
stable digests/inventories/alias readback, exact-image logs and final qualified/not-qualified ledger.
**Exclusions:** TrueNAS package delivery, new product features, production deployment, deleting RC
or prior releases, automatic rollback or any claim that successful publication alone is qualification.

## Publication authorization boundary and STOP rules

VB-153 authorizes documentation and planning only. Source preparation and review can be completed
before asking to publish. RC authority and stable authority are separate; neither follows from
task acceptance, green CI, a task file or the availability of GitHub credentials. The existing
release-published trigger pushes GHCR; a stable Release also advances aliases automatically.
Do not publish a stable Release while intending to defer those alias effects.

At every next-task boundary stop on changed source identity, dirty/unreviewed release content,
version/tag/OCI/runtime mismatch, a failed or required-but-unavailable check, unexplained dependency
or native-manifest drift, undocumented compatibility change, unexpected file delta, production
target ambiguity, or missing explicit publication authority. Do not reopen accepted ADRs, weaken
assertions, add sleeps/skips or claim universal cleanup success to force a green gate. New material
runtime findings need their own bounded repair and relevant requalification.

## VB-153 verification and handoff

The implementation preflight is `.agent/task_packet.md`, using `.agent/vb153-task.md`. The shell's
default `python` resolves to the Windows Store stub; the existing development environment is
used by prepending `../VaultBridge/.venv/Scripts` to this process's PATH. No interpreter install or
dependency change is required. Final checks and packet outcome are recorded in
`.agent/vb153-consistency.log`, `.agent/vb153-focused-checks.log` and `.agent/vb153-finish.log`.
Executed documentation checks: **127 passed, 0 failed/skipped** across
`tests/test_release_preparation.py`, `tests/test_truenas_package.py` and
`tests/test_truenas_mcp_preparation.py` (6.68 s). Local file links and new anchors, unchanged
runtime/test/version/ADR/deployment scope, preserved historical command blocks/reports, pinned
GitHub/upstream source claims and `git diff --check` pass. Read-only API snapshots are retained in
`.agent/vb153-github-evidence.json`; `.agent/vb153-document-manifest.json` binds changed documents.

The change-aware selector finds docs plus the README-triggered TrueNAS manual/agent requirement;
no Python/Docker/MCP/browser automated suite is selected for this Markdown-only diff. The manual
**changed-document** gate is satisfied by pinned development/generated catalog inspection, static
Dockerfile/Compose/settings/mount/identity comparison, the focused preparation tests and proof that
all TrueNAS operational procedures/code blocks below the status introduction are unchanged.
This is not a live deployment PASS: catalog delivery, migration, Edit App, reboot, recovery and
ixVolume lifecycle remain open external gates. The selector reports manual requirements even
after the agent records this scoped evidence; its exit status alone does not attest those gates.

The final completion command is `python scripts/agent_finish.py --task-file .agent/vb153-task.md`.
It must pass and regenerate `.agent/review_packet.md`; the separate acceptance session starts
only from that packet. No reviewer or subagent is launched here. Future source/image/deployment
gates above remain open; no release readiness PASS is inferred from documentation checks.
