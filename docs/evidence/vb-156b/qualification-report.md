# VB-156B qualification evidence

This is local synthetic qualification on 2026-11-21, not independent review, CI,
release, deployment, or an audit of real listeners. **Overall status remains
INCOMPLETE: Windows Playwright WebKit fails native Strict-cookie delivery, one
Chromium bootstrap failure has no established causal explanation, and authenticated
BFCache admission/suspended-page qualification was not exercised.**
ADR 0011 is unchanged. VB-156C/D/E are outside this work.

## Failure classification and verified corrections

| Finding | Classification | Resolution / evidence |
| --- | --- | --- |
| Windows WebKit 26.6 sends a Strict cookie on genuinely cross-site navigation and fetch | External browser implementation defect; failed security qualification | Matches independently confirmed [Playwright #44141](https://github.com/microsoft/playwright/issues/44141) and the upstream [WebKit correction #81431](https://github.com/WebKit/WebKit/pull/81431). Production and independent issuer both fail. Raw Strict/Secure/HttpOnly/host-only headers and receiving-server Cookie observations remain retained and asserted. Chromium/Firefox on Windows and all three Linux engines pass the unchanged test. No application attribute or origin guard was relaxed. |
| Same-host HTTPS other-port receiver obtains the cookie | Expected negative deployment demonstration | Cookies are hostname-scoped, not port-scoped. A different hostname in the same site receives no host-only cookie. Genuinely cross-site requests separately test Strict. |
| Firefox receiver falsely attributes a late favicon to the subsequent different-host navigation | Incorrect fixture observation | Capture HTTP Host and path; assert the matching /probe request without clearing another request's provenance. Reproduced the exact failing node before correcting it. |
| WebKit receiver shutdown waits on speculative TLS connections | Fixture teardown | Close the native browser context/connection pool before stopping the disposable receiving server. Cookie assertions run first. |
| TLS termination fixture startup times out during repeated HTTP readiness probes | Fixture setup | Synchronize on Uvicorn's actual startup completion event rather than repeated short network deadlines. Production application startup is unchanged. |
| A single TCP disconnect is retried transparently by Chromium | Incorrect failure setup | Keep the disposable proxy unavailable until the UI observes transport failure; recover it explicitly. Partial response tests also deliver actual Set-Cookie headers before truncation. |
| First continuation canonical Chromium run: 147 passed, one failed before orphan DELETE setup | Unresolved bootstrap failure; classification not established | The form remains at Checking browser support, with no spaces.js HTTP request observed. These custom contexts bypassed pytest-playwright's trace recorder; use its new_context factory so future failures retain native traces. Thirty exact-node reruns, 300 contexts on one server, 200 fresh server/proxy startups and a complete 148-case Chromium rerun passed. None establishes a causal fix or permits calling the failure flaky. No timeout, assertion, cache/security header or proxy capacity was changed to hide it. |
| Unlock submitted during another tab's live DELETE queues behind it and logs in after deletion | Application defect | Fence login at submission against deleting/pending/mismatched lifecycle state, while retaining under-lock revalidation. Two competing deliberate logins remain supported. Native regression failed before the fix. |
| Missed notifications plus a late restoration leaves the page checking indefinitely | Application state defect | Recheck shared lifecycle and display locked/pending/uncertain state when the snapshot became stale. No cookie mutation is added to GET. |
| Native storage quota failure before POST is shown as an abandoned HTTP mutation | Application state defect | Distinguish pre-dispatch storage failure (unsupported) from an issued mutation (uncertain). Partial durable evidence remains fenced. |
| Linux WebKit launch misses graphics/media libraries | Missing infrastructure | Installed native Playwright system dependencies after repairing interrupted X11 package configuration. Native WebKit 27.2 launches and executes the tests. No project dependency changed. |
| Docker unavailable / credential helper absent from process PATH | Missing infrastructure | Daemon and Compose available; use the installed Docker helper directory in process PATH. Production Docker build and all existing MCP container smokes execute. |

## Remaining-findings analysis (2026-11-21)

This repair pass changes qualification fixtures and this evidence document only.
No new application implementation defect was confirmed or corrected. Earlier
application corrections in the table above belong to the preceding implementation
pass; the independent review did not establish another application defect.
Production cookies, origin/Fetch Metadata guards, privacy headers and accepted
ADR 0011 remain unchanged. Evidence status is separate from implementation status:

| Classification | Implementation status | Qualification status / evidence |
| --- | --- | --- |
| Confirmed implementation defect | No new defect confirmed in these three remaining findings | The original Chromium failure has insufficient causal evidence; it is not an application fix claim. |
| Corrected implementation defect | Earlier login-during-DELETE, stale restoration and pre-dispatch quota handling corrections are retained | Historical reproductions and regressions remain in the preceding table. This pass adds no production correction. |
| Passing native qualification | Existing restoration/security behavior retained | Native Back/Forward, validation-held privacy blanking, logout while the other page is away, stale selection/status erasure, bootstrap dependency and lock barriers pass in the exercised Windows/Linux profiles. |
| External browser defect | Application security contract unchanged | Windows Playwright WebKit revision 2359, version 26.6, fails Strict delivery for production and independent issuers. Four original failures remain failures. |
| Inconclusive evidence | Chromium bootstrap remains unresolved | Original 147-pass/1-fail canonical schedule is retained. Later successful schedules and new barrier tests do not establish a cause or a fix. |
| Platform scenario not exercised | No authenticated BFCache support claim | Actual authenticated persisted admission and logout of an authenticated BFCache-suspended page were not observed. Firefox/WebKit automation disables admission; real Safari/macOS/iOS and uninstrumented browser profiles were not tested. |

### Chromium bootstrap: causal boundary, not a claimed fix

The failing node was
`test_orphan_mutation_quarantined[root-https-chromium-close-DELETE]`.
Its `login(a)` failed filling the hidden input before `barrier.arm("DELETE")`,
DELETE dispatch, tab closure, or the second page existed. The captured server log
shows shell/CSS/logo, `named-app.js`, `named-session.js` and `scoped-client.js`
successes, but no `spaces.js` request. It does not contain browser-side dispatch,
completion, exception, or native trace evidence for that original context.
An HTTP server completion alone does not prove browser module evaluation.

Static imports of NamedSession, ScopedClient and Spaces must complete before
`named-app.js` constructs the session, installs form handlers and calls restore.
The literal **Checking browser support** is initial HTML; the first supported
restore instead renders **Checking browser session**. The new deterministic
`test_bootstrap_module_dependency_barrier` holds only the real `spaces.js` request,
observes the initial hidden form, empty native held/pending locks, absent journal,
and zero session dispatch, then releases it and proves readiness/login. This
reproduces the *symptom boundary*, not the original unexplained missing request.
`test_bootstrap_native_lock_barrier` holds the actual origin Web Lock in another
tab: the new page reaches **Checking browser session**, reports that held lock and
its pending request, and sends no session request until release. A successful
release then permits normal login; no timer or retry grants readiness.

Session restoration timing, journal initialization and native lock contention are
therefore later than the observed initial-label boundary when module construction
has not started. Orphan DELETE synchronization cannot explain a failure occurring
before that fixture barrier was armed. Each server uses a fresh listener/proxy,
explicit Uvicorn startup completion, per-test context and context-close before
server shutdown. No startup/teardown race was causally established for the original
failure. No proxy capacity, production initialization order, timeout or assertion
was changed based on speculation.

`NativeEvidence` now records native module request/response/finished/failed events,
cache policies, document/page events, page errors and page close in sequence for
the named contexts, including canonical orphan tests. Explicit diagnostic snapshots
also record native locks, journal phase and readiness without credential/cookie
values, journal nonces, or protected response bodies. The pytest-playwright context
factory retains traces on failure. Original evidence and the earlier 30 exact-node,
300-context, 200-fresh-start and full-suite successes are preserved. **Root cause
remains unresolved; repetition is not proof of repair.**

### Authenticated BFCache versus safe native history restoration

`test_native_authenticated_history_qualification` uses native link navigation,
Back, Forward and Back again at root/prefix. The page first selects `research`.
A test observer registered after application initialization records the native
pagehide persisted flag and post-handler private-DOM/selection/status/key erasure.
Only these booleans cross navigation in a test-only sessionStorage key, immediately
removed on receipt; no private state is persisted. Console-only departure evidence
was rejected because automation can drop messages during document destruction.

For a clean return, a real server barrier holds all validation GET headers; private
UI, old selection and old status stay empty, the cookie/expiry and clean journal
stay unchanged, and responses contain no Set-Cookie. Releasing validation allows
fresh discovery/default selection, never replay of `research`. For the logout-away
case, another tab completes DELETE while the first page is away; Back/Forward
restoration stays locked/empty and dispatches no session GET to adopt the removed
cookie. These are passing **non-BFCache native history** paths, not proof that an
authenticated document was suspended in BFCache. Native pageshow persisted=false
and navigation type back_forward are recorded in JUnit and event JSON.

The prior attribution of all engines' refusal to no-store JS requests was too
broad. Chromium reports `response-cache-control-no-store` and
`response-cache-control-no-store-with-js-network-request` (plus a masked reason).
Firefox/WebKit expose no corresponding reason API here. More decisively, the
version-matched Playwright sources disable their BFCache independently of the app:

- Firefox [playwright.cfg](https://github.com/microsoft/playwright/blob/v1.63.0/browser_patches/firefox/preferences/playwright.cfg#L45)
  sets `fission.bfcacheInParent=false`; [content/main.js](https://github.com/microsoft/playwright/blob/v1.63.0/browser_patches/firefox/juggler/content/main.js#L55)
  sets `docShell.disallowBFCache=true`. The 1.64.0 source has the same restrictions.
- WebKit's [automation patch](https://github.com/microsoft/playwright/blob/v1.63.0/browser_patches/webkit/patches/bootstrap.diff)
  sets `preferences.setUsesBackForwardCache(false)` when the inspector connects;
  the 1.64.0 patch does the same. Separately, upstream
  [WebCore's HTTPS no-store policy](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/history/BackForwardCache.cpp#L144)
  rejects such documents. This source policy is corroboration, not an exposed
  per-navigation native diagnostic from the tested binary.

The independent public controls use a separate server, a nonempty document and
either no-store or max-age=60; production headers are never overridden. Full
Chromium admits the cacheable control (pageshow [false,true]); Firefox and WebKit
refuse both controls, consistent with their harness restrictions. A preliminary
headless-shell/empty-body control was insufficient to distinguish those causes;
its separate results remain retained. The existing full-Chromium public no-store
feature test still requires actual persisted=true and key erasure. It does not
establish authenticated admission on any engine.

A separate native Chromium protocol suspension probe also failed to observe any
freeze/resume event, despite successful protocol commands. Its source, strict
failed assertions, logs and event records remain under `remaining-qualification/`; it is an
unsuccessful diagnostic, not a passing regression or native suspension claim.
Its initial expectation of an immediately available unlock form was also stronger
than ADR 0011's permitted conservative logout_pending state. No application fix or
security-assertion relaxation followed. **Authenticated persisted admission and
logout while actually BFCache-suspended remain unexercised.** Safe navigation-away
restoration does not fill those evidence gaps; no header is weakened to force it.

### Windows WebKit upstream status and exact affected build

Checked live on 2026-11-21: Playwright issue 44141 is closed (2026-11-20), with the
maintainer directing tracking to the upstream fix; that closure is not a shipped
browser correction. WebKit PR 81431 is **open**, merged_at=null. Its proposed CURL
cookie-jar correction preserves explicit SameSite attributes. The local Windows
SDK is Playwright **1.63.0**, WebKit **2359 / 26.6**, matching an affected revision
in the independently confirmed issue. A corrected installed build was not supplied.

Retained application/control traces and JUnit contain the raw Strict issuance,
stored SameSite=None, cross-site Fetch Metadata and forbidden actual receiving
Cookie headers. All four Strict failures remain asserted; no xfail/skip or changed
cookie/guard was introduced. The receiving server's observations, rather than just
cookie inspection metadata, establish the failure. No unchanged failing Windows
cookie case was rerun in this pass. Guard rejection tests are retained and rerun
separately; they cannot prevent direct cookie delivery to another HTTP receiver.

Linux WebKit is a different native port/build (Playwright 1.64.0,
**2370 / 27.2**); its passing cookie qualification does not certify Windows WebKit.
Windows Chromium **1243 / 153.0.8010.12**, Firefox **1543 / 155.0**, and Linux
Chromium **1248 / 156.0.8078.4**, Firefox **1555 / 157.0** are also distinct
qualification profiles. Playwright WebKit on either OS is not real Safari on
macOS/iOS. No real Safari qualification was exercised.

## ADR 0011 sections 5, 13 and 14 matrix

Test names below refer to `tests/e2e/test_named_dashboard.py`, unless another file
is named. Root and `/proxy` use real HTTPS servers, the production application
factory, disposable roots and synthetic keys. Server barriers control response
headers/body; native browser cookie jars and actual HTTP Cookie headers are
asserted. Notification/feature fault injections do not replace native transport.

| Required boundary | Evidence | Result / limit |
| --- | --- | --- |
| Unlock, nonrenewing GET, seven-day expiry, logout, forged/expired/rotated cookies, current/previous Bearer and invalid Bearer precedence | `tests/test_named_dashboard.py`, existing `tests/test_ui.py`; `test_unlock_restore_selection_logout`, `test_native_current_401_rotation_cleanup_and_previous_key_unlock` | Passed; Windows WebKit cookie enforcement remains a separate failed gate. |
| Exact cookie read allowlist; writes, aliases, MCP and future routes denied; safe errors, guard, duplicate headers, JSON, limiter ordering/no-store | `test_named_cookie_exact_boundary`, `test_every_allowed_cookie_read_never_mutates`, `test_cookie_cannot_authenticate_mcp`, `test_named_session_guard_before_issuance`, `test_session_limiter_once_guard_before_body`, `test_named_route_matrix` in backend suite | Passed. |
| Held GET headers after completed logout; no renewal/deletion and no resurrection | `test_delayed_get_headers_cannot_resurrect` | Passed with actual response headers and cookie jar, abort disabled. |
| Live POST then logout; reverse ordering; old queued login; competing explicit logins; newer logout intent | `test_live_post_then_logout_orders_cookie_mutations`, `test_login_during_live_delete_is_not_replayed_after_completion`, `test_queued_old_login_cancelled_by_logout_intent`, `test_two_deliberate_logins_serialize_without_automatic_replay`, `test_new_logout_intent_fences_older_delete_completion` | Passed after the verified reverse-order defect fix. |
| Orphan POST/DELETE, tab close/reload, lost lock holder, late headers | `test_orphan_mutation_quarantined` | Passed; dirty journal remains quarantined whether the engine accepts or abandons the orphan's response. No abort is interpreted as proof of settlement. |
| Native network failure, partial body after Set-Cookie, failed DELETE 429 and explicit retry | `test_native_mutation_transport_failure_quarantines`, `test_failed_delete_blocks_login_until_explicit_retry` | Passed; native cookie effects and durable quarantine checked across reload. |
| Missing/delayed notifications, absent BroadcastChannel, stale success/401 after current-key rotation and newer deliberate login | `test_missing_notifications_fences_before_protected_fetch`, `test_missing_notifications_rechecks_after_native_json`, `test_native_old_json_after_rotation_and_new_login` | Passed with native JSON/HTTP and shared records; no notification grants authorization. |
| Clean browser reopen/reload, history/background return, no unload mutation, no expiry extension | `test_native_browser_profile_reopen_without_renewal`, `test_native_history_return_blanks_until_validation`, existing hidden-return branch test | Passed. Persistent profile is closed and relaunched; actual history waits on held nonissuing GET headers before rendering. |
| Native BFCache admission and erasure | `test_native_public_bfcache_erases_unsubmitted_key`; `test_named_qualification.py` native history and independent public controls | Public Chromium persisted=true is observed with unchanged production headers and its explicit no-store BFCache feature. Authenticated returns are persisted=false. Firefox/WebKit automation disables BFCache; the safe non-BFCache path passes, but authenticated persisted/suspended qualification remains unexercised. See causal evidence above; no universal cache-eligibility claim. |
| Storage partitions/private contexts and origin isolation | `test_native_context_partition_isolates_cookie_storage_and_locks`, `test_native_other_origin_locks_and_journal_do_not_fence_cookie_delivery` | Passed with independent native browser contexts and separate origins. Same-host other-port cookie scope still spans isolated locks/storage. Embedded/third-party partitions and arbitrary private configurations remain unqualified and unsupported. |
| Native storage quota, inaccessible/read/write storage, missing/malformed/dirty records; unsupported locks/insecure contexts | `test_native_storage_quota_blocks_cookie_issuance`, `test_control_record_write_failure_after_native_cookie_quarantines`, `test_required_browser_features`, `test_native_insecure_context_is_public_only`, `test_cookie_without_history_is_not_silently_adopted`, `test_durable_journal_never_expires_into_permission` | Passed; real quota/insecure-context tests complement explicit API fault injection. No protected/Bearer/per-tab fallback. |
| Bounded nonsecret journal; in-memory selection; no content/write calls; status failures and stale selection | `test_unlock_restore_selection_logout`, `test_responsive_keyboard_selection`, `test_stale_selected_status_never_overwrites_selection`, `test_selected_status_failure_states`, `test_denied_default_and_empty_discovery` | Passed; explicit Apply/Cancel and empty selections retained. |
| Trusted TLS termination, untrusted effective HTTP, root/prefix, alternative Origin rejection | `test_native_tls_termination_profile` and backend guard/route tests | Passed with real TLS edge forwarding to a real plaintext backend, explicitly trusted peer versus untrusted peer. No deployment listener is certified. |
| Same hostname/different HTTPS port; different hostname; same-site versus cross-site; navigation versus fetch; Secure over HTTP; raw receiving headers | `tests/e2e/test_named_cookie_delivery.py` (production and independent issuer); existing same-host receiver regression | Linux Chromium/Firefox/WebKit and Windows Chromium/Firefox pass. Windows WebKit fails Strict delivery for both issuers. Host-only, HttpOnly and Secure observations still succeed there. Assertions are not skipped or weakened. |
| Discovery policy-only zero probes, denied default/zero readable, complete selection before owners, safe projections | Backend discovery/status tests in `tests/test_named_dashboard.py` | Passed. Readable unavailable spaces are not policy-filtered out. |
| Minimal immutable storage observations, exact status mapping, root/storage binding, no scheduler lock under admission, cancellation/shutdown | `test_native_status_readonly_mapping_and_lock_order`, `test_wal_observation_never_opens_or_creates_coordination_files`, `test_scheduler_observation_wait_is_cancellable_before_admission`, `test_status_binding_loss_between_root_guard_and_observation`, native platform suites | Passed on Windows and Linux. WAL remains conservatively unavailable; no schema, signatures, scheduler execution or ranking changes. |
| Legacy controllers and renewing sessions; REST/OpenAPI/MCP inventory/CLI compatibility | Unchanged legacy backend/browser suites, `test_additive_openapi_and_legacy_no_discovery`, full Python suite, production-image MCP smokes | Passed in executed snapshots; final canonical log is authoritative for its selected checks. |

## Local evidence and reproducibility

Ignored `.agent/` artifacts retain command output and JUnit evidence for this
checkout. They are not shipped assets or a substitute for independent review.

- `remaining-qualification/source-snapshot.json` records live upstream API status,
  source URLs/hashes, retained original failure/trace hashes, ADR hash and production
  file hashes. `retained-webkit-traces.json` independently extracts Strict issuance
  flags and cross-site Cookie presence from all four retained native traces, without
  printing cookie values. Version-matched `*.excerpt` files retain the Firefox and
  WebKit automation restrictions cited above.
- `remaining-qualification/windows-qualification.log/xml` and
  `linux-qualification.log/xml`: **36 passed on each platform** for initial bootstrap,
  native history and public-control diagnostics. `windows-history.log/xml` then
  records **24 passed** with full Chromium and nonempty public controls. Those custom
  browser launches bypassed pytest tracing; `windows-history-traced.*` and
  `linux-history-traced.*` record **24 passed per platform** in focused reruns with explicit native trace
  start/stop in `native-traces/`, mapped by the event JSON's native-trace-saved record.
  Preliminary Chromium observer failures
  remain in `windows-chromium-observer-failure.log` and `windows-chromium.xml`; they
  rejected console-only departure evidence, not an application security boundary.
- `remaining-qualification/windows-final.log/xml`, `linux-final.log/xml` retain
  the complete affected named/browser/cookie suites: **246 passed** for Windows
  Chromium/Firefox and **368 passed** for Linux Chromium/Firefox/WebKit, no skips.
  Windows WebKit's unchanged Strict failures are
  deliberately not rerun by these commands; its new lifecycle/barrier cases are
  covered by the separate three-engine qualification/history runs.
- `remaining-qualification/windows-bootstrap.log/xml` and
  `traces-bootstrap-windows/` preserve full native traces and uncaptured server
  logs for both explicit bootstrap barriers on all three Windows engines.
  `native-events/*.json` contains sequenced native diagnostics across focused
  and canonical named contexts, with explicit snapshot labels in barrier/history tests.
- `remaining-qualification/backend.log/xml`: **110 passed** for named boundaries
  and unchanged legacy session behavior, including server-side guard denial paths.
- `remaining-qualification/native-freeze-probe.py`, `windows-frozen-form-expectation.*`,
  `windows-freeze-not-observed.log`, `linux-freeze-not-observed.log`, frozen XML and
  native event JSON retain unsuccessful diagnostic schedules. These custom contexts
  did not record native traces. Two cases failed per
  platform because native freeze/resume was not observed; they are not passing
  suspension tests. The probe is kept out of the collected suite rather than
  skipping it, removing its failed assertions, or calling protocol success admission.
- `remaining-qualification/agent-finish.log` is the completion-workflow output for
  this pass; the handoff reports its actual exit/result and generated packet. It uses
  the exact original inline `--task` text from `.agent/task_packet.md`, preserved
  below. The previous packet is retained as `remaining-qualification/review_packet.before.md`.
  An automated selector pass does not waive any matrix gap or constitute independent review.
- `remaining-qualification/agent-finish-interrupted.log` retains an additional
  canonical attempt interrupted during the Python suite, before any exit result
  or review packet existed. It is incomplete execution evidence, not a failed test
  or a completed workflow. The full original workflow was restarted with unchanged
  selection/task source; no partial result substitutes for its final outcome.

Original inline task source (unchanged for `agent_finish.py`):

```text
Implement VB-156B named Knowledge Spaces Dashboard foundation according to accepted ADR 0011 section 13, including named browser sessions, policy-filtered discovery, explicit selection, selected-space status, security regression tests and legacy compatibility.
```

The following artifacts predate this remaining-findings pass and remain retained:

- `vb156b-windows-webkit-control.log/xml`: independent Windows WebKit
  qualification, **4 failed**, production/control at root/prefix. Actual mismatch
  is cross-site navigation and cross-site fetch, with stored SameSite=None.
  `windows-webkit-traces/` retains all four native traces; their network records
  also show Cookie on requests marked Sec-Fetch-Site: cross-site. The issuer's
  raw Strict header and the receiving server's observations remain asserted.
- `vb156b-skip-audit.xml`: full native Windows Python snapshot, **4053 passed,
  316 skipped**. Every skipped node maps to a POSIX/Linux-native requirement or
  unavailable Windows symlink privilege, not a skipped VB-156B browser gate.
- `vb156b-skipped-nodeids.json` and `vb156b-native-skips.log/xml`: all **316
  exact skipped nodes passed** as ordinary WSL user, with no skips. Includes
  inotify, native descriptors/aliases, binding, SQLite replacement and permissions.
- `vb156b-windows-qualified.log/xml`, `vb156b-linux-qualified.log/xml`:
  native named lifecycle plus cookie qualification runs: **214 passed** on Windows
  Chromium/Firefox and **320 passed** on Linux Chromium/Firefox/WebKit. Separate
  `vb156b-windows-origin.log/xml` (**8 passed**) and `vb156b-linux-origin.log/xml`
  (**12 passed**) record the subsequently added different-origin lock/journal
  tests. Together these cover the current named suite, without skipped assertions.
- `vb156b-linux-recorded.log/xml`: **332 passed, no skips** in the complete
  Chromium/Firefox/WebKit named suite after adopting the trace-recording factory.
- `vb156b-backend-continuation.log` and `vb156b-linux-backend-continuation.log`:
  **84 passed on each platform**, including restoration of the exact token by a
  newly constructed production application with the same signing key.
- `vb156b-native-bfcache.log`: native persisted Chromium public-page restoration.
- `vb156b-linux-webkit-deps.log`, `vb156b-linux-x11-repair.log`: infrastructure
  repair/install evidence. `vb156b-docker-build.log` and
  `vb156b-mcp-containers.log`: successful production build, dependency, stdio and
  HTTP read-only/write-enabled container checks using disposable data.
- `vb156b-finish.log`: final canonical `agent_finish.py` result, with the original
  unchanged task source. The selector's Chromium gate alone cannot waive the
  Windows WebKit security failure or certify a deployment.
- `vb156b-finish-bootstrap-failure.log`: first continuation finish attempt,
  **exit 1**, full Python 4053 passed/316 skipped, Chromium 147 passed/1 failed,
  all remaining selected checks passed, no review packet. `vb156b-bootstrap-suite.log`
  records the subsequent **148 passed** diagnostic run with tracing available.
  `vb156b-bootstrap-repro.log`, `vb156b-bootstrap-native.log` and
  `vb156b-bootstrap-fresh.log` retain the 30/300/200 successful diagnostic schedules.
  They do not supersede the unexplained failing schedule.

Run cookie qualification without xfails/skips on each intended platform:

```text
python -m pytest tests/e2e/test_named_cookie_delivery.py --browser chromium --browser firefox --browser webkit -o addopts= --tracing retain-on-failure
```

Windows Playwright's WebKit port is not Safari/macOS qualification. Linux WebKit
success does not make the Windows port's observed delivery safe. No user-agent
fallback, weaker SameSite attribute, server-readable API key storage or application
port-isolation claim is introduced to hide that distinction.

## Remaining mandatory acceptance gaps

VB-156B remains **INCOMPLETE**. Required Windows WebKit Strict delivery is failed
on the installed build; qualification needs a corrected native build and the
unchanged receiving-header assertions. The original Chromium canonical bootstrap
failure remains causally unexplained. Authenticated native BFCache admission and
logout while actually suspended were not observed; safe non-BFCache history
restoration and an automation-disabled engine do not establish those scenarios.
Separate independent approval is also outstanding. Neither the prior successful
completion workflow nor a new selector pass changes these acceptance statuses.

## Remaining trust boundaries

Dedicated-hostname trust includes every HTTPS port/listener and proxy route,
with one canonical cookie issuer. Native origin locks/journals do not coordinate
other origins that can modify the same hostname cookie. Drain legacy renewers
and all old issuers before handover. Operator inventory/acknowledgement, real TLS
termination, private/embedded browser profiles and real deployments require their
own evidence. Copied tokens are not revoked by logout. An uncertain mutation
requires operator-confirmed draining and recovery; clearing browser data alone
does not prove settlement. No release, TrueNAS, upgrade or production-load claim
is made.
