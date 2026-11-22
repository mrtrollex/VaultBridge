# ADR 0012: VB-156B qualification contract amendment

- **Status: ACCEPTED — qualification contract amendment only**
- **Date:** 2026-11-21
- **Acceptance date:** 2026-11-22
- **Task:** VB-156B qualification contract amendment; documentation/architecture only
- **Authority:** project owner explicitly accepted this amendment and decisions W/C/H in the task
  "Final Owner Acceptance — ADR 0012" on 2026-11-22, after fresh independent review APPROVE.
- **Independent review:** "Review ADR 0012 Evidence Repair", 2026-11-22,
  review task `01a20210-1b1f-73b1-8a75-d118e4dd8a46`, verdict **APPROVE**. The review found the
  original HIGH durability finding addressed for amendment review, with historical limitations
  disclosed; it did not qualify the implementation or accept residual risks on the owner's behalf.
- **Accepted evidence bundle:** all 150 artifacts are preserved; the provenance manifest SHA-256 is
  `c41bc4a14210382a849462a9bcd252e2834b2b03a1b0cf9138c35b3990adbff3`.
- **Scope:** three qualification limitations in ADR 0011 sections 5, 13 (B) and 14.

## Context and decision boundary

[ADR 0011](0011-named-knowledge-spaces-dashboard.md) remains **ACCEPTED and technically
unchanged**. This separately reviewed, owner-accepted ADR supplements only its three bounded
qualification requirements below. Final VB-156B implementation integration and acceptance remain
separate and pending; acceptance of this amendment does not mark VB-156B complete.
ADR numbering was inspected: 0001–0011 exist; 0012 is the next unused ID.

The task owner reports an independent implementation review verdict **APPROVE**. The retained
[HTTP 401 repair review](../evidence/vb-156b/reviews/401-repair.md) approves that repair and its
integration after the [earlier implementation finding](../evidence/vb-156b/reviews/implementation-before-401.md);
its recorded scope must not be expanded into full qualification approval. That approves
the reviewed implementation; it does not accept incomplete qualification, this amendment, residual
risks, task completion, a release or a deployment. This documentation checkout is the accepted
design baseline, not the separately held implementation working tree. Its older future-task wording
is not new implementation evidence. This amendment neither imports nor enables that implementation.

The implementation's dated [qualification report](../evidence/vb-156b/qualification-report.md)
and the [durable evidence record](../evidence/vb-156b/README.md) document
three limitations: Windows Playwright WebKit fails Strict-cookie delivery; an original Chromium
bootstrap failure remains unexplained; authenticated BFCache admission/suspension was not observed.
The latter applies to Chromium too, not only Firefox/WebKit. No runtime security requirement is
removed. Only the following bounded evidence/acceptance rules are amended.

## Evidence boundary and preservation

Evidence inspected for this amendment is the separate VB-156B implementation record dated
2026-11-21. No browser or runtime suite was rerun in this documentation session. Sanitized records
below are included in this repository change; verification does not require the other worktree,
ignored files or raw traces. Original artifact names survive only as redacted provenance.

| Retained record | Identity / meaning |
| --- | --- |
| [Provenance manifest](../evidence/vb-156b/provenance.json), [commands](../evidence/vb-156b/commands.json) and [sanitization](../evidence/vb-156b/SANITIZATION.md) | Per-artifact original/sanitized hashes, commands, test dates, source/review relationship and transformation boundaries |
| [Build manifests](../evidence/vb-156b/builds.json) | Exact SDK, engine revision and version mapping used below |
| [Source snapshots](../evidence/vb-156b/source-snapshots.json), [source content](../evidence/vb-156b/source/implementation-changes.txt), [recovery record](../evidence/vb-156b/source-recovery.json) | Verified partial production/final-repair hashes distinguished from historical executions with unverified complete source linkage; no invented implementation commit |
| [Original Strict failures](../evidence/vb-156b/junit/vb156b-windows-webkit-control.xml.json), [receiving-server observations](../evidence/vb-156b/webkit-receiver-observations.json) | All four failed production/control root/prefix cases; all nine receiver scenarios per case; minimal native trace extracts indexed by provenance |
| [Original bootstrap failure log](../evidence/vb-156b/logs/vb156b-finish-bootstrap-failure.log.txt) | Original canonical exit 1, Chromium 147 passed/1 failed; original-byte hash and sanitized hash are separate in provenance |
| [All JUnit results](../evidence/vb-156b/results.json) and [historical report](../evidence/vb-156b/qualification-report.md) | 246 Windows and 368 Linux historical passes, no skips; original failures and intermediate failed diagnostics retained separately; never attributed silently to final source |
| [Native history observations](../evidence/vb-156b/history-observations.json), [native event chronology](../evidence/vb-156b/native-events.json) | Exact JUnit-correlated native history/control records and sanitized network extracts; 24 focused passes per platform, no authenticated BFCache admission |
| [401 repair record](../evidence/vb-156b/source/401-repair--repair-evidence.md.txt), [review verification](../evidence/vb-156b/reviews/401-verification.json) | Final 300 focused native passes per platform, canonical results and the recorded independent hash/probe checks; qualification gaps stay open |

Do not overwrite failures with later passing output or delete failed diagnostic assertions/artifacts.
The [evidence index](../evidence/vb-156b/README.md) makes the retained content reviewable now;
raw traces are replaced by documented event extracts, never shipped with credentials or bodies.
Current capture has base commit `90886af56f911b7708c9749bf63ab2f17816408b` plus an explicitly
uncommitted source fingerprint. It is not an immutable implementation commit or a historical tree.

**Unresolved historical provenance limitation:** history/reflog, task artifacts, review-time hashes and retained
logs do not recover complete contemporaneous source states for the original/intermediate runs.
The production map and final 401 repair hashes are verified; earlier results with incomplete
source provenance remain explicitly unverified as to exact source linkage. The original failing
bootstrap native trace was never recorded. Do not reconstruct it from current code, treat hashes
as accessible source content, or silently attribute historical passes to the final implementation.
These historical results cannot establish final-source qualification; exact historical source
linkage remains unverified and is not a fourth exception accepted by W/C/H. As the fresh review
records, new runs against a precisely identified current snapshot can establish current
qualification without reconstructing unavailable history. Final implementation integration and
acceptance remain **INCOMPLETE**. Acceptance of this amendment neither repairs historical provenance
nor reclassifies any failed or unobserved scenario as passing.

All 150 durable evidence artifacts remain byte-for-byte unchanged as the historical record.
Their PROPOSED/PENDING status wording describes the capture-time decision state; this ADR's dated
acceptance record and the current backlog govern amendment status. Historical outcomes, source
limitations and reviewer scopes in those artifacts are preserved, not regenerated.

## Browser and platform qualification matrix

The following table is preserved verbatim from the reviewed proposal. Its five eligible profiles
define the accepted qualification contract under these three bounded limitations, subject to the
separate final-source implementation completion gates below. Historical passes alone do not grant
final implementation qualification. This is not unconditional engine-family support.

| Native platform exercised | Playwright SDK | Engine version / revision | Proposed qualification scope |
| --- | --- | --- | --- |
| Windows 11, build 26300 | 1.63.0 | Chromium 153.0.8010.12 / 1243 | Eligible; unresolved bootstrap availability risk and authenticated BFCache evidence limitation retained |
| Windows 11, build 26300 | 1.63.0 | Firefox 155.0 / 1543 | Eligible; authenticated BFCache evidence limitation retained |
| Actual WSL/Linux environment in the retained record | 1.64.0 | Chromium 156.0.8078.4 / 1248 | Eligible; unresolved Chromium bootstrap risk is not assumed Windows-only; authenticated BFCache evidence limitation retained |
| Actual WSL/Linux environment in the retained record | 1.64.0 | Firefox 157.0 / 1555 | Eligible; authenticated BFCache evidence limitation retained |
| Actual WSL/Linux environment in the retained record | 1.64.0 | WebKit 27.2 / 2370 | Eligible; authenticated BFCache evidence limitation retained; no Windows or Safari inference |
| Windows 11, build 26300 | 1.63.0 | WebKit 26.6 / 2359 | **EXCLUDED / NOT QUALIFIED**: original Strict tests fail |
| macOS/iOS Safari; other builds/platforms | Not exercised | No qualified build | **UNQUALIFIED**: Playwright WebKit is not Safari testing |

Each eligible profile is restricted to tested top-level pages sharing one browser context/cookie
jar and storage partition, root and `/proxy` HTTPS, with native Web Locks and functioning shared
localStorage. Retain direct HTTPS and the tested trusted TLS-termination profile. Private/embedded/
third-party partitions, arbitrary private configurations, other OS/browser revisions and real
deployment listeners inherit no qualification. Separate contexts demonstrate isolation, not universal
private-mode support. A newer version label alone neither grants qualification nor removes an exception.

## Exception 1: exclude Windows Playwright WebKit until corrected

The external [Playwright defect #44141](https://github.com/microsoft/playwright/issues/44141)
and [WebKit correction #81431](https://github.com/WebKit/WebKit/pull/81431) concern explicit
SameSite enforcement in the Windows port. As checked on 2026-11-21, the Playwright issue is closed
and the upstream PR is open; closure or proposed source correction is not a shipped corrected
binary. The tested Windows WebKit stores the synthetic Strict cookie as None and sends it on
genuinely cross-site navigation/fetch for both production unlock and an independent minimal issuer.

Owner decision accepted on 2026-11-22: accept **omission of this Windows WebKit profile from VB-156B's qualified
set**, while retaining the failed tests as failed required evidence for that excluded profile.
Do not label it qualified, turn assertions into skips/xfails, remove it from the retained failure
inventory, or claim server guards repair browser cookie delivery. No user-agent workaround or
runtime detection/blocking feature is authorized by this documentation amendment. Exclusion is a
support/qualification statement; it is not an implemented technical access restriction.

**Removal criteria:** record a native Windows Playwright WebKit build that demonstrably contains
the upstream correction (or an evidenced equivalent); run the unchanged
`test_native_cookie_scope_and_samesite` cases in `tests/e2e/test_named_cookie_delivery.py` for
production/control issuers at root/prefix. Require all four original cases to pass: raw issuance
remains Strict/Secure/HttpOnly/host-only, actual cross-site navigation and fetch omit the cookie,
different-host delivery is absent, and expected same-host other-port delivery remains demonstrated.
Run all ADR 0011 B session/hostname/guard regressions on that exact build, preserve receiving-server
headers, native cookie-jar evidence and traces, obtain separate review, and explicitly record owner
approval of the matrix extension. An upstream merge, cookie metadata alone, Linux pass, Safari claim
or passing guard test cannot remove this exclusion. Recheck upstream/build status at that time.

## Exception 2: unresolved Chromium bootstrap availability behavior

The original node was
`test_orphan_mutation_quarantined[root-https-chromium-close-DELETE]`. Login could not fill the
hidden form while the page still displayed `Checking browser support`. It failed before DELETE
barrier setup, dispatch or tab closure. The server recorded other modules but no `spaces.js`
request; browser-side dispatch/evaluation/error and native trace evidence for that context is absent.
There is no established causal fix. Thirty exact-node reruns, 300 contexts, 200 fresh startups and
the later 148-case suite pass do not explain or supersede the original failure.

Classify this as **unresolved availability behavior**: bootstrap may remain unavailable before
unlock, with unknown cause/frequency. The observed schedule does not establish protected-state
disclosure, but absence of a proven security defect does not prove all future occurrences safe.
Do not classify it as fixed or dismiss it as flakiness.

Owner decision accepted on 2026-11-22: explicitly accept the possibility of an unexplained pre-unlock stall
for the listed Chromium profiles, and permit VB-156B completion without a causal repair **only**
while all security gates and the deterministic regressions pass and the original failure remains
open in the evidence/risk record. This accepts availability risk, not authorization bypass,
unbounded recovery, a frequency estimate or automatic runtime retries.

Retain `test_bootstrap_module_dependency_barrier` (hold actual `spaces.js`; no session dispatch/
journal/lock activity before release) and `test_bootstrap_native_lock_barrier` (real lock contention,
distinct `Checking browser session` state, no dispatch until release). Run root/prefix cases;
retain native module request/response/completion/failure, page-error and lock/journal diagnostics
and failure traces. These isolate symptom boundaries; they do not reproduce the missing-request
cause. No arbitrary retry, sleep, timeout increase, assertion removal or cache-header change.

**Reopen/suspend acceptance on:** any recurrence of an unexplained bootstrap stall in qualification,
CI or use of a listed profile; either deterministic regression failing; or new module/network/error,
startup/teardown or lock evidence that changes the causal boundary. Preserve the first failure
before diagnostic reruns, investigate the exact node/order, and obtain a fresh reviewed resolution
or explicit revised owner decision before claiming completion/releasing affected source. Evidence
of protected work before validation, disclosure or cookie/lifecycle failure is a security failure
and cannot be covered by this availability exception.

**Removal criteria:** establish an evidence-supported cause and bounded correction, reproduce the
original failure or a causally equivalent schedule before correction, demonstrate the correction
with a regression and affected profile suites, preserve original evidence, and obtain separate
review plus owner closure. Repeated success alone never retires this risk.

## Exception 3: evidence where authenticated BFCache admission is unavailable

Native Back/Forward history restoration is not BFCache admission. A `back_forward` navigation
type, protocol freeze command, synthetic persisted event or ordinary successful restore is not
proof of a cached authenticated document. Actual admission requires native `pageshow.persisted=true`
on return to the same authenticated document, correlated with departure/document identity evidence.

The retained native history tests demonstrate pagehide erasure of protected DOM, selection,
status and key input; clean Back/Forward return stays empty while real nonissuing GET headers are
held, then performs fresh discovery/default selection without replaying `research`. Cookie/expiry
and clean journal do not change. If another tab logs out while the page is away, return stays
locked/empty, with no session GET adopting the removed cookie. Recorded authenticated returns have
`pageshow.persisted=false`. These are demonstrated **native non-BFCache restoration** paths.

Version-matched Playwright Firefox configuration/Juggler and WebKit automation patches disable
BFCache in the tested environments, as preserved in the implementation evidence. Independent
nonempty cacheable/no-store public controls help distinguish harness restrictions; they do not
alter production headers. Full Chromium admits a cacheable public control, and a separate public
no-store Chromium feature test observes persisted admission/key erasure. Neither proves
authenticated admission. The native freeze/resume diagnostic failed to observe suspension on both
platforms; its unsuccessful assertions remain evidence, not passing suspension qualification.

Owner decision accepted on 2026-11-22: for the exact tested profiles where authenticated admission is unavailable,
accept the following **combined alternative evidence** for B only:

1. Native authenticated navigation away/Back/Forward at root/prefix, recording actual persisted flags
   and post-pagehide protected DOM/controller-state erasure, not merely in-page history changes.
2. Held validation-response headers proving blank state until successful nonrenewing validation and
   fresh discovery; unchanged cookie/expiry/journal and no Set-Cookie on GET. Logout-away must remain
   locked without silent cookie adoption, stale selection/status or automatic protected replay.
3. Existing deterministic pagehide/pageshow/hidden/focus, stale-response, delayed-notification and
   shared-journal checks, including denied restoration after logout/uncertainty. Injected events
   exercise controller branches and must be labelled as such, never native admission evidence.
4. Native public controls and version-matched harness/configuration evidence for unavailable
   admission, plus traces/events/commands/build identities and preserved failed suspension probes.

All four evidence classes are required; ordinary history success alone is insufficient. Keep
`test_native_authenticated_history_qualification` and the public/control/lifecycle regressions.
This changes the evidence gate, not the privacy behavior required on a future actual BFCache return.
**Remaining limitation:** no authenticated persisted admission or logout of an actually
BFCache-suspended authenticated page has been demonstrated on any tested engine. No Safari/macOS/iOS
or uninstrumented browser qualification is claimed; no universal BFCache privacy claim is permitted.

**Reopen on:** browser/harness/cache-policy changes making authenticated admission possible, new
native persisted/suspension evidence, or any erasure/revalidation regression. **Removal criteria:**
on each affected profile, with unchanged production privacy/cache headers and security controls,
observe authenticated native persisted admission and logout while actually suspended; prove erasure
before suspension, blanking before any restored protected rendering/fetch, fresh validation/discovery
for clean return, and locked return after logout/rotation/uncertainty. Retain native event/document,
cookie/journal, receiving-header and trace evidence; obtain separate review and record owner closure
for that profile. A pass on one profile cannot retire another profile's evidence limitation.

## Non-negotiable invariants

ADR 0011's entire runtime contract remains mandatory, including:

- Server-side exact Origin and Fetch Metadata checks, exact CSRF marker, JSON-only unlock, no CORS
  relaxation; exact cookie-route allowlist, Bearer precedence, peer limiting and policy-before-work.
- Secure on HTTPS, HttpOnly, SameSite=Strict, host-only/no Domain, Path=/ and matching deletion
  attributes. Existing legacy/local HTTP behavior is unchanged; no remote non-Secure session waiver.
- Dedicated VaultBridge hostname, trusted listeners/proxy routes across **all ports**, one canonical
  HTTPS session issuer, trusted proxy handling and drained legacy issuers. Cookies do not isolate
  TCP ports; Origin guards cannot prevent direct cookie delivery to another matching HTTP server.
- Validation-only named GET with no Set-Cookie/renewal; fixed expiry/current-key rules; coordinated
  POST/DELETE, native Web Locks, two bounded nonsecret records, and fail-closed abandoned-mutation
  quarantine. No per-tab/Bearer fallback or timer-based uncertainty reset; logout is not revocation.
- Protected-state erasure on lifecycle/authentication transitions, blank-before-revalidation, fresh
  discovery, memory-only protected state and no automatic protected query/selection replay.
  Preserve no-store response/fetch behavior, CSP and all privacy headers; never weaken them to force
  BFCache admission or bootstrap success.
- Scope isolation/containment, zero-probe/read-only status, qualified identities, default-only omission,
  unknown=denied, whole mixed-selection denial and unchanged legacy REST/MCP/CLI contracts.

No application/test, cookie/session, dependency, Docker/deployment or release metadata change is
authorized. In particular, documenting an excluded build cannot make its failing cookie behavior safe.

## Owner decisions, completion and release gates

All three decisions below were explicitly **ACCEPTED** by the project owner on 2026-11-22 in
"Final Owner Acceptance — ADR 0012". The owner accepted the Windows WebKit exclusion, unresolved
historical Chromium availability risk without a causal-fix claim, and demonstrated safe non-BFCache
history restoration as the current evidence while unobserved authenticated BFCache/suspension
remains unqualified. The review task and unchanged evidence bundle are identified above.
Preparation, packet generation or implementation APPROVE alone did not supply this acceptance.

| Decision | Exact acceptance requested | Current state |
| --- | --- | --- |
| W | Accept the five-profile qualified set and exclude Windows Playwright WebKit until exception 1's removal criteria pass; no Safari or untested-profile inference | ACCEPTED — 2026-11-22 |
| C | Accept the unresolved Chromium pre-unlock availability risk without claiming a fix, subject to retained barriers, first-failure preservation and recurrence/new-evidence reopening | ACCEPTED — 2026-11-22 |
| H | Accept the combined alternative history/lifecycle evidence for unavailable authenticated BFCache admission on the listed profiles, retaining the explicit unexercised-suspension limitation | ACCEPTED — 2026-11-22 |

The independent amendment review and explicit owner acceptance establish this ADR as a supplement
to ADR 0011. Its original requirements continue to govern outside these three bounded exceptions.
ADR 0011 remains accepted history and its technical content is unchanged.

**VB-156B completion would require all of:** the implementation APPROVE tied to the final source;
separate amendment review APPROVE; explicit owner acceptance of W/C/H and the amendment; durable
evidence mapping; all other ADR 0011 B requirements and selector-required Windows/actual WSL/Linux,
legacy, security and compatibility checks satisfied for that source. Missing required evidence
outside these three exceptions still blocks completion. Record failures/exclusions separately from
passing profiles. C/D/E are not implemented or authorized by B acceptance; E must repeat its integrated
matrix and explicitly resolve or reauthorize any still-applicable exception for its own scope.
The amendment-review and owner-acceptance gates are satisfied on 2026-11-22; final VB-156B
implementation integration, final-source qualification and implementation acceptance are not
recorded as complete by this status update.

**Future release/deployment remains separate:** require explicit authorization, exact-source CI and
applicable release/image/operator evidence; carry the qualified matrix, exclusions and residual risks
into the release decision without advertising excluded profiles. Reassess exceptions against the
actual browser builds/source being delivered and reopen as specified above. Operator acceptance must
inventory all hostname ports/routes, canonical issuer, trusted TLS/proxy and legacy handover; laboratory
browser tests do not certify those real conditions. No new release or deployment is authorized or
certified here. A future owner must explicitly accept remaining risks for that release/deployment;
accepting this B-only amendment alone does not do so.

## Alternatives and consequences

Retaining every original evidence gate without amendment was an alternative considered by the owner.
The owner selected this bounded amendment. Claiming universal qualification, treating reruns as a fix,
weakening Strict/guards/no-store, or forcing BFCache by privacy-header changes is rejected. The
accepted alternative permits a bounded B acceptance decision with transparent evidence gaps while
retaining all runtime invariants, failed evidence and explicit reopening/removal conditions.
