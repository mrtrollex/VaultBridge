# VB-156B qualification evidence

## Final implementation and owner acceptance (2026-11-22)

**VB-156B implementation is COMPLETED and OWNER-ACCEPTED** under accepted
[ADR 0011](adr/0011-named-knowledge-spaces-dashboard.md) and
[ADR 0012](adr/0012-vb-156b-qualification-amendment.md), for the named session,
policy-filtered discovery, explicit selection and selected-status foundation within
the five qualified browser/platform profiles and trust boundaries recorded below.
The latest fresh independent final evidence-durability review returned **APPROVE**,
closed the remaining HIGH finding and found no blocking findings. The project owner
then explicitly accepted the final implementation and authorized PR preparation.

Acceptance is tied to the unchanged 251-input executable/check-input fingerprint
`cf8961d2a813117393a08aa7159c602548e5fdfbe4538ea1ce0a649eb801a32e`,
**501 Windows / 749 native Linux browser E2E passes** with no failures/skips, and full
backend/Python results of **4053 passed / 316 platform skips on Windows** and
**4277 passed / 92 Windows-only skips on Linux**, including evaluation. Original
commands, source/build maps, native observations and outcomes remain in the immutable
[durable final-source evidence bundle](evidence/vb-156b-final/README.md).

This dated acceptance record supersedes capture-time PENDING language in that bundle
and the historical records below; those artifacts remain unchanged. ADR 0012 W/C/H
exclusions, unresolved Chromium availability risk, unqualified authenticated BFCache
admission/suspended logout and unqualified Windows Playwright WebKit remain in force.
No Safari qualification or VB-156C/D/E completion is claimed. PR creation, publication,
a new release and deployment remain unauthorized and retain separate future gates.
This documentation-only finalization changes no application, test, dependency or runtime
behavior and performs no new qualification execution.

## Final-source evidence durability repair

The independently verified final-source qualification is now available in the
[durable, hash-indexed evidence bundle](evidence/vb-156b-final/README.md), including
original test identities/outcomes, source/build maps, native transport/history records,
numeric layout measurements and sanitized execution output. This fixes the confirmed
HIGH durability finding, now closed by the final independent review; no production/test
source was changed. Both accepted ADRs, all 150 historical artifacts and all final-source
evidence artifacts remain byte-for-byte unchanged. Implementation acceptance is recorded
above; release/deployment remains **NOT AUTHORIZED**. The evidence repair itself did not
constitute independent review, publication, deployment or VB-156C/D/E completion.

Reproduce the read-only source, count, security, sanitization and integrity checks:

```text
python docs/evidence/vb-156b-final/verify.py --self-test
```

The [source manifest](evidence/vb-156b-final/source-fingerprint.json) specifies the original
canonical 251-input raw-byte procedure, per-file hashes and ordered set. The original
fingerprint `cf8961d2a813117393a08aa7159c602548e5fdfbe4538ea1ce0a649eb801a32e`
was independently recomputed and matches all five retained execution source maps.
The bundle documents explicit recorded-EOL reproduction after a Git checkout/commit;
it never accepts changed LF content. Its scoped Git attribute preserves artifact
bytes without changing any implementation/test file. Original unsuccessful evidence,
private recovery backups and historical source-linkage limitations are preserved.

## Confirmed-finding repair (2026-11-22)

Both actionable independent-review findings are repaired. Final-source browser
qualification passes on the five eligible ADR 0012 profiles. Final independent review
**APPROVE** and explicit owner acceptance are recorded above. This is not a release,
publication, deployment or completion of VB-156C/D/E.

### Confirmed scope and correction

- **TCP/DELETE fixture race:** an accepted socket could pass the old failed-state
  check, wait in CONNECT parsing outside the tracked set, and forward a retry after
  disconnect. The deterministic pre-fix socket regression actually received complete
  successful DELETE JSON after disconnect; the retained original Firefox failures
  also recorded complete responses/two server executions. The original focused
  Firefox rerun in this repair happened to pass with native resets; that does not
  explain away the original failure or replace the deterministic reproduction.
- The proxy now accepts/registers sockets under one condition lock before handler
  dispatch. CONNECT parsing, upstream connect and backpressured forwarding use
  cancellable native socket waits. Every nonblocking write and failure transition
  shares the lock. Disconnect permanently fails that generation, rejects retries,
  wakes all pending/active work, and returns only after worker cleanup closes every
  owned socket. Tests require that acknowledgement before releasing held responses.
  Explicit test-only network recovery starts a new generation after disposal; it
  never revives old sockets or resets browser uncertainty. No arbitrary sleep,
  timeout inflation or client-side retry was introduced.
- **Legacy reflow:** `.app-header__identity h1` alone gains
  `overflow-wrap: anywhere`. Normal typography, layout, focus rules and named CSS
  remain unchanged. No horizontal overflow hiding or clipping was added.
- **Production session logic is unchanged.** No application session-settlement
  defect was established. Cookie attributes, guards, privacy/cache headers, API,
  MCP, CLI, SQLite, ranking, storage and deployment contracts are preserved.

### New final-source results

| Execution | Result |
| --- | --- |
| Windows Chromium/Firefox complete E2E | 501 passed; no failures/skips |
| Native WSL/Linux Chromium/Firefox/WebKit complete E2E | 749 passed; no failures/skips |
| Native Linux full Python, including evaluation | 4277 passed, 92 Windows-only skips |
| HTTP 401 matrix (subsets of the complete runs) | 200 Windows / 300 Linux passed |
| Ambiguous native POST/DELETE TCP/truncated-body outcomes | 16 Windows / 24 Linux passed |
| Complete successful DELETE positive control | 4 Windows / 6 Linux passed |
| Deterministic socket regressions | 3 passed per platform |
| Bootstrap/history/public-control qualification | 24 Windows / 36 Linux passed |
| Native named backend security/status/discovery | 84 passed within the Linux full suite; also selected by canonical Windows full verification |
| Ruff and compileall | Passed; repeated by the final canonical workflow |

The exact original task was used by the recorded 2026-11-22 qualification completion
run. Its full Windows Python, Chromium, Docker/MCP and exit-0 outcomes are retained in
the [sanitized original output](evidence/vb-156b-final/validation/windows-finish.log.txt)
and [execution records](evidence/vb-156b-final/validation-results.json). Quiet Python
progress accounts for 4053 passed / 316 platform skips and is independently recounted
by the bundle verifier. Tooling success is not independent review. No mandatory browser
profile is skipped or xfailed.

At 320 CSS pixels all five exercised engines record
`innerWidth = clientWidth = scrollWidth = 320`. Linux headings wrap to fit; at 375
and 1280 their dimensions/font match the same-page normal-wrapping control exactly.
The header stays visible, real keyboard Tab navigation retains visible focus, and
named composition/selection/keyboard tests verify the legacy selector does not match
the named heading. Actual values are retained in the
[Windows measurements](evidence/vb-156b-final/layout/windows-measurements.json) and
[Linux measurements](evidence/vb-156b-final/layout/linux-measurements.json).

### Native transport, cookie and uncertainty proof

The durable [Windows](evidence/vb-156b-final/security/windows-tcp-delete-chronology.json)
and [Linux](evidence/vb-156b-final/security/linux-tcp-delete-chronology.json) chronologies
cover **4 Windows and 6 Linux TCP/DELETE cases** at root/prefix on all eligible engines.
They join to the retained [native channels and mutation observations](evidence/vb-156b-final/README.md#native-security-and-settlement-evidence).
The verifier independently requires
one DELETE execution per logical request, no complete successful browser response,
no forwarding after the failure transition, actual socket-disposal acknowledgement,
and server response emission only after that acknowledgement. Server emission is
not mislabelled browser receipt. Logs retain native error names without credentials.

Tests additionally assert journal uncertainty, no complete mutation JSON, protected
DOM erasure, hidden unlock controls, and no session/API dispatch across reload and
sibling tabs even after explicit transport recovery. With pre-header TCP failure,
DELETE's old cookie remains but cannot be silently adopted; truncated responses can
already apply Set-Cookie, yet still remain uncertain. No aborted fetch is treated as
proof of non-execution. A separate complete native DELETE 200/JSON control reaches
locked journal/removal-confirmed with the cookie removed and no retry.

### Precisely identified source and preserved evidence

Raw application/test/script/check-input fingerprint:
`cf8961d2a813117393a08aa7159c602548e5fdfbe4538ea1ce0a649eb801a32e`.
The [per-execution source maps](evidence/vb-156b-final/README.md#executions-and-exact-outcomes)
retain every input hash and prove Windows/Linux equivalence for browser, Python and
completion executions. The source remains uncommitted over
`8dd6f2a421f017dc954d6cb036e42b9b583bdd95`; no final implementation commit is invented.
Future PR reviewers can reproduce the fingerprint against implementation files using
the bundle verifier and its explicit recorded-EOL mode.

Exact commands/dates/exits, all final JUnit identities, native events/trace extracts,
browser revisions, numeric measurements and causal failure records are accessible in
the [durable index](evidence/vb-156b-final/README.md). The
[manifest](evidence/vb-156b-final/manifest.json) maps original-to-sanitized hashes and
methods; [sanitization and omissions](evidence/vb-156b-final/SANITIZATION.md) documents
credential/body/DOM exclusions and any absent original trace. Ignored records/backups
remain private originals, not prerequisites for reviewing material acceptance claims.
All original failed records and the 150 immutable historical artifacts remain preserved
with their actual source/review limitations. No historical run is attributed to this
final source without its own recorded linkage.

### Accepted exclusions and future release/deployment gates

Windows uses Playwright 1.63.0 Chromium 153.0.8010.12 / 1243 and Firefox 155.0 / 1543.
Actual WSL/Linux uses 1.64.0 Chromium 156.0.8078.4 / 1248, Firefox 157.0 / 1555 and
WebKit 27.2 / 2370. The exact SDK/browser catalogs are retained with the
[execution matrix](evidence/vb-156b-final/execution-matrix.json).
Windows WebKit 26.6 / 2359 remains **EXCLUDED / NOT QUALIFIED** under ADR 0012 W;
its four original Strict failures/assertions are unchanged. No Safari testing is claimed.

ADR 0012 C's original Chromium bootstrap incident remains unresolved; this fixture
repair is not a causal bootstrap fix or retirement of its reopening criteria.
The [ADR 0012 evidence map](evidence/vb-156b-final/history/alternative-evidence-map.json)
retains all four required H classes: new native history,
held nonissuing validation/erasure/logout-away, deterministic lifecycle/journal/stale
response checks, and native controls plus matching harness records/traces and retained
failed suspension probes. Those records are retained/run without weakening privacy
headers. Unobserved authenticated BFCache admission/suspended logout remains
unqualified. Historical intermediate source linkage remains explicitly unverified;
no historical result is silently attributed to this final source.

The separate final independent review and implementation owner-acceptance gates are
satisfied on 2026-11-22 for the fingerprint and qualified scope above. Future
release/deployment still requires its own authorization, exact-source CI/image/operator
evidence, reassessment of W/C/H, and dedicated-hostname/issuer/TLS/legacy-handover trust
acknowledgement. PR preparation does not authorize those actions or PR creation and
implements no C/D/E feature.

## Historical integration record (before this repair)

The following record preserves the initial final-integration source, results and
four failures that led to the two confirmed findings. Its INCOMPLETE and pending-review
language is historical; the final implementation/owner-acceptance and repair records
above govern current status.

### Final integration record (2026-11-22)

**INCOMPLETE.** The existing implementation was preserved and integrated with current
`origin/main` at `8dd6f2a421f017dc954d6cb036e42b9b583bdd95` by fast-forward, then the named stash was
applied without conflicts. No production code, tests, assertions, cookie attributes,
guards or privacy/cache headers were changed during this integration. The confirmed
HTTP 401-before-body-parsing repair remains present. Final independent implementation
review/acceptance remains pending. VB-156C/D/E are outside this work.

[ADR 0011](adr/0011-named-knowledge-spaces-dashboard.md) and the accepted
[ADR 0012](adr/0012-vb-156b-qualification-amendment.md) are unchanged. All **150** files
under `docs/evidence/vb-156b/` match the integrated accepted baseline. Their historical
failures, intermediate source-provenance limits and capture-time status wording remain
unchanged. No intermediate historical run is attributed to the source below.

Git's automatic checkout CRLF conversion was detected during the byte-level audit.
The 150 historical files and both accepted ADRs were restored directly from accepted
Git blobs, without regenerating evidence or changing its hashes. All 152 now match
those blobs exactly; `accepted-byte-preservation.json` retains that verification and
the provenance manifest hash remains the exact value accepted in ADR 0012.

#### Precisely identified current source

The source is an uncommitted implementation over the integrated commit, not a new
implementation commit. `.agent/final-integration/final-source.json` records all
**250** executable/check-input file hashes and the source archive hash.
`windows-*-source.json` and `linux/final-integration/linux-*-source.json`
record the source identity for each new run, checked again at completion. The Linux
copy was extracted from the captured archive onto a native Linux filesystem and run
as an ordinary WSL user; it was not a Windows test or a platform mock.

- Raw-byte source fingerprint: `fc239d0fe2f1cd99f4d16a3230c7d3614f4242e3b5b6ff35e93b1cbf739a1552`.
- Cross-platform CRLF-normalized fingerprint: `e2381fd0453e04756283641bc4d7042cbdc9992f28c6b1c0828255761ccbca34`.
- Both fingerprints hash the sorted path-to-file-SHA-256 JSON map using UTF-8 and
  compact separators. Current-state documentation is outside that executable/check-input
  map; its final diff is part of the review handoff. The full source archive is retained.
- `.agent/final-integration/preservation.json` identifies the verified external ZIP
  backup, retained stash and successful restoration. The original task and review
  packets were also copied locally before completion workflow regeneration.

#### New final-source execution results

| Check | Actual result |
| --- | --- |
| Native Windows full non-E2E Python, including evaluation | 4053 passed, 316 skipped |
| Native WSL/Linux full non-E2E Python, including evaluation | 4277 passed, 92 skipped |
| Windows Chromium + Firefox, all E2E | 492 passed, 2 failed |
| Native WSL/Linux Chromium + Firefox + WebKit, all E2E | 738 passed, 2 failed |
| Ruff and compileall | Passed on Windows and native Linux |
| Docker production build | Passed; all 92 application files matched the captured source |
| MCP image dependency, stdio and HTTP container smoke | Passed, including read-only/write-enabled HTTP configurations with disposable data |
| `git diff --check` | Passed before completion workflow |

Exact commands, timestamps, exit codes, JUnit and file hashes are retained under
`.agent/final-integration/`; `summary.json` maps each run to its source fingerprint
and records platform-skip cross-coverage. Native lifecycle traces/events are retained
alongside the Linux evidence copy and the newly generated Windows event inventory.
No skip, xfail, retry, timeout inflation or assertion weakening was added.
All 316 Windows skips passed on Linux, and all 92 Linux skips passed on Windows;
there is no node skipped on both platforms. Named backend tests passed 84 per platform.
The HTTP 401 regression matrix passed 200 Windows and 300 Linux cases, native cookie
delivery passed 8 and 12, and bootstrap/history/public-control qualification passed
24 and 36 respectively. `feature-results.json` maps those subsets to the complete
JUnit runs. All 16 Windows and 24 Linux authenticated history snapshots recorded
non-persisted returns; no authenticated BFCache admission was observed.

The final canonical workflow uses the exact original inline task and writes
`.agent/final-integration/windows-agent-finish.log` and its result JSON. Its actual
exit/packet result is authoritative for selector checks only: a passing Chromium
selector cannot waive the additional browser failures below.

#### New non-exempt failures and acceptance blockers

1. Windows Firefox: `test_native_mutation_transport_failure_quarantines[root-https-firefox-tcp-DELETE]`
   and `[prefix-https-firefox-tcp-DELETE]` failed. The expected uncertain state was
   replaced by confirmed session removal; native evidence records a successful DELETE
   response. The TCP fault schedule therefore did not demonstrate the required
   quarantine in these executions. Whether transport setup or application behavior
   caused the mismatch is unresolved; no causal correction or harmlessness is claimed.
2. Linux Firefox and WebKit: legacy
   `test_hygiene_all_evidence_grouping_bounds_inert_text_and_reflow[firefox]` and
   `[webkit]` failed the 320 CSS-pixel overflow assertion. The native trace records the assertion's
   false result. This is a compatibility failure, not an ADR 0012 exception.
3. A new independent review tied to the final integrated source and final acceptance
   remain pending. The retained earlier implementation/401-repair approval is not
   expanded into approval of this final combined state.

The first failing logs, JUnit and native traces are preserved. These failures remain
open even if another selected run passes; no diagnostic rerun is presented as a fix.
No new protected-state disclosure or implementation cause is inferred solely from
the failed assertions. These profiles cannot receive completed final qualification
while their non-exempt failures remain unresolved.

#### Exact ADR 0012 profile and exception application

| Native profile | SDK | Engine version / revision | Current evidence boundary |
| --- | --- | --- | --- |
| Windows 11 build 26300 Chromium | 1.63.0 | 153.0.8010.12 / 1243 | New final-source tests; Chromium bootstrap risk remains unresolved under C |
| Windows 11 build 26300 Firefox | 1.63.0 | 155.0 / 1543 | New final-source tests include the two non-exempt TCP/DELETE failures |
| Actual WSL/Linux Chromium | 1.64.0 | 156.0.8078.4 / 1248 | New native final-source tests; C is not assumed Windows-only |
| Actual WSL/Linux Firefox | 1.64.0 | 157.0 / 1555 | New native final-source tests include the legacy reflow failure |
| Actual WSL/Linux WebKit | 1.64.0 | 27.2 / 2370 | New native final-source tests include the legacy reflow failure; no Windows or Safari inference |
| Windows Playwright WebKit | 1.63.0 | 26.6 / 2359 | EXCLUDED / NOT QUALIFIED under W; four original Strict-cookie failures retained unchanged |
| Safari/macOS/iOS and other builds | Not exercised | No qualified build | UNQUALIFIED |

Qualification is bounded to the tested top-level context/storage partition, root and
`/proxy` HTTPS, functioning native Web Locks/shared localStorage, and exercised trusted
TLS termination. Exclusion is a qualification statement, not runtime access blocking.

- **W:** The four unchanged production/control root/prefix Strict assertions remain
  failed historical evidence for the excluded Windows WebKit profile. They were not
  rerun or converted into skips/xfails. Cookies and server guards were not weakened;
  Linux WebKit is a separate profile. Removal still requires every ADR 0012 criterion.
- **C:** Original missing-`spaces.js`/Checking browser support failure remains open,
  with its missing original trace and incomplete historical source linkage disclosed.
  New root/prefix dependency and native-lock barriers are part of the final-source
  suites, not a causal repair. Recurrence, barrier failure or new causal-boundary
  evidence reopens/suspends acceptance exactly as ADR 0012 specifies.
- **H:** All four alternative classes remain required: native authenticated history
  with departure/state erasure; held nonissuing validation with unchanged cookie,
  expiry and journal plus locked logout-away; deterministic controller/notification/
  stale-response/shared-journal branches; native public controls, matching build/harness
  records, traces/events and preserved failed suspension probes. The new suites run
  the history, public-control and deterministic tests on the five eligible profiles.
  Retained version-matched harness configuration records and failed suspension probes
  remain unchanged. Actual authenticated persisted admission and logout of a truly
  BFCache-suspended authenticated document remain unqualified where not observed;
  successful non-BFCache restoration is not relabelled BFCache admission. No privacy
  or cache header was changed. New admission/suspension or erasure/revalidation
  regressions reopen H as specified in the accepted ADR.

The implemented Access/Spaces/Status foundation, exact named UI gate, nonrenewing
restoration, Web Locks/journal, route allowlist, Origin/Fetch Metadata/CSRF guards,
policy-first discovery/explicit selection, selected-only side-effect-free status,
401 erasure before body parsing and legacy composition remain in source. Backend
and browser test outcomes above determine verification; implementation presence
alone does not close failed acceptance gates.

Release/publication/deployment remain separate and unauthorized. Exact-source CI,
applicable release/image evidence and owner approval must carry the profile matrix,
exceptions and reopening conditions. Dedicated-hostname inventory across every port/
proxy route, canonical issuer, trusted TLS and drained legacy handover remain real
operator requirements. No deployment, publication, general Safari support, universal
private/embedded-context support or VB-156C/D/E completion is claimed.

### Historical pre-integration qualification record (2026-11-21)

The following dated record is preserved as historical context. Its old completion
language predates accepted ADR 0012 and this integration. The current record above
and the accepted ADR govern current status. Historical passing results with incomplete
contemporaneous source linkage do not qualify the final integrated source.

This is local synthetic qualification on 2026-11-21, not independent review, CI,
release, deployment, or an audit of real listeners. **Overall status remains
INCOMPLETE: Windows Playwright WebKit fails native Strict-cookie delivery, one
Chromium bootstrap failure has no established causal explanation, and authenticated
BFCache admission/suspended-page qualification was not exercised.**
ADR 0011 is unchanged. VB-156C/D/E are outside this work.

#### Failure classification and verified corrections

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

#### Remaining-findings analysis (2026-11-21)

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

##### Chromium bootstrap: causal boundary, not a claimed fix

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

##### Authenticated BFCache versus safe native history restoration

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

##### Windows WebKit upstream status and exact affected build

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

#### ADR 0011 sections 5, 13 and 14 matrix

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

#### Local evidence and reproducibility

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

#### Remaining mandatory acceptance gaps

VB-156B remains **INCOMPLETE**. Required Windows WebKit Strict delivery is failed
on the installed build; qualification needs a corrected native build and the
unchanged receiving-header assertions. The original Chromium canonical bootstrap
failure remains causally unexplained. Authenticated native BFCache admission and
logout while actually suspended were not observed; safe non-BFCache history
restoration and an automation-disabled engine do not establish those scenarios.
Separate independent approval is also outstanding. Neither the prior successful
completion workflow nor a new selector pass changes these acceptance statuses.

#### Remaining trust boundaries

Dedicated-hostname trust includes every HTTPS port/listener and proxy route,
with one canonical cookie issuer. Native origin locks/journals do not coordinate
other origins that can modify the same hostname cookie. Drain legacy renewers
and all old issuers before handover. Operator inventory/acknowledgement, real TLS
termination, private/embedded browser profiles and real deployments require their
own evidence. Copied tokens are not revoked by logout. An uncertain mutation
requires operator-confirmed draining and recovery; clearing browser data alone
does not prove settlement. No release, TrueNAS, upgrade or production-load claim
is made.
