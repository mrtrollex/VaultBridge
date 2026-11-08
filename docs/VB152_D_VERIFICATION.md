# VB-152 Slice D production composition verification

Implementation scope: coordinated public composition only, based on clean HEAD/merge base
`6d6c257a33d4710a0a6a9c2f92975fac8c50d08b` on `feat/vb-152-d-public-activation`.
The task owner confirms independently accepted/merged A (#147), B (#148) and C (#149).
The accepted BACKLOG contract and ADRs 0009/0010 remain unchanged.

Production composition is implemented and the final Windows/native Linux suites pass.
The final completion workflow records its selected-check result in `.agent/d-finish.log` and creates
`.agent/review_packet.md` only on success. Separate fresh acceptance remains required.
No milestone completion, hosted CI, release, deployment, package update or client migration is claimed.

## Production entry-point activation checklist

All rows are implemented or deliberately fenced. Executable native gate results follow separately;
a checked disposition is not a claim that independent acceptance or publication occurred.

| Entry point | Disposition and ownership | Executable evidence |
| --- | --- | --- |
| FastAPI `create_app`, module-level `app` | Strict named registry before allocation, one `SpaceRuntime`, exact operations/resolver/admission. All seven raw-owner injection options rejected before registry work. Legacy branch retains constructors/defaults/injection. | `test_factory_exact_single_generation_and_no_legacy_components`, `test_conflicting_raw_injection_before_registry`, module subprocess and supplemental actual module lifespan checks; legacy API/lifecycle suites |
| Application lifespan | E5 native readiness -> E4 startup reconciliation scheduling -> admission -> serve. Semantic readiness stays asynchronous. Same-generation rollback/shutdown; no restart/replacement. | Production startup faults, active-operation drain, native background tests and supplemental post-native/post-E4/post-MCP faults |
| REST dependency providers | Exact `state.space_operations`; raw vault/semantic/indexer/relationships/duplicates getters stay legacy-only. Hygiene has no named raw service fallback. | Production injection/no-work tests and accepted adapter/security suites |
| REST aliases/versioned reads and writes | Existing functions, paths, IDs, authentication, direct-peer limiter and cancellation. Default omission retains compatibility; explicit selection uses accepted scoped operations. | Both alias families, real create/append/read/search, Query, policy denial, native containment and A/B/C compatibility tests |
| Knowledge Query | Existing strict parser/domain operation on REST, MCP and structured CLI; no new engine/ranking. | Four-way production REST/HTTP MCP/stdio/CLI identity/result parity, A/B/C predicate/security suites |
| MCP factory/stdio main | Named stdio owns one runtime through SDK lifespan, local trusted-process access/limiter, no Bearer requirement. Legacy persisted-read/write-opt-in branch remains separate. Public named HTTP factory requires borrowed operations. | Actual `python -m app.mcp_server` frames; enabled named stdio supplemental subprocess proves one runtime/model, two reconciled spaces and stopped native watcher |
| MCP HTTP `/mcp` and SDK sessions | Opt-in stateless transport borrows parent's exact operations/limiter, no child runtime. Host/Origin/current/previous Bearer, protocol and registration gates retained. Disabled transport constructs no SDK server. | Both HTTP protocol paths, exact identity assertions, shared REST/MCP limiter, previous key, auth/Host/Origin errors, resource reads and disconnect drain |
| MCP tools/resources | Accepted schemas/annotations/tools/templates only; uniform resource denial, qualified canonical paths, no registry enumeration. | Production tools/resources/nested path/strict UTF-8/unknown-space calls, schema regressions and complete A/B/C input security suites |
| CLI main/exported `run_*` reads | Public dispatch activates complete-registry persisted/inspection profiles; direct raw injection fails safely. Omitted compatibility uses default-only old limits/output; explicit uses scoped output. | Real CLI subprocess reads, exported injection tests, semantic stored-byte/mtime checks, missing-index no-model tests and CLI regressions |
| CLI capture/review/decide/apply | Public default v1 and explicit v2 use accepted profiles; review inspection-only, writes full runtime with qualified scheduler wait before shutdown. | Exported round trips, actual v1/v2 CLI subprocess round trips, accepted C approval/replay/uncertain outcome suites and supplemental Python interrupts |
| CLI profiles and maintenance | Read profiles have no sync/storage creation/download/cache repair/watch. `status`, `index`, `index check`, `index rebuild`, `reindex` remain fenced before settings root resolution or owner work. | Persisted snapshot tests; five environment-entry maintenance cases, direct-export guards and accepted A persisted/security matrix |
| Health/readiness/privacy | Minimal named health/admission, dependency-free live, authorized default-only ready. Disabled/denied default has no probes/fallback. Static public privacy. | Production health/cookie tests, background ready probe, accepted denial/no-owner tests and legacy health regressions |
| Dashboard/sessions/assets | Static 404/no-store before session/body/owner/asset work, all `/ui` subpaths/methods and proxy root paths. Cookies cannot authenticate named REST/MCP. | 60 production fence combinations plus auth tests; legacy UI/session suites and Chromium regressions |
| Startup reconciliation | Existing full runtime uses read+index-authorized bindings only; disabled owners inert with stale derived state. Unselected enabled CLI write owners retain reconciliation. | Production background/disabled-stale tests, enabled stdio and accepted C unselected-space reconciliation |
| Shared scheduler/watcher | Exact runtime-issued bindings, one E4 executor/model and optional native E5 observer/dispatcher; no legacy background components. Existing limits/debounce/recovery/cancellation unchanged. | Actual native external-edit isolation, two-space repositories, single model, no-legacy-construction spies and full VB-151 E1â€“E6/G suites |
| Postcommit indexing | Exact authorized identity/owner; durable outcome survives enqueue failure; disabled indexing has no enqueue. | Production qualified commit and failed callback tests; accepted C dedupe/enqueue, capture/promotion and G reference tests |
| Cancellation/shutdown | Existing one active/64 waiters and token/budgets. Fence operations/indexing before HTTP MCP ingress closes; E5/E4/operation/E2 cleanup stays runtime-owned. Confirmed/uncertain writes survive late cancellation. | Direct production ASGI shutdown during read/write, uncertain capture on actual app runtime, mounted HTTP disconnect, saturation/waiter cancellation and native cleanup suites |

Legacy-only guards remain in `legacy_operations`, `validate_legacy_operations`, raw dependency
getters and the five unsupported maintenance exports. Legacy factory/startup, stdio persisted
behavior and Dashboard/session paths retain their original branch. No guard is blanket-removed.
Named public stdio cannot borrow operations, and public HTTP MCP cannot create a standalone runtime.

## Design, compatibility and security

FastAPI stores its runtime on application state and exposes only its operations to named adapters.
MCP HTTP's session lifecycle has no owner shutdown path. Named stdio attaches runtime ownership
to the existing SDK lifespan. `SpaceRuntime.begin_shutdown()` exposes the existing fence sequence
so transports can close ingress before calling runtime cleanup; it adds no new native lifecycle.
Startup cancellation drains the same blocking runtime call before rollback. A failed/incomplete
generation remains pinned and cannot be restarted.

Omitted CLI semantic operations needed a compatibility reader in the existing persisted profile.
It screens the same storage ancestry, validates the default namespace (including recognized legacy
unbound default indexes) inside an immutable read transaction, checks root/file identity again
after payload work, and retains reader cleanup in the profile. It does not initialize storage,
write metadata, narrow the registry or use federation's tighter scoped limits for legacy requests.
Supplementary policy preflight is shared with compatibility execution, before owner construction.
Omitted v1 promotion review with candidate advice uses the same persisted read-only profile and
retains exact legacy semantic evidence when available. Explicit scoped review stays nonsemantic;
semantic denial/disabled indexing never invokes the backend, and a missing index is not initialized.
The before-fix comparison over the same persisted default returned three semantic candidates in
legacy mode versus unavailable/zero in named mode; the final production regression requires exact
review JSON equality and unchanged stored bytes/mtimes. Four profile regressions include optional
semantic denial, explicit default v2 and missing-index no-model behavior.
Named CLI parser failures use static categories before settings/root resolution, including unknown
scope spellings and malformed numeric/group arguments. Error formatting is selected separately
from scoped validation, so omitted requests retain their legacy defaults and numeric bounds;
search limit 51 still fails before work. Sixteen environment/injected-settings and human/JSON
regressions prove no supplied private values are echoed. Legacy parser behavior is unchanged.

All named dispatch remains policy-first. Authentication identifies the deployment, not a per-space
grant. Unknown/denied/mixed selections cannot reach operational owners. No client root choice,
generic dataclass serialization, labels, registry enumeration or configuration diagnostics is added.
Scoped REST read responses now consistently include no-store on success and safe failure, including
authentication/limiting errors. Existing current/previous Bearer, direct-peer rate limits, route IDs,
aliases, tool/resource schemas and disabled API-doc endpoints remain. Native root replacement and
cross-space Windows junction/POSIX symlink tests fail closed.

The changed existing tests update only the historical A/C activation-guard expectation for D:
raw-injection/maintenance denials stay asserted; public named constructors and supported exported
functions now reach their authorized profiles. Legacy schema/ID and all owner security assertions
remain. There are no added skips, longer timeouts, weakened assertions or changes to accepted ADRs,
reference fixture, external Action schema, schema/migrations, ranking, model settings or packaging.

## Startup, shutdown and failure matrix

| Failure/order | Observed contract and evidence |
| --- | --- |
| Invalid root/overlap/default binding and raw injection | Production factory rejects before owner/worker allocation; raw injection also precedes registry probes. Settings/registry legacy behavior remains unchanged. |
| Native readiness failure | Production lifespan rolls back its exact runtime. Supplemental native fault injects after the real observer is alive and native arming succeeds, then proves observer/dispatcher stopped, E2 closed and admission fenced. |
| E4 startup reconciliation submission failure | Supplemental fault starts the actual E4 executor/submission then raises before admission; runtime rolls back the same generation. Owner-local asynchronous indexing failures retain existing E4 isolation rather than becoming a new readiness requirement. |
| MCP SDK constructor or started ingress failure | Same parent runtime is cleaned; no fallback or second registry/runtime. Supplemental ingress case starts actual SDK sessions before failing, including SDK ExceptionGroup propagation. |
| All indexing disabled with stale rows | No E2 backend/scheduler/watcher or indexing of stale SQLite. REST/real stdio semantic attempts deny by capability before old-row work. |
| Qualified callback throws | Successful REST durable commit/result and exact selected Markdown survive; no cross-space enqueue or write retry. |
| 64 waiters and excess request | Excess public read returns safe 503; cancellation releases each queued reservation and later public read succeeds. Existing 50 ms polling/budgets unchanged. |
| Mounted MCP HTTP disconnect | Exact parent operation token is cancelled; request drains real blocked read, returns no successful note content, and active admission releases. Both accepted transport cancellation matrices rerun in full. |
| Shutdown with active read | Production ASGI lifespan fences admission and remains non-stopped until real owner returns; read fails operation_cancelled, no successful partial result. |
| Shutdown with confirmed write | Production REST result retains successful commit despite late runtime cancellation; shutdown waits for the same admitted owner. |
| Shutdown with uncertain write | Actual capture on the app's runtime physically writes then returns commit_unknown; file exists even though committed=false. No automatic repeat; same generation drains. |
| Cleanup failure | Public lifespan keeps its same runtime stopping/fenced and rejects restart. ADR 0010's actual opaque release/reuse/certified-safe retry matrix is rerun in the full native suites; no numeric close retry or false stopped is added. |
| CLI Python interrupt | Supplemental main-thread injection signals the exact caller token, joins the same read/write, suppresses cancelled read success, and retains confirmed/uncertain evidence. These are Python interrupt tests, not external Windows console-control or terminal-signal certification. |

The production shutdown test uses direct ASGI exchange and lifespan tasks. Its first native Linux
attempt exposed a TestClient portal teardown race: the client portal closed before the calling
thread consumed a completed response. The test now observes the app's ASGI messages directly;
its owner-drain, fencing and outcome assertions are preserved. That new test-harness defect is
not attributed to inherited instability. Preliminary full runners were stopped before restarting
with the corrected frozen test source; incomplete preliminary runs are not acceptance evidence.

Earlier focused attempts also found fixture mistakes (duplicate-cased HTTP auth headers, an absent
legacy append destination in review and an uncaught expected disconnect cancellation), the missing
omitted semantic CLI immutable path, missing scoped read no-store, and stdio interrupt cleanup
status. Named stdio now reports clean exit only after its exact runtime is verified stopped;
two final regressions retain stopping ownership and operational failure after interrupted cleanup. Retained logs distinguish
those exploratory failures from final verification. No production failure is called flaky.
The last implementation check reproduced named argparse echoing a private ID from an unsupported
scope flag before settings/policy. Its error formatter is now static without enabling scoped
limits/defaults on omitted requests. Sixteen new regressions cover environment/injected settings,
human/JSON output and retained legacy numeric bounds. Pre-correction native/full/workflow evidence
is historical; final native suites and completion run again on the corrected frozen source.
The public profile inventory also exposed omitted v1 promotion review losing optional semantic
advice through its inspection-only profile. That confirmed gap is repaired in profile composition,
without changing scoped v2 advice, policy, ranking or storage. The final matrix reruns after this
profile correction; pre-correction Linux 4,106/0/92 evidence remains historical.

## Verification and reproducibility

Native Windows uses Python 3.12.10. Native WSL/Linux uses Python 3.12.15 and a source copy under
`/home/<user>/vb152-d-native-20261108`; filesystem/SQLite/native watcher tests use native Linux
temporary storage. The final runners record before/after SHA-256 manifests for every Python source
under app/tests/scripts and the unchanged accepted reference fixture. Native paths are not mocked.
Executed on 2026-11-08: Windows 11 build 26300 and Linux kernel
`6.6.87.2-microsoft-standard-WSL2`. The Windows development environment has MCP SDK 2.2.0;
native Linux and the rebuilt container have 2.3.0. Both satisfy the unchanged `mcp>=2,<3`
requirement. These are distinct verified environments, not a claim of identical dependency installs.
Production/backend work uses synthetic vaults and the existing deterministic local test backend;
there is no real vault content, model download or external AI service.

Counts are passed / failed / skipped. Subsets overlap the full suite.

| Check | Windows | Native WSL/Linux |
| --- | --- | --- |
| Final production composition | 146 / 0 / 0 | 146 / 0 / 0 |
| Full non-E2E | 3886 / 0 / 316 | 4110 / 0 / 92 |
| A/B/C adapters and input security (within full) | 706 / 0 / 0 | 706 / 0 / 0 |
| SpaceRuntime native lifecycle/resources (within full) | 284 / 0 / 22 | 278 / 0 / 28 |
| Shared scheduler/watcher modules (within full) | 206 / 0 / 188 | 342 / 0 / 52 |
| Semantic evaluation | 29 / 0 / 0 | 29 / 0 / 0 |
| Exact 13-case semantic/v1 reference export | Exact accepted reference | Exact accepted reference |
| Ruff / compileall app/tests/scripts | Pass | Pass |
| Chromium legacy Dashboard | 36 / 0 / 0 | Not separately run |

All 194 Python sources plus the reference fixture match byte-for-byte between native trees and remain frozen. The reference SHA-256 is `9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de`.

The existing platform/privilege skip totals remain 316 on Windows and 92 on Linux; all 146 D tests execute on both. No inherited instability was reproduced in these successful full runs. The final Windows full count is from the required completion workflow; the native Linux full count is from `d-linux-final-matrix/`. The earlier pre-stdio-status-fix full runs (Windows 3,864/0/316, Linux 4,088/0/92) and pre-parser-privacy-fix Linux 4,090/0/92 are historical, not final-source acceptance evidence.

The standalone rebuilt Docker/Compose/MCP checks passed 5/0/0; the final workflow rebuilds and reruns the selected MCP container checks on the final source. Compose configuration validation also passed. Chromium and all selected-check results are retained in the final workflow log. The selector reports no additional manual gate; deployment/package files and historical runbooks are unchanged. No required local check is treated as successful if unavailable.

Separate supplemental process checks pass 7/0/0 on each platform: actual legacy/named module-level
startup/drain, actual CLI v1/v2 subprocess round trips, and three Python interrupt scenarios.
Four actual startup-fault scenarios pass 4/0/0 on each platform. Enabled named stdio subprocess
receipts prove exactly one runtime/model, startup reconciliation of two spaces, semantic Query,
authorized create, terminal stopped and native watcher/dispatcher disposal on each platform.
Actual direct production stdio without backend injection is also exercised in the 146-case suite
with disabled indexing, strict UTF-8, resources, Query, denied selection and write registration.

Commands:

```text
python -m pytest -o addopts= -q tests/test_public_space_composition.py
python -m pytest -o addopts= -q --ignore=tests/e2e -r s
python -m pytest -o addopts= -q tests/eval
python -m tests.space_integration_evidence --output platform-reference.json
python -m ruff check app tests scripts
python -m compileall -q app tests scripts
python -m pytest -o addopts= -q tests/e2e --browser=chromium
git diff --check
python scripts/agent_finish.py --task-file .agent/vb152-d-task.md
```

Artifacts under ignored `.agent/`:

- Task/preflight: `vb152-d-task.md`, `task_packet.md`; final completion target `review_packet.md`.
- Frozen runners/copy: `d_verify.py`, `d_native_copy.py`, `d-native-source.tar`;
  historical `d-windows-final/` and `d-linux-final/`; final native `d-linux-final-matrix/`, final Windows `d-finish-full.xml`/`d-finish.log`, `d-complete-results.json` and source manifests retain the exact results. Windows final reference is `d-windows-complete-reference.json`.
- Supplemental process/interrupt runner: `d_public_smoke.py`, `d-public-windows/`, `d-public-linux/`.
- Actual native startup fault runner: `d_native_faults.py`, `d-faults-windows/`, `d-faults-linux/`.
- Enabled production stdio: `d_stdio_owned.py`, `d-stdio-owned-windows/`, `d-stdio-owned-linux/`.
  The final cleanup-status correction is also exercised by `d-stdio-owned-windows-complete/`
  and `d-stdio-owned-linux-final-matrix/` receipts.
- Container checks: `d_extra_checks.py`, `d-extras/`; Chromium: `d-chromium.log`/`.xml`.
- Early focused/source-diagnostic logs retain failures; only final frozen-source results are acceptance evidence.

The Windows environment is `../VaultBridge/.venv/Scripts/python.exe`; the native Linux runner uses
the existing `vaultbridge-g-ci-py312` virtual environment. Backend instrumentation substitutes only
the model in supplemental module/stdin smoke; public factories, SDK lifespans, real native owners,
registry/policy/admission, repositories and shutdown are executed. SDK ExceptionGroups are checked
for the exact injected synthetic failure, not suppressed as arbitrary success.

## Retained limitations and activation status

No new cross-process coordination, tenant/RBAC model, registry discovery, named Dashboard,
maintenance tooling, root rebind, old-artifact migration, cross-space promotion, REST/MCP
capture/promotion, deletion/move/repair or deployment changes are introduced. Named stdio runs
instead of the serving application against the same writable state. CLI commands follow the same
stopped-service discipline. Cooperating-filesystem limits, no atomic multi-root snapshot, no
hardlink/hostile-writer isolation and no hard latency bound remain. Index readiness is asynchronous.
ADR 0010 permits ambiguous native ownership to remain permanently stopping with bounded retention;
process termination is not a successful in-process cleanup claim. Confirmed/uncertain commits
must never be retried automatically because of cancellation or derived-index failure.

The [operator guide](KNOWLEDGE_SPACES.md) and README describe actual source composition and its
exclusions. Historical release/catalog runbooks and all deployment/package sources are unchanged;
this work supplies no hosted CI, production deployment, catalog or release evidence.

Public named composition is enabled in source and its required native matrix passes.
The final `agent_finish.py` invocation owns the remaining selected-check sequence, records actual
results in `.agent/d-finish.log`, and produces the review packet only after success; implementation
stops at that handoff. No native matrix gate is unresolved. Separate fresh acceptance remains
mandatory, so milestone/release/deployment completion is not declared.

## Exact changed-file inventory

```text
ARCHITECTURE.md
BACKLOG.md
PROJECT_STATE.md
README.md
ROADMAP.md
app/api/space_reads.py
app/cli.py
app/core/config.py
app/main.py
app/mcp_http.py
app/mcp_server.py
app/services/_space_compatibility.py
app/services/persisted_space_reads.py
app/services/space_runtime.py
app/services/write_composition.py
docs/KNOWLEDGE_SPACES.md
docs/VB152_D_VERIFICATION.md
tests/test_authorized_write_adapters.py
tests/test_knowledge_spaces.py
tests/test_policy_read_adapters.py
tests/test_public_space_composition.py
```

Ignored task/preflight/review packets, runners, logs/JUnit, receipts, manifests and exports are
completion artifacts, not production source. No accepted ADR or frozen reference fixture changed.
