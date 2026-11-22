# VB-156B final-source qualification evidence

This bundle repairs the confirmed **evidence durability** finding. It publishes selected,
sanitized underlying records for the independently verified, uncommitted implementation
over base `8dd6f2a421f017dc954d6cb036e42b9b583bdd95`. It does not invent an implementation
commit, run an independent review, or authorize release/deployment. **Implementation
acceptance is PENDING.** ADRs 0011 and 0012 remain ACCEPTED; VB-156C/D/E are outside scope.

The raw executable/check-input fingerprint is:

`cf8961d2a813117393a08aa7159c602548e5fdfbe4538ea1ce0a649eb801a32e`

It was independently recomputed before selecting evidence and matches the original
Windows browser, Linux browser, Windows completion and Linux Python source manifests.
All recorded final runs below used these same 251 input files. No production or test
file was changed by this durability repair. Original failures and recovery backups
remain preserved; this directory does not replace or rewrite the 150 accepted historical
artifacts in [the separate ADR 0012 record](../vb-156b/README.md).

## Reproduce the integrity and source checks

From a repository checkout, with Python 3.12 or later:

```text
python docs/evidence/vb-156b-final/verify.py --self-test
```

This standard-library, read-only verifier checks the manifest, every artifact hash,
references, privacy patterns, source input set/bytes, source aggregate, executed counts,
browser revisions, transport chronologies, history flags and layout measurements.
It does not run the application or regenerate evidence. `.gitattributes` disables
checkout conversion only for this hash-indexed directory, preserving artifact bytes.

The original canonical procedure is retained in [source-procedure.py.txt](source-procedure.py.txt).
[source-fingerprint.json](source-fingerprint.json) records the exact ordered path set,
per-file raw SHA-256, original byte length, LF-content hash and compact line-ending map.
The aggregate is SHA-256 of the UTF-8 JSON path-to-raw-SHA-256 map, with sorted keys,
compact comma/colon separators, default `ensure_ascii=True` and no trailing newline.
It includes all files under `app`, `tests`, `scripts` except `__pycache__`/`.pyc`/`.pyo`,
plus the five explicitly listed root check inputs. Documentation is outside that set.

After the implementation is committed, a clone may materialize text with different
Git-controlled line endings. Run strict verification first. If it differs **only** for
that reason, use:

```text
python docs/evidence/vb-156b-final/verify.py --git-normalized --self-test
```

That explicit mode first requires each recorded LF-content hash, then reconstructs
only the original recorded CRLF/LF positions in memory and verifies the original raw
hash/aggregate. Binary inputs remain exact. Any other content/input-set change fails;
this is not qualification for changed source. The original tracked/untracked inventory
is historical metadata from before the evidence addition, not a claim that a commit
already existed. Its documentation rows are outside the qualified code fingerprint.
This mode proves content-to-recorded-byte provenance for review; it does not claim
that the physical LF checkout was executed. A future runtime rerun/delivery must use
the exact recorded bytes or establish a new strict source identity and qualification,
particularly for fixtures whose raw line endings can affect behavior. The verifier
never rewrites implementation files to make a fingerprint pass.

## Executions and exact outcomes

| Record | Independently inspectable content |
| --- | --- |
| [Execution matrix](execution-matrix.json), [validation results](validation-results.json) | Original commands/UTC start/end/exit/source identities; private executable/root aliases are documented below |
| [Windows browser source](source-records/windows-browsers.json), [Linux browser source](source-records/linux-browsers.json), [Windows completion source](source-records/windows-finish.json), [Linux Python source](source-records/linux-python.json), [focused source](source-records/linux-focused-final.json) | The actual complete per-execution file-hash maps; the verifier compares all five maps directly to current source |
| [Windows JUnit](validation/windows-browsers.json), [Linux JUnit](validation/linux-browsers.json) | All 501 / 749 testcase identities, outcomes, timings and original native-observation properties; zero failures/skips |
| [Windows completion output](validation/windows-finish.log.txt) | Original canonical Python progress accounts for 4053 passed / 316 platform skips; all eight selected checks PASS, including Docker/MCP |
| [Linux Python JUnit](validation/linux-python.json), [original output](validation/linux-python.log.txt) | All 4369 identities: 4277 passed / 92 platform skips, reasons retained |
| [Windows builds](native-browser/windows-builds.json), [Linux builds](native-browser/linux-builds.json), [environment context](native-browser/environment-context.json) | SDK/Python and exact engine version/revision; Linux platform is measured in final native records, WSL is the owner/invocation identity; earlier kernel inventory is labelled separately |
| [Acceptance-to-test map](security/claim-test-map.json) | Exact executed 401, lifecycle, bootstrap, history, layout, socket, backend policy/security, API/OpenAPI, MCP and CLI test identities |

Windows used Playwright **1.63.0**, Chromium **153.0.8010.12 / r1243** and Firefox
**155.0 / r1543**. Native WSL/Linux used **1.64.0**, Chromium **156.0.8078.4 / r1248**,
Firefox **157.0 / r1555**, WebKit **27.2 / r2370**. The installed Windows WebKit entry
in its build catalog is not an executed/passing profile. It remains excluded.

The canonical Windows result is the original 2026-11-22 run against this fingerprint,
not a manually written replacement summary. Its quiet pytest progress lines count
passed/skipped nodes; the verifier independently recounts them. Linux and browser
counts are independently recomputed from every retained JUnit testcase. The complete
final matrices include 200/300 HTTP 401 cases, 16/24 TCP/truncated mutation cases,
4/6 complete-DELETE controls, and three deterministic socket regressions per platform.
Backend parameter IDs that embed fixture API credentials use distinct
[source-bound aliases](sanitization/credential-aliases.json). Each testcase retains its
original exact identity hash; the verifier reconstructs it from the pinned source in
memory and checks the hash without printing credentials. Function/class/route and
parameter variants remain distinguishable. Native 401/transport/history identities
are unchanged.

## Native security and settlement evidence

| Record | Meaning |
| --- | --- |
| [Windows native events](native-browser/windows-events.jsonl), [Linux native events](native-browser/linux-events.jsonl) | The 396/594 NativeEvidence records emitted by the final full runs, with exact node identities, sequence/timing, network/lifecycle observations, protected-state booleans and journal phases |
| [Windows mutation observations](security/windows-mutation-observations.json), [Linux mutation observations](security/linux-mutation-observations.json) | Original printed native fetch/body failures, complete-JSON absence, cookie presence and `uncertain` phase, joined to the next original node-labelled event record |
| [Windows TCP chronologies](security/windows-tcp-delete-chronology.json), [Linux TCP chronologies](security/linux-tcp-delete-chronology.json) | All ten actual native TCP/DELETE failures at root and `/proxy`; accepted sockets, failure/ACK/closure, forwarding and HTTP server execution/emission order |
| [Windows receiving observations](security/windows-cookie-delivery.json), [Linux receiving observations](security/linux-cookie-delivery.json) | Original production/control issuer observations: actual receiver cookie presence for all nine same-host/sibling/cross-site/HTTP cases; Strict metadata retained, values absent |
| [Native trace extracts](native-browser/trace-extracts.jsonl) | 40 final-source native history/public-control traces and six Linux focused-final complete-DELETE traces using the identical source; safe HAR metadata, session authenticated boolean where recorded, API timing and lifecycle observations |

The verifier independently requires one server DELETE execution per TCP case, native
failure and no later complete successful response, no forwarding after the terminal
failure transition, and actual disposal ACK before the held server response is emitted.
Server emission is not browser receipt. Existing journal assertions additionally
verify no session/API replay, hidden unlock/protected controls, and uncertainty across
reload/sibling tabs after explicit transport recovery. Pre-header DELETE can retain
the old cookie; partial responses can already change cookies. Neither grants recovery.
An aborted fetch is never interpreted as proof that DELETE did not execute.

Positive complete-DELETE controls are separate exact passed test identities; native
request-finished observations and the pinned test assertions establish complete JSON
settlement/locked journal/cookie removal. Six matching-source Linux focused traces
also retain the recorded nonsecret response boolean. No absent Windows raw trace or
body event is fabricated. Original final passes used `retain-on-failure`, so their
standard passing contexts intentionally have native event logs rather than raw traces.
The [focused-final JUnit](validation/linux-focused-final.json) and its separately labelled
execution-matrix row preserve the supplementary 42-case run/command/date; it is never
added to the 749-case full-matrix count.

To inspect a particular case without reading a large JSONL file:

```text
python docs/evidence/vb-156b-final/verify.py --show-case "tests/e2e/test_named_dashboard.py::test_native_mutation_transport_failure_quarantines[root-https-firefox-tcp-DELETE]"
```

The 401 variants include controlled response/body injection; actual native truncated
HTTP and TCP tests are distinct identities. Deterministic/injected lifecycle events
exercise controller branches and are never labelled native BFCache admission.
Backend Origin/Fetch Metadata/CSRF, cookie/Bearer precedence, zero-probe discovery,
selected-status read restrictions and legacy compatibility are exact passing test
identities in the retained full Python JUnit and pinned source, not new invented
request/response observations.

## ADR 0012 W/C/H and layout

[The alternative-evidence map](history/alternative-evidence-map.json) links all four
H classes and immutable historical supplements:

1. Native authenticated history observations retain actual `pageshow.persisted` values
   and post-pagehide erasure booleans: 16 Windows / 24 Linux snapshots, no authenticated
   persisted admission observed.
2. Held nonissuing validation, blanking, fresh clean restoration and locked logout-away
   behavior are in the passed pinned native history tests, JUnit properties and traces.
3. Deterministic lifecycle, stale-response, missing-notification and journal cases have
   exact passing identities and recorded native channels. Injected events are distinct.
4. Native public controls/traces are here; version-matched harness restrictions and
   unsuccessful suspension observations remain linked in the immutable prior bundle.

H does not qualify authenticated BFCache admission or logout of an actually suspended
authenticated document. C retains the unresolved original Chromium bootstrap availability
risk; current deterministic dependency/native-lock barriers do not establish a causal
fix. W retains Windows WebKit's exclusion and the four original Strict failures.
No Safari, uninstrumented/private/embedded or other-build support is inferred.
Historical intermediate source linkage limitations remain unchanged. Reopening/removal
conditions remain exactly those in [accepted ADR 0012](../../adr/0012-vb-156b-qualification-amendment.md).
The earlier bundle's PROPOSED/PENDING wording is its capture-time state; accepted
ADR 0012's dated owner decision governs the current W/C/H contract.

[Windows layout measurements](layout/windows-measurements.json) and
[Linux measurements](layout/linux-measurements.json) retain actual numeric observations:
all five profiles have inner/client/scroll width 320 at 320 px; at 375/1280 the fitted
heading dimensions/font equal the normal-wrapping control. Visibility, native keyboard
focus and named CSS isolation are passing pinned assertions. The only production
repair was `.app-header__identity h1 { overflow-wrap: anywhere; }`.

## Failures, review boundary and integrity

[Original Windows integration failures](reviews/windows-integration-failures.json),
[Linux failures](reviews/linux-integration-failures.json), the
[pre-repair proxy](reviews/pre-repair-proxy.py.txt) and
[deterministic failing CONNECT reproduction](reviews/windows-red-pending.log.txt)
remain inspectable. Pre-fix layout and unsuccessful wakeup/focus diagnostic records
are also indexed, with their actual intermediate source identities. They are not
passes or executions of the repaired fingerprint. The original bootstrap/Strict/
freeze failures remain in the separate immutable historical record.

[Owner-reported durability finding](reviews/durability-finding.md) is explicitly not
a new APPROVE verdict; no full review transcript was fabricated. See
[sanitization and omissions](SANITIZATION.md) for extraction limits.
[manifest.json](manifest.json) hashes every payload, including this index and verifier;
[manifest.sha256](manifest.sha256) separately hashes the manifest. Neither manifest
hashes itself nor its checksum, avoiding recursive hashes. Original raw hashes and
relative evidence identities remain provenance, not inaccessible evidence links.

These are repository additions for review, not a claim that they were already committed.
PR authors must include every indexed file, the manifest/checksum and the scoped
`.gitattributes` rule. Independent review/owner acceptance and any exact-source CI,
release/image/operator/deployment gates remain separate and unauthorized here.
