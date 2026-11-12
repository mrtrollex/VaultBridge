# VB-154 verification — release preparation

**Source preparation handoff; independent acceptance remains separate.** The initial native failure
and aborted workflow remain historical evidence below. The authorized test-only correction and
complete native Linux requalification are verified. The completion receipt is the successful final
`agent_finish.py` log and newly generated `.agent/review_packet.md`; neither this document nor a
focused PASS waives that final gate. No release or deployment qualification is claimed.

The subsequent independent acceptance returned **FIXES REQUIRED** for one runner lifecycle blocker;
the task owner accepted the other release preparation, compatibility and platform evidence. The
bounded cleanup repair and its current verification receipts are recorded in the final section.

Scope is only task 1 of [VB-153](VB153_RELEASE_READINESS.md): unpublished `1.5.0-rc.1` source
metadata, documentation and disposable image qualification tooling. No commit, push, PR, merge,
tag, publication, deployment, catalog change, real data, subagent or independent acceptance occurs.

## Preconditions and source identity

Verified 2026-11-12 before task artifacts/edits: clean worktree on
`release/vb-154-v150-rc1-prep`; HEAD, local `origin/main` and live remote main all
`65958c5a8b124fac899454aef64b7822021cb3e5`. [PR #152](https://github.com/mrtrollex/VaultBridge/pull/152)
was merged at that SHA on 2026-11-11. Exact-main push
[CI #323 / 43016049057](https://github.com/mrtrollex/VaultBridge/actions/runs/43016049057) is completed/success:
python `130507266220`, docker `130507266510`, dashboard-e2e `130507266579` all success. The conditional
failure-artifact upload alone was skipped. This is baseline evidence, not CI for the preparation diff.

Live release and remote tag inventories include stable v1.4.0, published 2026-10-10, and no
v1.5.0-rc.1 tag/Release. Its historical annotated tag object
`714c849c53afa7f0747f2e377d80d17d790c3d46` still resolves to
`16488735e7a19a6283707a1b6995249432916ca8`. No historical identity/digest is changed.
Task/preflight artifacts are `.agent/vb154-task.md` and `.agent/task_packet.md`.

## Change and compatibility boundary

- `pyproject.toml`, `app/main.py`, `app/mcp_server.py`: only source version `1.5.0-rc.1`.
- `tests/test_api.py`, `tests/test_release_preparation.py`: current assertions, additive RC/stable compatibility vectors,
  new document links and PowerShell syntax verification; all historical vectors retained.
- `tests/test_shared_watcher_ownership_repairs.py`: only the confirmed reconstruction test gains
  a condition-based coverage-restoration fence and stronger recovery/completion assertions.
- `scripts/verify_named_image.py`, `tests/test_named_image.py`: synthetic Docker smoke and focused
  tests for actual metadata, explicit native/container settings, generic Compose forwarding limits,
  file/byte isolation, disabled state, process exclusivity, fail-before-mount identity and cleanup.
- `docs/V150_RELEASE_NOTES.md`, `docs/V150_RC_RUNBOOK.md`: accurate delivered scope, executable
  preparation/named smoke and separately gated publication/semantic/canary/stable procedures.
- `CHANGELOG.md`, `PROJECT_STATE.md`, `ROADMAP.md`, `README.md`, `README_TRUENAS.md`,
  `docs/KNOWLEDGE_SPACES.md`, `docs/RELEASE_CHECKLIST.md`: current preparation status and boundaries.
  Historical v1.4.0 notes/runbook, VB-151/VB-152 reports and VB-153 ledger are unchanged.

No production owner, route, alias/operation ID, auth/policy, filesystem/index/watcher/cleanup
behavior, dependency, accepted ADR, parity fixture, Docker/Compose/catalog or CI/release workflow
changes. The later stable `1.5.0` transition requires separate metadata/documentation review unless
new evidence requires bounded repairs and requalification. Release publication remains unauthorized.

## Native equivalence and environments

Compared the retained VB-152 D Windows `d-finish-sources-before.json` and actual Linux
`d-linux-final-matrix/sources-after.json`, 195 entries each. Manifest file SHA-256:

```text
Windows d80706136c423e7d34f2009c6019a8c8064e53c870f7935ee3212c63a8ad5316
Linux   0de33655da3c9f5987e3853596aded53affd5b74145b5865ce55f731f277c109
```

The initial comparison differed in six existing paths: the two version constants, current release assertions,
`tests/space_integration_evidence.py`, `tests/test_scoped_promotion.py` and
`tests/test_public_space_composition.py`. These manifests predate later accepted test evidence;
they are not described as full current-test equality. Every production Python source is equivalent
to the retained CI-repair worktree after normalizing line endings and reverting only the two version
strings for comparison. The final correction additionally changes only the current application
version assertion in `tests/test_api.py`; the authorized continuation adds the one watcher-test
correction, making eight existing-path differences. Git
dependency/Docker/Compose comparison since accepted D merge `a35fb06`
is empty. Actual source diff against VB-154 baseline confirms the constants are the entire app delta.
Complete current raw-byte hashes and comparison details are retained in
`.agent/vb154-source-manifest.json`, `.agent/vb154-equivalence.json`; inventory runner is
`.agent/vb154_inventory.py`. Despite production equivalence, the full native Linux suite is rerun
to avoid relying on incomplete historical test-manifest coverage.

Windows: build 26300, native Python 3.12.10, existing `../VaultBridge/.venv`, NumPy 2.5.2 and MCP
2.2.0; `pip check` passes. The shell's default Python is the Store stub, so commands prepend the
existing virtualenv Scripts directory to process PATH. No dependencies were installed/changed.
Linux: actual WSL2 Ubuntu, kernel `6.6.87.2-microsoft-standard-WSL2`, native Python 3.12.15 and
`/home/<user>/.venvs/vb152-d-ci-repair`, on native Linux temporary storage. It is not a Windows-
mounted pytest temp directory or a mocked POSIX branch. Full snapshot and final focused-source
boundaries are retained in `.agent/vb154-linux-full-source-manifest.json`,
`.agent/vb154-native-manifest.json` and native/focused logs. Linux's environment has no `pip`
module; the diagnostic `python -m pip check` was unavailable. The existing `uv pip check --python`
verified all 74 installed packages compatible without installing/changing dependencies.

## Initial execution (retained; superseded where stated below)

| Check | Result / retained evidence |
| --- | --- |
| Windows focused release/configuration/runner/TrueNAS-source checks | 143 passed, 0 failed/skipped; `.agent/vb154-windows-focused.log`, `.xml`. |
| Windows actual APP/MCP factory versions | `1.5.0-rc.1`, within focused tests; no derived-state creation. |
| Corrected current API version assertion + focused release/configuration/runner | Windows 59 passed, 0 skipped/failed; `.agent/vb154-version-correction.log`, `.xml`. |
| Actual native module APP startup + MCP stdio initialization | Windows and Linux PASS: named lifespan, APP version/liveness, MCP serverInfo.version, EOF exit 0, no data path and temporary-root absence. `.agent/vb154-{windows,linux}-entrypoints.json`, `.agent/vb154_entrypoints.py`. |
| Ruff / compileall app/tests/scripts / diff whitespace | Pass; focused logs and final workflow. |
| Windows exact semantic/v1 export | All 13 cases and capture/create/append bytes/digests exact; `.agent/vb154-windows-parity.json`. |
| Full actual Linux non-E2E | **FAIL: 4128 passed, 2 failed, 92 skipped**, 4222 collected, 538.41 s; `.agent/linux-full.log`, `.agent/linux-full.xml`. Version assertion corrected afterward; native watcher failure remains unresolved. |
| Final Linux focused metadata/configuration/runner | 57 passed, 2 existing PowerShell-only skips, 0 failures; `.agent/linux-focused.log`, `.xml`. Corrected API assertion included; this does not supersede the failed full suite. |
| Final Linux semantic/v1 export, Ruff and compile | PASS; `.agent/vb154-linux-parity.json` matches Windows and accepted reference byte-for-byte. |
| Local named Docker final matrix | PASS; `.agent/vb154-named-local-final.log`; exact versions, read-only/writable HTTP MCP, REST, stdio, CLI v1/v2/replay, byte/hash assertions, restart, normal stop and cleanup. |
| Local image/source equivalence | 85 packaged app files exactly match current host bytes; `.agent/vb154-local-image-inventory.json`. |
| Initial workflow-selected completion | **INCOMPLETE/ABORTED** after the blocking Linux result: `python scripts/agent_finish.py --task-file .agent/vb154-task.md`; `.agent/vb154-initial-finish.log`. Ruff passed; Windows full suite had loaded the then-stale API assertion and was stopped at about 38%. Finish reported failure; no review packet was generated. Chromium, image build and MCP container checks were not reached by that initial attempt. |

The unchanged accepted parity SHA-256 is
`9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de` (26,158 bytes).
The complete reference includes all precise scores, orderings and legacy capture/create/append
bytes and digests; no rounding, fixture update or new skip is introduced.

The selector requires Ruff, full Windows non-E2E/evaluation, compile, Chromium E2E, Docker build,
MCP dependency/stdio/HTTP smoke. It also reports a manual TrueNAS gate due to README changes.
That **changed-document** gate is satisfied by unchanged deployment/package sources and operational
README blocks, static mount/env/settings review, focused package tests and explicit catalog/production
exclusions. This is not live TrueNAS acceptance. Compose is additionally validated separately;
the selector's automatic selection is preserved and not optimized or bypassed. At the initial stop
those automated checks were incomplete; focused/source-equivalent evidence did not waive them.
The continuation and final completion receipts below supersede that interrupted attempt.

## Disposable local Docker evidence

Built normal Dockerfile locally as `vaultbridge:vb154-local` from the baseline plus the two
version edits. Exact inspected local image ID:
`sha256:5ee105415df312dcff218051b5327dca79c743e3b2899ee37384674bdfd4183b`.
Linux/amd64 runtime manifest `sha256:353c1148f7a2d9e35d1899d712e669ed0f8aa45cfbf12d936bfc841bacc055db`;
config `sha256:41f00e08d46c92ca01b50f43ceb28d8b5666105a300a40592e3d7042154e83c6`;
local attestation manifest `sha256:ca57c5793edd1a86c0ee72b9f7faec413bee1ecb0499fd7646257200ef064473`.
Resolved base index `python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b`.
Docker Desktop Engine 29.8.1; container Python 3.12.16, 60 installed packages. Full installed package,
platform and app-file inventories are in `.agent/vb154-local-image-inventory.json`.

Commands:

```text
docker build -t vaultbridge:vb154-local .
python scripts/verify_named_image.py --local-image vaultbridge:vb154-local --expected-revision 65958c5a8b124fac899454aef64b7822021cb3e5 --expected-version 1.5.0-rc.1
python -m pytest -o addopts= -q tests/test_named_image.py tests/test_release_preparation.py tests/test_truenas_package.py tests/test_truenas_mcp_preparation.py
python -m tests.space_integration_evidence --output .agent/vb154-windows-parity.json
python -m ruff check app tests scripts
python -m compileall -q app tests scripts
```

Local mode's revision argument identifies only the baseline, not reviewed clean source. The exact
app manifest binds the uncommitted two-version delta to the image. Every runner container executes
the inspected local image ID, with synthetic mounts only, UID/GID 568 and no capabilities. Serving,
stdio and CLI writers run sequentially. Default/research independent data paths remain absent
because indexing is disabled; a denied sentinel root/data path proves isolation. Exact inventories
include all synthetic files, not only Markdown. Generated creation timestamps are format-checked;
all other create/append bytes and fixed capture/promotion digests are asserted. Final receipt records
every final file hash. Model/index/watcher/deployment is explicitly **NOT EXERCISED** by this runner.
There is no GHCR pull/push, OCI release identity claim, canary or production receipt.

## Iteration failures and cleanup

Initial daemon probe found Docker Desktop stopped; it was started locally, without changing any
deployment. Early new-runner dry-runs failed because of harness mistakes: legacy initialization
was incorrectly reused for 2026 tools; SDK list results needed `.tools`; denied scopes require
403 and disabled capability 409; MCP denials can be plain text; batch stdio EOF raced replies;
omitted CLI Query retains legacy `matches`; maintenance denial has empty stdout; capture/provenance
frontmatter uses JSON/YAML bytes; promotion needs provisioned Notes and explicit scoped destination,
while legacy absent-destination review must omit that option. Those runner errors were corrected
against existing code/contracts; production code and assertions were not weakened. Early focused
test failures were a Windows test using container POSIX paths and a link to this not-yet-created
report. Final native settings tests use native paths, and actual container config remains POSIX.
Logs `.agent/vb154-named-local*.log`, `.agent/vb154-focused-initial.log` retain the failed iterations.

The full Linux suite found one missed current version assertion at `tests/test_api.py:115`;
it was updated from `1.4.0` to `1.5.0-rc.1` and passed on both native platforms afterward. The
other failure is `tests/test_shared_watcher_ownership_repairs.py::test_reconstruction_wakes_paused_gap_work_once`,
at line 205: after replacement `watcher.start()` and `scheduler.wait(5)`,
`requires_full_sync=True`, `running=False`, `paused=True`, `error_type='RuntimeError'`,
`monitoring_recovering=True`. This test and watcher/scheduler production files are unchanged
from accepted baseline `65958c5`. `SharedSemanticScheduler.wait` explicitly includes paused
failures in quiescence (`app/services/semantic_scheduler.py:689`); watcher start and asynchronous
coverage restoration are distinct. The observed failure is consistent with the test not fencing
restoration before its paused scheduler wait, but no deterministic causal reproduction or bounded
repair has been performed here. It is **not** dismissed as flakiness or erased by a passing retry.
No watcher assertion, timeout, skip, scheduling behavior or cleanup logic was changed.

The required STOP rule was applied. The owned Windows `agent_finish`/selector/pytest process tree
was stopped after recording process IDs in `.agent/vb154-aborted-processes.json`; no unrelated
process was targeted. Finish returned failure (aborted child exit `4294967295`), and an absence
check confirmed no review packet. Interrupted Windows tests are not shutdown/cleanup evidence.
The separate disposable Docker runner had already completed normal shutdown and full cleanup.
The final Linux focus shell wrapper emitted a shell-variable diagnostic after pytest completed;
its JUnit independently establishes 57/0/2 and subsequent parity/Ruff/compile output is retained.
No wrapper exit code is substituted for actual test results.

The user interruption killed one host runner before finally cleanup. Its exact recorded container
was stopped with exit 0 and removed, its observed temporary mount was passed to the existing guarded
cleanup helper, and absence was verified. Later failed dry-runs used finally cleanup and never
printed PASS. Final successful runs stop normally, remove owned containers and require temporary
root absence. Forced failure cleanup is not treated as successful in-process native disposal.

## Confirmed watcher test synchronization race and authorized continuation

The task owner authorized continuation of the same VB-154 task, permitting the smallest test-only
correction if the stated native callback-order hypothesis was proven. No new task/branch/preflight
or production repair was introduced. `.agent/vb154-task.md` records that additional authority.

Source inspection separates three contracts:

1. `SharedFilesystemWatcher.start()` waits for native producer arming/liveness and calls
   `_watcher_reconstructed`; it does not synchronously drain the Linux inotify FIFO fence.
2. Linux initial recursive registration calls `_begin_registration` and queues an `IN_IGNORED`
   fence. `_BoundedInotify._finish_registration_batch` invokes the restored callback only when that
   matching registration revision reaches the event reader. `_watcher_restored` then records
   coverage and, after verified reconstruction, requests full work and wakes the paused owner.
3. `SharedSemanticScheduler.wait()` waits for quiescence including paused failures. It does not
   publish native restoration or retry the deliberately paused owner.

The original full failure did not log coverage counters, so those counters are not retroactively
claimed as measurements of that run. A controlled **actual Linux native** execution of the original
test withheld only the real restored callback until teardown. `start()` returned at restored=2,
revision=5 with paused=true/full_needed=true; scheduler wait immediately returned the same state.
The original assertion failed at the same line 205 with the same retained debt/paused state as
the full-suite traceback. This causal ordering is retained in `.agent/vb154-watch-before-ordered.json`.
No sleeps, successful retries or changed production state were used to explain away the failure.

The corrected test waits under the existing scheduler condition for
`coverage_restored == coverage_revision`, using the existing adjacent-test five-second bound,
then calls the original scheduler wait. It preserves `not requires_full_sync` and additionally
requires unpaused, no monitoring recovery/error, and `coverage_reconciled == coverage_revision`.
The original paused-failure precondition is retained. It cannot pass on readiness alone.

The same controlled ordering now passes: the main thread enters the condition wait, the held
native callback publishes restored=5 and wakes the owner, then the actual full turn completes with
reconciled=5, full_needed=false, paused=false. A second native execution forces restoration before
reconstruction verification; it also passes. Thus both early-restoration and late-callback paths
are checked without changing either production path. These are real native registrations; only
test diagnostic callback ordering is gated.

Negative controls run the corrected test itself in separate processes. Suppressing native
restoration fails the new wait assertion. Suppressing scheduler wakeup or making reconciliation
fail leaves restored=revision=5 but reconciled=0, paused=true/full_needed=true, and fails the
preserved final debt assertion. All three expected failures are required by the diagnostic driver;
they are not pytest skips/xfails. No production file is modified by these temporary patches.

Diagnostic scripts: `.agent/vb154_watcher_probe.py`, `.agent/vb154_watch_matrix.py`. Exact events,
assertion sites and statuses: `.agent/vb154-watch-linux-mutations/{natural,ordered,restored_first,
no_restore,no_wake,no_reconcile}.json` and `summary.json`.

Repetition uses `.agent/vb154_repeat_watch.py`, fresh pytest processes/fixtures and hash seeds
15400 onward. Linux: **20 processes × 5 cases = 100 passed, 0 skipped/failed**. Windows:
**10 processes × 5 cases = 50 passed, 0 skipped/failed**. Each process includes the corrected case,
both stopped-gap/failed-reconstruction cases and both early-full/clean-stop reconstruction counts.
Receipts/logs/XML: `.agent/vb154-watch-{linux,windows}-repeat/`. The full Windows focused run
including the entire ownership module, release/configuration/runner/package checks and current API
metadata passes **156/0/0**, `.agent/vb154-continuation-windows-focused.{log,xml}`. Native Linux
inotify and Windows native observer lifecycles are both exercised; no platform skip was added.

The sole tracked runtime-family change is eleven added lines in the existing test. There are no
new test cases, fixtures, production features, timing sleeps, timeout increases or relaxed assertions.
Accepted semantic/native behavior, dependency and image/source equivalence boundaries remain intact.
The previously completed local named-image matrix is retained unchanged; test-only synchronization
does not alter the packaged app or qualify any published image.

### Continuation verification results

| Check | Executed result and evidence |
| --- | --- |
| Complete native Linux non-E2E | **4130 passed, 0 failed, 92 existing skips**, 4222 collected, 519.48 s. `.agent/vb154-continuation-linux-full.{log,xml}`. The original reconstruction node passes within this complete run. |
| Final selected Windows non-E2E | **3906 passed, 0 failed, 316 existing skips**, 4222 cases. `.agent/vb154-before-cleanup-finish.log`; quiet pytest progress markers are counted and checked against 4222 in `.agent/vb154-continuation-summary.json`. No test/skip was added. |
| Final selected Chromium E2E | PASS, all 36 cases; `.agent/vb154-before-cleanup-finish.log`. |
| Final selected Windows Ruff / compile, Docker build, MCP dependency and stdio EOF | PASS; `.agent/vb154-before-cleanup-finish.log`. Image SDK 2.3.0; actual stdio start/stop with zero exit. |
| Native source identity | All **394 archived files** match the declared snapshot before/after the Linux run. `.agent/vb154-continuation-full-source-manifest.json`, `.agent/vb154-continuation-linux-sources-{before,after}.json`. No source changed during execution. |
| Linux Ruff / compile app/tests/scripts | PASS, `.agent/vb154-continuation-linux-{ruff,compile}.log`; driver `.agent/vb154_linux_verify.py` records actual child exit codes in `.agent/vb154-continuation-linux-results.json`. |
| Current source metadata/CLI contracts | Windows 59/0/0 focused API/release/configuration/runner checks, `.agent/vb154-continuation-release-final.log`. Source identities all `1.5.0-rc.1`; historical release vectors remain. |
| Actual module APP/MCP entry points | PASS on Windows and Linux, including actual named APP lifespan, real MCP initialization, clean EOF and temporary-root removal. `.agent/vb154-continuation-{windows,linux}-entrypoints.json`. |
| 13-case precise semantic and v1 parity | Linux export, Windows export and unchanged fixture exactly equal, **26,158 bytes**, SHA-256 `9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de`. `.agent/vb154-continuation-{linux,windows}-parity.json`. |
| Legacy v1 artifacts independently rehashed | Capture 213 bytes / `e7ee721f1e9d8b4724fe9d70cdcd925f35a4eac2ba6a8397eadd4e43bfe9938c`; create 616 bytes / `97b822f95a5673e42528a6a45badee03b0adb230e9fbd1c132509aaf5b275814`; append 655 bytes / `9c8b86990788350d4f7f37abb8ca50d4ff927f6eef8b28905d80e2a53491f6ea`. |

These are completed source/native checks, not hosted CI or image/deployment qualification. Linux's
92 skips remain the existing Windows-specific, native-PowerShell and case-insensitive-filesystem
limitations. Windows's 316 skips remain its existing platform/symlink-privilege limitations. No test
or skip count was changed by this eleven-line correction. The final Docker/MCP HTTP smoke and
packet-generation outcome are recorded by the completion workflow in `.agent/vb154-before-cleanup-finish.log`;
the packet is produced only after every selected check succeeds. Its real legacy semantic work
does not replace the separately documented future named model/index/watcher qualification.
The full original 156-case focused Windows set and native repetition/mutation evidence above are
additional, overlapping checks; they are not added together as a unique-case total.

`.agent/vb154_continuation_summary.py` independently checks JUnit totals, frozen native sources,
actual entry-point receipts, repeated-test XML and byte-for-byte exports. Its retained output is
`.agent/vb154-continuation-summary.json`. The final selected-check run uses the original command
`python scripts/agent_finish.py --task-file .agent/vb154-task.md`; its live/final receipt is
`.agent/vb154-before-cleanup-finish.log`, replacing only the working filename of the archived initial attempt.

## Remaining gates and final handoff

The inherited watcher failure is resolved by the authorized test synchronization correction above.
Full native Linux verification, repeated native Windows/Linux cases, negative controls, metadata
and exact reference checks pass. The final completion workflow owns all remaining selected checks;
its successful log and fresh packet are required for the implementation handoff. A fresh independent
acceptance session must start only from that packet, without implementer conversation history; no
reviewer is launched here. After an authorized merge, independent CI must pass on the exact
preparation SHA. The required Windows full non-E2E, Chromium, Docker build and MCP image checks
are recorded stage-by-stage in `.agent/vb154-before-cleanup-finish.log`, with the selector summary in the packet.
RC GitHub/GHCR publication, published-digest legacy/named qualification, actual named CPU model/
index/watcher evidence, disposable deployment/restart/restore, stable metadata review/publication
and all TrueNAS catalog/lifecycle gates remain open and separately authorized. Accepted native
opaque cleanup/uncertain-write limits remain documented; no universal shutdown or automatic retry
guarantee is added. Task-1 source preparation does not confer release/deployment qualification.

## Focused acceptance repair — Docker client timeout and owned-container cleanup

The task-owner-supplied independent finding identified one blocker in
`scripts/verify_named_image.py`: mounted CLI and metadata `docker run --rm` calls had no registered
name. A 180-second client timeout could leave a live daemon-owned container while outer finally
cleanup removed its writable mount. The shared permissions-cleanup helper also had an implicit
unnamed mounted container. The previous successful-path smoke did not prove these failure paths.
All other accepted release work is preserved; no production defect or runtime change is inferred.

Before editing the runner, the new targeted regression injected `subprocess.TimeoutExpired` at
the actual CLI subprocess boundary. It failed with `registered == [set()]`, proving the unsafe
ownership gap, in `.agent/vb154-cleanup-before.log`. The same regression passes after repair.
Prior sources/packet/log are retained as `.agent/vb154-named-{runner,tests}-before-cleanup.py`,
`.agent/vb154-before-cleanup-review_packet.md` and `.agent/vb154-before-cleanup-finish.log`.

### Smallest lifecycle repair

One `Containers` owner now allocates a per-qualification UUID prefix and monotonic role sequence,
registering the exact name **before** Docker `run` or `Popen`. HTTP, stdio, each CLI invocation,
metadata and the optional permissions-cleanup worker all use it. Foreground invocations are no
longer anonymous/`--rm`; their finally blocks remove by registered identity even when the Docker
client times out. HTTP and stdio retain their existing strict zero-exit and functional assertions.
No publication/source validation, expected file bytes, semantic flags or tool coverage is weakened.

Every creation includes a run-ownership label. Cleanup first obtains a successful daemon container
inventory, verifies that label via inspect, and thereafter addresses the verified full container ID.
A name collision with an unrelated container fails without stopping/removing it. Running containers
are stopped, then removed; removal success is followed by an independent inventory absence check.
If creation failed before a container existed, or it was already removed, a checked inventory is
required before declaring absence. A failed inspect/stop/remove is never treated as absence just
because the Docker client exited. Unexpected/malformed inventory also fails closed.

Cleanup probes have 15-second subprocess limits; stop/removal have 45-second limits, with a
30-second Docker stop grace. There is one bounded pass over registered identities, with no infinite
wait or blind retry. Failure is latched for this qualification attempt: outer finally can clean
other owned containers but cannot silently retry away an unconfirmed release. It reports residual
name, failed stage and preserved host mount, prints no PASS, and leaves that mount for verified
operator recovery. Prior confirmed absence is idempotent. A hard host interruption remains an
operator recovery boundary; killing the Docker client is not successful container teardown.

The host temporary root is removed only after all application/metadata/CLI/stdio owners are
confirmed absent. If UID-568 files require a root cleanup worker, it too is named, registered,
bounded and confirmed removed before its host mount directory is removed. The strict resolved
system-temp parent/prefix/non-symlink checks remain. `docs/V150_RC_RUNBOOK.md` changes only this
teardown/failure-recovery description. No real containers are deliberately leaked in failure tests.

### Focused regressions and executed verification

Controlled fake Docker/subprocess state outlives a simulated client timeout. Tests cover all five
mounted creation roles registering before dispatch; distinct IDs across sequential invocations;
live-container stop/remove/absence before host deletion; stop failure, removal failure, a container
still present after reported removal and unavailable inventory; pre-creation client failure;
already-removed state; Popen/HTTP launch failure; unrelated-label/name collision; idempotence;
and full orchestrated timeout with both successful cleanup and preserved residual state. The fake
failure fixture removes its synthetic host directory only after assertions; it creates no daemon
containers. Expected injected failures remain assertions about error behavior, not skips/xfails.

| Check | Actual result / evidence |
| --- | --- |
| Windows focused runner/release/configuration/API/MCP-smoke-script tests | 91 passed, 0 failed/skipped; `.agent/vb154-cleanup-windows-focused.{log,xml}`. All 28 named-runner cases execute. |
| Actual native Linux same focused set | 89 passed, 0 failed, 2 existing PowerShell-only skips; `.agent/vb154-cleanup-linux-focused.{log,xml}`. All 28 named-runner cases execute. |
| New final selected Windows full non-E2E | **3924 passed, 0 failed, 316 existing skips**, 4240 cases (18 added cleanup regressions). `.agent/vb154-finish.log`; marker/JUnit/reference checks are summarized in `.agent/vb154-cleanup-summary.json` by `.agent/vb154_cleanup_summary.py`. |
| Windows and Linux Ruff/compile | PASS, canonical `app tests scripts`; `.agent/vb154-cleanup-linux-{ruff,compile}.log` and final selected workflow. |
| Real local named-image successful path | PASS with the repaired helper against the previously recorded exact local image `sha256:5ee105415df312dcff218051b5327dca79c743e3b2899ee37384674bdfd4183b`; `.agent/vb154-cleanup-named-local-final.log`. REST/MCP HTTP/stdio/CLI, exact bytes/provenance/replay, restart, shutdown and guarded root removal all retained. Checked run-label container inventory confirms no runner containers remain in `.agent/vb154-cleanup-container-absence.log`. |
| Exact 13-case and v1 preservation | Windows export, Linux export and unchanged reference are byte-identical, 26,158 bytes / `9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de`; `.agent/vb154-cleanup-{windows,linux}-parity.json`. Legacy v1 bytes/digests above remain exact. |

The accepted native Linux full run (4130/0/92) is reused within its stated source boundary, not
represented as a new 4240-case Linux run. A raw comparison of its 394-entry retained after-manifest
finds only four changed paths: this report, the runbook teardown paragraph, the named runner and
its test module. Every app, dependency, native owner, watcher test repair, other test and reference
byte is unchanged. The affected runner/tests were rerun on actual Linux, with native source hashes
unchanged before/after those checks. No affected production native suite is omitted on an assumption
of equivalence. Details: `.agent/vb154-cleanup-equivalence.json`, native driver
`.agent/vb154_cleanup_native.py`, `.agent/vb154-cleanup-linux-results.json`.

This is still only local disposable mechanics, with semantic indexing/model/watcher disabled in
the named runner. It is not a published immutable-image or deployment qualification. Versions remain
`1.5.0-rc.1`; no application/dependency/workflow changes, release tags, publication, aliases,
deployment, user vaults, commits, pushes, PRs, subagents or reviewer launch occur in this repair.
The original completion command is rerun: `python scripts/agent_finish.py --task-file .agent/vb154-task.md`.
Its new `.agent/vb154-finish.log` and freshly generated `.agent/review_packet.md` are the authoritative
completion receipts; the previous packet is superseded. Separate focused independent acceptance
must use only that updated packet, without implementer history. Operator cleanup after an
unconfirmed daemon outcome remains deliberately fail-closed with preserved disposable state.
