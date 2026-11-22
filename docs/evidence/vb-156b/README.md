# VB-156B durable qualification evidence

**Evidence status: INCOMPLETE for independent acceptance.** Available records are included as
sanitized repository additions, but exact historical source linkage for original/intermediate runs
is unverified. No exception is accepted. ADR 0012 remains PROPOSED; ADR 0011 remains ACCEPTED;
VB-156B qualification, release and deployment remain pending. No tests were rerun to create this
bundle. Historical failures are retained rather than replaced by later passing output.

## Read the evidence

| Record | Purpose |
| --- | --- |
| [Provenance manifest](provenance.json) | Per-artifact identity, original-byte and sanitized SHA-256, original relative path under a private root alias, commands, dates, result and source/review relationship |
| [Builds](builds.json), [recorded commands](commands.json), [all JUnit runs](results.json) | Browser/SDK/revision identity, exact available commands with private roots replaced, and every retained test outcome including diagnostic failures |
| [Source recovery](source-recovery.json), [source snapshots](source-snapshots.json), [partial continuation hashes](partial-continuation-hashes.json) | What was recovered, what hashes establish, and what cannot be attributed to a complete historical source tree |
| [Captured source changes](source/implementation-changes.txt) | Full tracked diff and all untracked source content relative to the recorded base, sanitized and explicitly uncommitted |
| [Pre-401 implementation review](reviews/implementation-before-401.md), [repair review](reviews/401-repair.md), [review verification](reviews/401-verification.json) | Original FIXES REQUIRED, subsequent repair APPROVE, and the reviewer's actual recorded hash checks / 21 in-memory cases |
| [Amendment HIGH finding](reviews/amendment-finding.md) | Full finding that caused this evidence repair; FIXES REQUIRED, not a verdict on the repair |
| [Original qualification report](qualification-report.md), [401 repair report](source/401-repair--repair-evidence.md.txt) | Dated implementation narratives, including superseded outstanding-review wording and all residual limitations |
| [Sanitization and extraction boundaries](SANITIZATION.md) | Exactly what is retained, redacted or omitted and why raw traces are not shipped |
| [Original amendment task](tasks/original-amendment.md), [implementation preflight](tasks/implementation-preflight.md) | Original task authority and clean design-baseline preflight; neither is an implementation snapshot or acceptance |

All durable paths in `provenance.json` are relative to this directory. Original `VB156B_SOURCE/`
and `CODEX_SESSION/` names are provenance aliases only; their content is included here where required
for verification. An external worktree, local session store or ignored directory is not required to
read the bundle. This change adds repository files for review; it does not claim they were committed.

## Source states and review linkage

The source worktree base is `90886af56f911b7708c9749bf63ab2f17816408b`, the accepted design baseline.
Read-only Git history, branch/HEAD reflogs, stash inspection and relevant path history found no
committed/stashed implementation snapshot. The task packet records a clean preflight only. Raw
WebKit traces contain network/stacks, not historical source text. No missing source was synthesized.

| Source state | Verified boundary | Limitation |
| --- | --- | --- |
| Partial continuation manifest | Original changed-file hashes preserved without invented capture time | Not a complete tree or proof of which exact run used those bytes |
| Pre-401 production map | Retained 100-file production map; repair reviewer checked it and found only `scoped-client.js` changed | A production-map fingerprint, not a complete test/fixture tree fingerprint for each historical execution |
| Final 401 repair | Original prior client hash matches the production map; final client, E2E test and fixture hashes match the review-time scope record; reviewer independently verified those matches | Full capture fingerprint was created during this repair, not contemporaneously with the tests or review |
| Original/intermediate execution revisions | Exact available test names, commands, timestamps, JUnit diagnostics and trace member identities are preserved separately | Complete contemporaneous source snapshot/fingerprint not recoverable; source linkage UNVERIFIED |

The current uncommitted capture fingerprint is
`3328142225fbd009c4740e8038186fb2a188e70e0f49e8754925d66ea49b5f30`.
Its algorithm and raw changed-file map are in `source-snapshots.json`. It is not an implementation
commit. Sanitized source files have separate content hashes; substituting placeholders changes those
bytes. The full historical review verdicts are preserved, with local source links retargeted to this
capture and their original line labels retained as historical labels, not current line anchors.

The repair APPROVE applies to the HTTP 401 correction and integration. The earlier review inspected
the implementation and found the 401 defect; neither review establishes complete qualification.
Do not silently attribute pre-401 suites, intermediate fixture attempts, Strict failures or the
bootstrap failure to the captured final tree. Unverified historical source linkage is an unresolved
**blocker for independent acceptance**, not an additional exception authorized by the owner.
The included final scoped client and E2E test content also match their review-time raw hashes
byte-for-byte. The fixture has credential literals redacted, so its sanitized hash differs; the
original fixture hash was checked by the reviewer and is preserved separately in the scope map.

## Windows WebKit: four failures remain failures

Read the [complete sanitized failure log](logs/vb156b-windows-webkit-control.log.txt),
[all four JUnit cases](junit/vb156b-windows-webkit-control.xml.json),
[receiving-server observations](webkit-receiver-observations.json) and
[test source snapshot](source/tests--e2e--test_named_cookie_delivery.py.txt).
The four native trace network extracts are listed in `provenance.json` under `traces/webkit-*`.
All network rows are included, including passing scenarios; no cookie values or bodies are included.

The recorded command at 2026-11-21T08:27:51.538Z launched the unchanged WebKit-only test at root
and `/proxy`, production and independent control issuers, with native tracing/JUnit. The resulting
JUnit starts at 09:27:53+01:00 and records **4 failures, 0 skips**. Earlier invocations are separately
dated in `commands.json`; they are not confused with the retained last failure output.

Windows SDK 1.63.0 / WebKit 26.6 revision 2359 issued Strict/Secure/HttpOnly/host-only cookies but
stored SameSite=None. In every case, genuinely cross-site navigation and fetch were expected to
omit the cookie and actually delivered it. The test prints boolean observations from the receiving
server's actual Cookie header; native network extracts corroborate Cookie presence with
`Sec-Fetch-Site: cross-site`. The other seven scenarios are retained, including expected cookie
delivery to another HTTPS port on the same hostname, different-host omission and Secure-over-HTTP
omission. Server guards cannot repair browser-to-receiver cookie delivery. Windows WebKit is
**NOT QUALIFIED**; Linux WebKit is a different native build, and neither is a Safari test.

Passing server-side guard evidence is separately visible in
[110-case backend results](junit/remaining-qualification--backend.xml.json),
[backend log](logs/remaining-qualification--backend.log.txt),
[guard/session test source](source/tests--test_named_dashboard.py.txt) and the final canonical run.
These retain rejection of wrong/null/malformed/duplicate Origin/marker, non-same-origin Fetch
Metadata, invalid media type and forbidden cookie routes before protected work/issuance. Passing
guards are not reported as a passing Strict-cookie qualification.

## Chromium bootstrap: original failure and later successes

The [original canonical log](logs/vb156b-finish-bootstrap-failure.log.txt) records
`test_orphan_mutation_quarantined[root-https-chromium-close-DELETE]` failing during `login(a)`
before DELETE barrier setup. The hidden input could not be filled; the page stayed at
`Checking browser support`. Captured HTTP completions include shell/CSS/logo and
`named-app.js`, `named-session.js`, `scoped-client.js`, but no `spaces.js` request.
This is absence in the retained server observations, not proof of browser dispatch/evaluation.

The original finish launch and its selected command are retained in `commands.json` and the log:
`python -m pytest -q tests/e2e --browser=chromium --tracing=retain-on-failure --output=test-results/playwright`.
It records **147 passes / 1 failure**, workflow exit 1, and no generated review packet.
The original browser context bypassed the recorder; its native trace/page errors were not recorded
and are **genuinely unavailable**. This absence cannot be repaired by a new trace of current code.

All subsequent diagnostics remain separate:

- [30 exact-node reruns](logs/vb156b-bootstrap-repro.log.txt): each passed; not a causal repair.
- [300 contexts on one server](logs/vb156b-bootstrap-native.log.txt) and
  [200 fresh startups](logs/vb156b-bootstrap-fresh.log.txt): bootstrap-readiness diagnostics, not
  repetitions of the full orphan-DELETE scenario. Their recorded scripts include a diagnostic
  30000 ms observation deadline; that is historical diagnostic scope, not a production/test-timeout change.
- [148-case Chromium rerun](logs/vb156b-bootstrap-suite.log.txt): subsequent pass, not explanation.
- [Deterministic barrier source](source/tests--e2e--test_named_qualification.py.txt),
  [12 Windows barrier results](junit/remaining-qualification--windows-bootstrap.xml.json),
  [complete barrier log](logs/remaining-qualification--windows-bootstrap.log.txt) and
  [native events](native-events.json): actual module dependency held before construction versus
  native Web Lock contention after construction. Readiness messages, lock/journal state and zero
  session dispatch are retained. Supplementary bootstrap documents lack an exact per-run source
  manifest and are labelled accordingly; they are not silently assigned to final source.

**No causal explanation or fix is established.** Original source linkage remains unverified;
availability-risk acceptance is still PENDING. No arbitrary runtime retries or weakened assertions
were introduced by this evidence export.

## Authenticated BFCache versus native history

Read [native test source](source/tests--e2e--test_named_qualification.py.txt),
[actual lifecycle observations](history-observations.json), [event chronology](native-events.json),
[Windows JUnit](junit/remaining-qualification--windows-history-traced.xml.json) and
[Linux JUnit](junit/remaining-qualification--linux-history-traced.xml.json).
Each platform's traced history/control run records **24 passes**. Its 24 event documents and native
network trace extracts were matched by trace wall-time window **and exact JUnit snapshot content**;
coincident duplicate diagnostic runs were not falsely associated with these JUnit results.
The [retained command note](source/remaining-qualification--diagnostic-command-note.md.txt) also
preserves a preliminary Linux invocation whose nested quoting selected only a narrower Chromium
history run. Its partial result is not substituted for the final three-engine traced results.

Authenticated clean and logout-away paths at root/prefix report **pageshow.persisted=false** on
every tested engine. Pagehide observations report protected DOM hidden, selection/status/key empty.
The clean-return test holds real validation GET headers while those states remain empty, then
requires fresh discovery/default selection, unchanged cookie/expiry/journal and no Set-Cookie.
Logout-away requires cookie removal, locked/empty Back/Forward return and no adopting GET. The
corresponding source assertions and unchanged native outcomes are included, not inferred from a
`back_forward` navigation type alone.

Public cacheable Chromium controls record `[false,true]`; no-store controls and all Firefox/WebKit
controls refuse admission in these harnesses. A separate public no-store Chromium feature test is
retained in the historical report/source; it proves no authenticated admission. Version-matched
automation restrictions are included under `upstream/`, alongside failed observer/freeze attempts
in `results.json` and complete diagnostic logs. Both platforms' native freeze/resume probes recorded
**2 failures**, not a passing suspension test. The earlier Windows console-observer run has
**4 failures among 12 cases**; those are retained rather than hidden by later observers.

**Authenticated admission and logout while actually BFCache-suspended remain unexercised on all
engines.** No Safari/macOS/iOS, uninstrumented-browser or universal BFCache privacy claim follows.
No cache/privacy header was changed to obtain these results.

## Security and HTTP 401 repair

The source capture includes server guards/cookie handling and the actual named session coordinator,
Spaces controller, scoped client, native fixtures and assertions. Prior and final 401 clients and
the repair patch are separately included. Cookie/Origin/Fetch Metadata rules, nonrenewing GET,
cross-tab Web Lock/journal ordering, abandonment quarantine, privacy erasure and revalidation remain
mandatory. These are source/evidence records, not changes to application or test files.

[Original red result](junit/401-repair--red.xml.json) records **1 failure** before correction.
[Final Windows](junit/401-repair--windows-complete.xml.json) and
[Linux](junit/401-repair--linux-complete.xml.json) results record **300 passes each, 0 skips**, across
Chromium/Firefox/WebKit and root/prefix for the focused 401/status selection. They cover five body
forms, real truncation, erasure before asynchronous deletion, same/cross-tab reauthentication,
stale responses, concurrent logout, failed DELETE and non-401 retention. These focused passes do
not rerun Windows WebKit's failed Strict tests.

All intermediate JUnit failures are in `results.json`, including CSP observer/route-completion and
streaming experiments; partial/cancelled final attempts are in complete logs. The
[final canonical workflow](logs/401-repair--agent-finish.log.txt) records the exact original
implementation task and all selected checks passing. That remains source-check evidence, not a
waiver of the three qualification gaps or the historical provenance blocker.

## Upstream snapshots

[Playwright #44141](upstream/playwright-44141.json) and
[WebKit #81431](upstream/webkit-81431.json) retain the dated API content and original hashes.
Upstream issue closure is not an installed corrected Windows binary. Version-matched Firefox
configuration/content and WebKit automation patch excerpts retain the harness BFCache restriction
with source URLs and context. These historical captures do not claim current upstream state.

## Acceptance boundary

This directory removes dependence on local paths for the content that was available. It does not
invent missing provenance. Exact source attribution of original/intermediate runs and the original
bootstrap browser-side recording remain unresolved as described in `source-recovery.json`.
Material provenance gaps remain blockers for independent acceptance. W/C/H decisions, final-source
qualification requirements and separate release/deployment gates are unchanged. No commit, PR,
release, deployment or independent review of this evidence repair was performed.

## Verify included content without the source worktree

From the repository root, the following standard-library check verifies every indexed sanitized
artifact hash. `provenance.json` is the index and does not recursively hash itself. This is content
integrity verification, not a new qualification run or proof of missing historical source linkage.

```text
python -c "import hashlib,json,pathlib; p=pathlib.Path('docs/evidence/vb-156b'); m=json.loads((p/'provenance.json').read_text()); assert all(hashlib.sha256((p/r['durable_relative_path']).read_bytes()).hexdigest()==r['sha256'] for r in m['records']); print('Evidence hashes match')"
```
