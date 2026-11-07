# VB-152 Slice A implementation and verification

Slice A implements policy-bound existing reads privately from approved contract merge
`3653b1644ad9041b6c073370fd761bf0116edf82`. Production named serving remains guarded.
The first independent acceptance returned three findings. A later focused review reproduced one
HIGH raw-input fail-open finding, followed by qualified-resource selection loss. The latest
review identified the remaining Windows qualified-subfolder URI mismatch.
The repairs and separate verification records follow below;
fresh acceptance of the latest repair is pending. This session performs no independent review.

## Implementation handoff

1. **Changed files:** the exact source/test/document list is recorded below.
2. **Domain compatibility:** `SpaceOperations.compatibility_read` authorizes default before
   local-owner access and admits once without federation bounds. Legacy related verification,
   title/semantic duplicate advice, hygiene errors and query output remain on their old branch.
   Explicit scopes reuse the existing qualified operations, complete authorization, quotas and
   availability/discard rules. Mounted legacy MCP HTTP shares the parent's exact operations.
3. **REST:** existing read/list/search/related/duplicates/links/backlinks/hygiene paths, aliases,
   operation IDs, Bearer rotation, limiter and omitted serializers remain. Explicit requests use
   strict singular/plural validation, safe error projection and allowlisted OneSpace/ScopedRead.
   OpenAPI describes both branches without adding Query or scoped writes.
4. **MCP:** existing read tools share validation/projection on both transports. Both legacy and
   qualified resource templates are registered. Qualified paths use one UTF-8 encoded component,
   uppercase escapes, one decode, owner containment and canonical re-encoding equality. Explicit
   default also uses a qualified URI; a URI conveys no grant. Resource failures are uniform.
   Scoped duplicate JSON keys are rejected before SDK coercion; legacy protocol input is unchanged.
5. **CLI persisted reads:** explicit search/related/query/hygiene use the existing federation and
   a complete registry with one admission gate and one lazy shared model. Profile preflight checks
   the complete selection before owner construction. Immutable bound SQLite reads reject missing,
   incompatible, redirected or sidecar-bearing state. No startup synchronization, initialization,
   watcher, worker, model download or cache repair occurs. Existing model artifacts are selected
   explicitly before invoking the backend. Bare query paths require one effective selected space.
6. **Activation fences:** every production guard remains. Private tests inject exact operations;
   raw-owner/legacy-registry injection is rejected. Named health is minimal, readiness authorizes
   default before probes, UI/session/assets are 404/no-store including proxy prefixes, cookies do
   not authenticate named REST, and named maintenance is fenced before owner work.
7. **Legacy evidence:** existing adapter/security/health/UI suites plus frozen semantic/v1 evidence
   are rerun. Existing MCP schema assertions now inspect the unchanged legacy alternative and
   expect the required second resource template; no behavior assertion or skip was removed.
8. **Platforms:** native Windows Python and actual WSL/Linux Python use real temporary filesystems,
   SQLite, junctions/symlinks, admission and retained owners. Counts and platform skips follow below.
9. **Semantic/v1 parity:** all 13 cases and legacy capture/create/append bytes/digests are compared
   to the accepted Git reference and across platforms. The reference content is unchanged.
   `.gitattributes` pins this one JSON fixture to LF so Windows checkout conversion cannot alter
   the accepted evidence bytes. Reference SHA-256 remains
   `9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de`.
10. **Accepted limits:** cooperating filesystem writers and stopped-service CLI operation remain
    required. No cross-process locking, atomic multi-root snapshot, hardlink/hostile-writer isolation,
    freshness or wall-clock latency claim is added. ADR 0010 can retain ambiguous cleanup and keep
    the same runtime stopping. Missing model/index state fails unavailable. One-space duplicate
    advice preserves the existing owner unavailable outcome when no exact-title fallback exists,
    without semantic search. External proxy/access logs may record GET path/scope parameters;
    application middleware does not control those logs. The strict stdio input
    iterator uses the installed SDK's standard-stream claim/restore helpers and is covered by real
    subprocess compatibility tests; dependency migration must retain that behavior.
    Slices B–D, named UI/discovery/maintenance, migration/rebind, scoped writes, new Query transports
    and structured CLI selectors remain excluded. No dependency, ADR, deployment or release change.
11. **Review packet:** generated only after all required verification and agent_finish succeed;
    separate fresh review must start from `.agent/review_packet.md` without implementer history.

## Final qualified-resource identity repair, 2026-11-07

**Confirmed finding and types.** Both scoped read URI generation (`execute_scoped`, read branch)
and the qualified resource handler's post-owner equality check used `QualifiedNoteRead.note.path`.
That field preserves the existing `NoteReadResult` platform-native payload and contains backslashes
for Windows subfolders. `SpaceOperations.read_note` separately returns
`QualifiedNoteRead.identity: QualifiedNoteIdentity`, established after verified owner execution by
`SpaceOperations._identity`. Its `canonical_relative_path` already uses forward-slash separators.
The qualified contract encodes that canonical identity as one component, including `%2F` separators.

Before production edits, all eight new Windows ASCII nested-resource regressions failed over
real authenticated Streamable HTTP and real stdio: named and explicit default, direct resource
read and generated-URI flow. Direct reads failed after owner execution; scoped tools generated
`folder%5Cnote.md` rather than `folder%2Fnote.md`. Evidence is `.agent/nested-uri-before.log` and
`.xml`; the original source/archive/manifest and packet are `.agent/nested-uri-original*` and
`.agent/nested-uri-prior-review_packet.md`. The source finding is runtime-reproduced.

**Correction.** Both URI consumers now pass `identity.canonical_relative_path` to the existing
encoder. The resource handler compares the URI reconstructed from the verified post-owner identity
with the original requested URI. The read-tool payload still contains the original `note.path`;
no owner path format, public schema, encoder, parser, legacy URI template, Windows branch or
additional string replacement is introduced. All other inherited production files remain unchanged,
including the previous raw-input rejection/cancellation/availability repairs and activation guards.
This fixes the separate Windows subfolder limitation recorded in the historical 2026-11-06 section.

**Focused regression scope.** New `tests/test_mcp_resource_identity.py` has 38 cases on both native
platforms. Successful real HTTP and stdio tests cover named and explicit default nested resources,
scoped tool URI generation and readback, exact post-owner identity types, unchanged platform-native
payload paths, and existing legacy default nested resources. ASCII and nested Unicode/punctuation
paths use identical literal expected URI bytes on both platforms, including uppercase `%2F`,
`%C3%A9`, `%20`, `%23` and `%25`. Malformed/lowercase/unreserved/double-encoded/backslash aliases,
traversal into another existing root and absolute-root inputs fail with the static resource error;
denied and unknown selection performs authorization without admission or owner read. A URI grants
no access. Strict decoding, encoding and containment behavior are preserved rather than relaxed.

**Final verification.** Native Windows Python 3.12.10 and actual WSL/Linux Python 3.12.15
(kernel `6.6.87.2-microsoft-standard-WSL2`) executed the final source. All 184 Python files plus
the accepted reference fixture have identical bytes across platforms. Native Linux source uses
persistent `/home/<user>/.cache/vaultbridge-verification/vb152-a-nested-uri-20261107` storage;
fixtures use native Linux temporary filesystems, and logs/XML are exported before exit.

| Check | Windows passed / failed / skipped | Native Linux passed / failed / skipped |
| --- | --- | --- |
| Final nested-resource module, standalone and within full run | 38 / 0 / 0 | 38 / 0 / 0 |
| Related MCP/REST/policy-read modules, extracted from final full run | 436 / 0 / 1 | 437 / 0 / 0 |
| Previous raw-input security module, within full run | 161 / 0 / 0 | 161 / 0 / 0 |
| Previous cancellation/duplicate/root-availability repair module, within full run | 53 / 0 / 0 | 53 / 0 / 0 |
| Full non-E2E | 3,365 / 0 / 316 (786.47 s) | 3,589 / 0 / 92 (453.76 s) |
| Semantic evaluation suite | 29 / 0 / 0 | 29 / 0 / 0 |
| Chromium Dashboard E2E | 36 / 0 / 0 | Not separately run |
| Ruff and compileall, app/tests/scripts | Pass | Pass |

The initial combined focused processes collected before the final two `%5C`-alias cases were added:
Windows 434 / 0 / 1, Linux 435 / 0 / 0. The final standalone 38-case runs and both final full runs
include those cases; the related-module counts above are extracted from the final full JUnit files.
No assertion was weakened and no skip was added. Existing platform skip counts are unchanged.

All 13 semantic cases and v1 capture/create/append exports match the accepted reference and each
other byte-for-byte, retaining SHA-256
`9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de`.
All four rebuilt Docker/MCP checks pass: build, SDK import (2.3.0), clean stdio EOF and
read-only/write-enabled HTTP smoke with disposable cleanup. `git diff --check` passes.
There are no remaining failures or unavailable required checks in the executed matrix. The
historical Windows qualified-subfolder limitation is resolved. CI, deployment, release, public
named activation, independent review and whole-Slice-A acceptance are not claimed.

Runners are `.agent/nested_uri_verify.py`, `.agent/nested-uri-linux.sh` and
`.agent/nested_uri_docker_verify.py`. New tests run with
`python -m pytest -o addopts= -q tests/test_mcp_resource_identity.py`; focused execution also selects
`test_mcp_read_input_security.py`, `test_policy_read_repairs.py`, `test_policy_read_adapters.py`,
`test_mcp_server.py`, `test_mcp_http.py` and `test_mcp_knowledge_hygiene.py` under `tests/`.
Full execution uses `python -m pytest -o addopts= -q --ignore=tests/e2e -r s`; semantic export uses
`python -m tests.space_integration_evidence --output PATH` plus exact byte comparison.
Windows logs/XML/exports/exits use `.agent/nested-uri-*`; native artifacts are retained in
`.agent/nested-uri-linux/`. Counts are `.agent/nested-uri-results.json`, with source parity in
`.agent/nested-uri-source-parity.json`. The original eight failures are expected before-fix evidence.

The final completion command is
`python scripts/agent_finish.py --task-file .agent/vb152-a-task.md`. Its actual selected-check result
is retained in `.agent/nested-uri-finish.log`; `.agent/review_packet.md` is regenerated only after
success. This report and task evidence are finalized before that command. Stop at the regenerated
handoff without a commit, public activation, subagent or independent review launch.

## Shared raw resource-selection repair, 2026-11-06

**Boundary and executable reproduction.** The latest HIGH was source-supported when reported.
This session independently inspected the complete normalizer and confirmed execution. The first
object-pairs hook retained duplicate keys and scope-field evidence, but not qualified URI or
resource-method evidence. The later method/params/arguments checks consulted the already-collapsed
dictionary; `scoped=False` returned original bytes before strict validation. The SDK could then
replace a qualified selection with a legacy default URI. The second `strict_json` parse could only
reject duplicates on requests that passed that faulty gate. SDK JSON parsing, typed parameter
validation and URI lookup were downstream of it on both transports.

Before production edits, duplicate `uri` and duplicate `params` each returned default Markdown,
a legacy URI and authorization/admission/owner work over authenticated loopback HTTP and real
stdio: **4 failed, 0 passed, 0 skipped** (`.agent/resource-input-before.log` and `.xml`). A separate
run in the archived original source tree confirms named-URI and unknown-space params erasure on
both transports: another **4 failed, 0 passed, 0 skipped** in
`.agent/resource-input-selection-before.log` and `.xml`. The finding is therefore runtime-verified
in these local private profiles, rather than merely inferred from source. No external deployment
or production exploit claim is made.

**Correction.** The first parser now records scope keys, qualified `uri` values and resource-read
method evidence before *each* dictionary assignment, including members inside overwritten params
or arguments and overwritten methods. One shared decision rejects duplicate/non-finite envelopes
for tools and resources from that retained evidence. There is no second parse or final-dictionary
omission gate. Qualified resource identifiers use the existing canonical parser before SDK
coercion; old-template spelling validation remains in its existing owner. All valid original
messages, including explicit default, pass through byte-for-byte.

Rejection stays outside every original-input fallback. A fresh allowlisted envelope preserves
safe correlation and SDK version metadata. For resource framing, it retains only a UTF-8-safe
scalar URI for the SDK's required MCP-Name routing equality, never params/envelope/metadata objects.
The rejection marker is consumed before the SDK handler, resource lookup, policy, admission or
owner access and returns the existing static resource error. Unusable non-string URI routing
headers retain the SDK's static protocol error; both transports still reject without owner work.
If construction or serialization fails, the transport aborts without dispatching original bytes.
HTTP cancellation, security, authentication and size checks remain intact.

Production changes are limited to `app/mcp_read_input.py` and three lines in the existing
`app/mcp_server.py` rejection consumer. Tests extend `tests/test_mcp_read_input_security.py`;
this report and the ignored task file record evidence. The pre-repair source/archive/manifest
and previous packet are `.agent/resource-input-original*` and
`.agent/resource-input-prior-review_packet.md`. Every other inherited source/test file is unchanged.

**Regression scope.** The final raw-input security module has 161 cases: duplicate uri/params,
escaped keys, envelope and nested duplicates, overwritten resource methods, qualified selection
erasure, overflow/NaN/Infinity, invalid qualified URI spellings/types, irreversible rejection and
forced construction/serialization failures. Real HTTP and stdio tests assert zero policy,
admission, VaultService.read_note and scoped-session work for rejected requests. Valid legacy,
explicit default and named identities retain framing, content and URI. Unicode/percent spelling
is tested end-to-end with native-compatible filenames; canonical subfolder URI bytes are also
checked at the raw boundary. All earlier tool-envelope/non-finite protections remain covered.

**Separate inherited limitation (historical; resolved by the 2026-11-07 repair above).**
An exploratory valid qualified subfolder read on Windows fails
after owner execution: `read_space_note_resource` re-encodes `result.note.path`, whose legacy
Windows representation contains backslashes, instead of the canonical qualified identity path.
The archived pre-repair source reproduces the same failure, with the same static resource error
(`.agent/resource-input-nested-original.log` and `.xml`). It is unchanged and outside this single
raw-input finding. Flat Unicode resource reads and raw canonical subfolder identity preservation
are verified; successful Windows qualified subfolder resource execution is **not** claimed.
The exploratory fixture also initially used `?`, which is not a valid Windows filename; it was
corrected before final runs. No production workaround, weakened assertion or skip was introduced.
This report does not establish acceptance of the whole Slice A implementation.

**Final verification.** Native Windows Python 3.12.10 and actual WSL/Linux Python 3.12.15
(kernel `6.6.87.2-microsoft-standard-WSL2`) executed the final source. The retained Linux source is
under `/home/<user>/.cache/vaultbridge-verification/vb152-a-resource-input-20261106`; fixtures use
native Linux temporary filesystems. The first Linux execution passed but its temporary artifacts
expired after WSL exited, so the authoritative final run uses persistent source and exports logs
before exit. The final source manifest verifies identical bytes for all 183 Python files plus the
accepted reference fixture across platforms. No new skip was added.

| Check | Windows passed / failed / skipped | Native Linux passed / failed / skipped |
| --- | --- | --- |
| Final raw-input security suite | 161 / 0 / 0 | 161 / 0 / 0 |
| Slice A plus MCP server/HTTP/hygiene and REST policy-read regressions | 398 / 0 / 1 | 399 / 0 / 0 |
| Full non-E2E | 3,327 / 0 / 316 (808.40 s) | 3,551 / 0 / 92 (388.98 s) |
| Semantic evaluation suite | 29 / 0 / 0 | 29 / 0 / 0 |
| Chromium Dashboard E2E | 36 / 0 / 0 | Not separately run |
| Ruff and compileall, app/tests/scripts | Pass | Pass |

All 13 semantic cases and v1 capture/create/append exports match the accepted reference and each
other byte-for-byte, with SHA-256
`9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de`.
The Windows skip is the existing symlink-privilege case; full platform skip counts remain unchanged.
The rebuilt container passes all four required Docker/MCP checks: build, SDK import (2.3.0), clean
stdio EOF and read-only/write-enabled HTTP smoke with disposable cleanup. `git diff --check` passes.
There are no unavailable required checks. No CI, deployment, release, public named activation or
independent review is claimed; the separate inherited limitation above remains unresolved.

Reproducible runners are `.agent/resource_input_verify.py`, `.agent/resource-input-linux.sh` and
`.agent/resource_input_docker_verify.py`. Windows logs/XML and exits use `.agent/resource-input-*`;
retained native logs/XML/export are in `.agent/resource-input-linux/`. Machine-readable counts are
`.agent/resource-input-results.json`; source parity is `.agent/resource-input-source-parity.json`.
Focused execution uses `python -m pytest -o addopts= -q tests/test_mcp_read_input_security.py
tests/test_policy_read_repairs.py tests/test_policy_read_adapters.py tests/test_mcp_server.py
tests/test_mcp_http.py tests/test_mcp_knowledge_hygiene.py`. Full runs use
`python -m pytest -o addopts= -q --ignore=tests/e2e -r s`; semantic export uses
`python -m tests.space_integration_evidence --output PATH` followed by exact byte comparisons.
Intermediate exploratory failures are retained and are not final acceptance evidence.

The final completion command is
`python scripts/agent_finish.py --task-file .agent/vb152-a-task.md`. Its selected-check result is
retained in `.agent/resource-input-finish.log`; the current `.agent/review_packet.md` is generated
only after those checks pass. This report and task evidence are finalized before that command;
implementation stops at the regenerated handoff without launching independent review.

## One-finding security repair, 2026-11-05

**Scope.** Only `app/mcp_read_input.py` changes in production for this repair. New coverage is in
`tests/test_mcp_read_input_security.py`; the existing private stdio test process gains optional
audit/fault hooks installed after real persisted composition. This document and the ignored original
task file record evidence. Every other inherited implementation file, including domain authorization,
owners, admission, cancellation and runtime composition, is unchanged from the pre-fix manifest.
The original source/archive and previous packet are retained in `.agent/input-security-original*`
and `.agent/input-security-prior-packet.md`.

**Root cause and original executable evidence.** The first parser correctly observed duplicate
`arguments` and the explicit unknown selection. Its rejection branch then serialized the entire
untrusted envelope with `allow_nan=False`. An extra `1e400` became infinity in Python, while `NaN`
and `Infinity` also produced non-finite values. Serialization raised ValueError, which the enclosing
parse-error handler caught by returning the ORIGINAL bytes. The SDK discarded duplicate members
and executed the remaining omitted-scope default read. Before any production edit, all six
number/transport regressions failed: **0 passed, 6 failed, 0 skipped**. Authenticated HTTP and real
stdio both returned `isError=false`, default content and a legacy URI. Spies recorded authorization,
admission and `VaultService.read_note`. Evidence: `.agent/input-security-before.log` and `.xml`.

**Correction.** Rejection is now outside all parse-error raw-input fallback handlers. Its builder
creates a new deterministic JSON-RPC tool-error marker, copying no envelope objects, extension
fields or arbitrary metadata. Only a validated string/integer correlation ID is retained; invalid
IDs use synthetic ID 0 on this rejection path. Tool names and protocol versions are selected from
fixed read-tool/SDK version allowlists, and capabilities become an empty object. The first parser
also detects float overflow and non-standard numeric constants so either defect independently
rejects an explicit scoped request. If marker construction, serialization or encoding fails, a
fixed `InvalidReadRequest("Invalid MCP request.")` aborts before SDK dispatch, suppressing the
original exception context. There is no path from that failure back to the original request bytes.
Valid legacy/scoped inputs return their original bytes unchanged. Existing authentication, size
limits, SDK framing and all owner/runtime behavior remain in their existing layers.

**Regression evidence.** The 74 new cases cover the exact 1e400/NaN/Infinity exploit on both
transports; negative infinity/overflow; other envelope/params/argument/nested duplicates; malformed
IDs and non-finite extension/metadata fields; and non-finite scoped input without duplicates.
Authenticated loopback HTTP uses the real uvicorn/SDK transport and real bound owners. Real stdio
subprocesses use the persisted read-only composition with audit hooks. Every rejection asserts no
policy, admission, note-read or scoped-owner work, no default content, no successful resource/tool
result and no supplied private values in responses/stderr. Valid explicit/omitted tools, both
resource URI forms and ordinary tools/list retain framing, correlation and legacy/qualified shapes.
Raw-boundary fault injection proves ValueError, TypeError and RecursionError cannot yield the
original bytes to either SDK input iterator; serialization failure is separately injected.
End-to-end forced-construction failure returns an HTTP failure, or terminates the stdio process
without a response, with no owner/admission work or private exception text. A duplicated envelope
cannot redirect the rejected operation into a legacy resource read. Parse failure after a rejection
decision also cannot restore the original bytes.

**Verification.** Executed 2026-11-05 using native Windows Python 3.12.10 and actual Ubuntu WSL2
Python 3.12.15, Linux kernel `6.6.87.2-microsoft-standard-WSL2`. Native Linux fixtures used
`/tmp/vb152-a-input-security-20261105-native`, not the Windows-mounted checkout.

| Check | Windows passed / failed / skipped | Native Linux passed / failed / skipped |
| --- | --- | --- |
| New security regressions, fresh-process repetition 1 | 74 / 0 / 0 | 74 / 0 / 0 |
| New security regressions, fresh-process repetition 2 | 74 / 0 / 0 | 74 / 0 / 0 |
| All Slice A and acceptance repairs, within full run | 206 / 0 / 0 | 206 / 0 / 0 |
| Complete MCP/Slice A transport/input/compatibility suite | 311 / 0 / 1 | 312 / 0 / 0 |
| Full non-E2E | 3,240 / 0 / 316 (617.67 s) | 3,464 / 0 / 92 (354.86 s) |
| Previous three acceptance-repair regressions, within full run | 53 / 0 / 0 | 53 / 0 / 0 |
| Shared semantic scheduler, within full run | 52 / 0 / 0 | 52 / 0 / 0 |
| Runtime ownership/ADR 0010 modules, within full run | 252 / 0 / 22 | 246 / 0 / 28 |
| Semantic evaluation suite | 29 / 0 / 0 | 29 / 0 / 0 |
| Chromium Dashboard regressions | 36 / 0 / 0 | Not separately run |

The full suites also include all REST/MCP/CLI/health/UI and VB-151 regressions. All 74 new tests
run without skips on both platforms; platform skip counts are unchanged. The focused Windows
skip is an existing symlink-privilege restriction. No assertion was removed or weakened.
All 13 semantic cases and legacy v1 capture/create/append exports match each other and the accepted
reference byte-for-byte, SHA-256
`9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de`.
All 183 Python files and the reference fixture are byte-identical across platform source manifests.
Ruff and compileall (`app tests scripts`) pass on both platforms, and `git diff --check` passes.
The rebuilt SDK 2.3.0 container passes all four Docker/MCP checks: build, dependency import, stdio
EOF, and authenticated read-only/write-enabled HTTP smoke with disposable cleanup. An initial
Docker attempt found its daemon stopped; Docker Desktop was started and all checks rerun successfully.
No required check is unavailable. CI, deployment, release and independent acceptance are not claimed.

Exact commands/logs/XML, before evidence, source manifests and exports are under ignored
`.agent/input-security-*`; native Linux evidence is copied to `.agent/input-security-linux/`.
The runner `.agent/input_security_verify.py` executes two fresh pytest processes for the new suite,
then the combined focused suite, full `python -m pytest -o addopts= -q --ignore=tests/e2e -r s`,
evaluation, exact parity, Ruff, compileall, diff and Chromium checks. Native Linux uses the generated
`.agent/input-security-linux.sh`; Docker commands are in `.agent/input_security_docker.py`.

The mandatory final command remains
`python scripts/agent_finish.py --task-file .agent/vb152-a-task.md`. Its actual selected-check result
is retained in `.agent/input-security-finish.log`; it regenerates `.agent/review_packet.md` only
after all selected checks pass. This document is finalized before that command. Stop immediately
after the current packet is generated; independent review requires a separate session.

**Remaining limits.** Valid request IDs retain correlation; malformed IDs cannot be meaningfully
correlated and use ID 0 only for the rejection marker. An unexpected rejection-builder failure
aborts the request, and stdio may end its process rather than emit a fabricated successful result.
Existing documented omitted-only SDK validation remains unchanged. No new domain authorization,
owner composition, dependency, public named activation, Slice B-D work or ADR change is introduced.
The previous three repairs retain their existing assertions and are rerun in the same verification.
Independent acceptance remains a separate session after the regenerated packet.

## Focused acceptance repair, 2026-11-04

Only four inherited production files changed in this repair: `app/mcp_read_input.py`,
`app/mcp_server.py`, `app/read_execution.py`, and `app/services/space_operations.py`.
Regression coverage is added in `tests/test_policy_read_repairs.py`; this document and the original
ignored task file record the evidence. All other inherited Slice A changes are preserved byte-for-byte.
The pre-repair source, manifest and review packet are retained under ignored `.agent` artifacts.

### Finding 1: HTTP disconnect and the domain token

**Root cause.** Both HTTP protocol paths carried the Starlette Request through SDK metadata, but
the read middleware supplied neither its disconnect state nor a shared token to the read executor.
After consuming the body, the transport had no receive owner that signalled domain reads while the
SDK awaited their result. A blocked read could finish successfully after the client disconnected.

**Change.** The bounded raw-input wrapper now owns one receive pump and one bounded queue per POST.
An actual `http.disconnect` immediately cancels every enrolled read's exact `CancellationToken`.
The SDK Request scope carries that lifetime to explicit tools and omitted tools/resources. The read
executors accept the same token; the omitted branch also carries it through its existing ContextVar
into SDK owner execution and admission. Cancellation does not abandon worker threads: waiters leave
admission while active owners retain their slot until their safe boundary and normal cleanup.
All outbound frames are suppressed after confirmed disconnect. The receive pump is joined on exit.
No write is enrolled or cancelled; committed/uncertain legacy write precedence remains unchanged.
Bearer/Host/Origin checks, request limiting, SDK framing/size checks and stdio behavior remain intact.

**Executable evidence.** The original real-owner ASGI reproduction left the token unset, retained
admission and sent HTTP 200 after owner release. After repair, both supported SDK protocol paths
cancel before release for explicit/omitted tools and qualified/legacy resources. Eight additional
loopback socket-close cases exercise real uvicorn disconnects during execution and admission waits:
they assert token object identity, retained active ownership, released waiters, no response frames
and successful capacity recovery on the same live server. Legacy committed and uncertain writes
retain their filesystem/index-enqueue outcome after a disconnect.

### Finding 2: duplicate JSON members erasing scope

**Root cause.** Ordinary `json.loads` discarded duplicate envelope members before `has_scope`
decided whether strict parsing was required. A second omitted `arguments` or `params` object could
erase an earlier explicit unknown selection and execute a default read.

**Change.** The first raw parser uses an object-pairs hook to retain duplicate/scope evidence across
all objects, including overwritten objects. Invalid scoped JSON enters the existing sanitized
`invalid_request` rejection before policy, admission or owner work. Both HTTP body normalization and
the real stdio line iterator use that first parser. Valid legacy and explicit messages pass through
unchanged; SDK protocol validation still owns framing and transport errors.

**Executable evidence.** The exact two-`arguments` exploit originally returned the default note.
Nine HTTP cases now reject before policy/admission/owner spies: overwritten arguments/params,
duplicate jsonrpc/id/method, params name, space, escaped space and nested members. The same nine
raw envelopes reject through real stdio subprocesses. Valid genuinely omitted and explicit default
requests retain distinct legacy and qualified schemas/URIs on both transports.

### Finding 3: named omitted default-root unavailability

**Root cause.** Omitted compatibility reads called bound-root validation outside the qualified
operation's local-error translator, allowing expected filesystem exceptions to become internal
errors. The SDK's prefixed tool error also missed the exact scoped-message sanitizer match.

**Change.** Only named bound-root validation converts `OSError` and `NoteUnavailableError` to
`unavailable_space`; identity mismatch retains that category. The MCP sanitizer recognizes the
exact SDK wrapper around fixed allowlisted operational messages. Legacy owners with no root anchor
retain their original exceptions. Programming errors propagate; no arbitrary BaseException catch,
fallback root, rebind, filesystem preparation or public named activation is introduced.

**Executable evidence.** Before repair, removed-default-root REST reads returned explicit 503 and
omitted 500. Both now return 503 `space_unavailable`, also when a file replaces the directory.
MCP explicit/omitted tools report `space_unavailable`; both resource URI forms retain uniform
privacy-safe failures. The private CLI projection returns operational exit 1, the public named CLI
remains fenced, and legacy root loss/owner exceptions keep their documented read/search behavior.
Synthetic programming exceptions remain the exact original exception object.

### Repair verification

All three initial regressions failed against the original source **before production edits**:
3 failed, 0 passed, 0 skipped (`.agent/repair-before.log` and `.xml`). The original three passed
immediately after their corrections. The complete repair suite contains 53 tests with no new skips.
Final repair verification on 2026-11-04 used native Windows Python 3.12.10 and actual native
WSL/Linux Python 3.12.15, kernel `6.6.87.2-microsoft-standard-WSL2`. Linux fixtures and SQLite
databases ran under `/tmp/vb152-a-repair-20261104-native`, not the Windows-mounted checkout.

| Check | Windows passed / failed / skipped | Native Linux passed / failed / skipped |
| --- | --- | --- |
| New acceptance-repair regressions | 53 / 0 / 0 | 53 / 0 / 0 |
| Complete Slice A tests (original 79 plus repair 53) | 132 / 0 / 0 | 132 / 0 / 0 |
| Slice A plus MCP server/HTTP/hygiene compatibility | 237 / 0 / 1 | 238 / 0 / 0 |
| Full non-E2E | 3,166 / 0 / 316 (411.42 s) | 3,390 / 0 / 92 (257.59 s) |
| Shared semantic scheduler, within full run | 52 / 0 / 0 | 52 / 0 / 0 |
| Native runtime ownership/ADR 0010 modules, within full run | 252 / 0 / 22 | 246 / 0 / 28 |
| Semantic evaluation suite | 29 / 0 / 0 | 29 / 0 / 0 |
| Chromium Dashboard E2E | 36 / 0 / 0 | Not separately run |

The ADR 0010 row selects all `test_space_runtime_*` modules; the disjoint VB-151 A-G groups
also retain exactly the passing/skipped counts in the original table below, verified from both new
full JUnit files. REST/MCP/CLI/health/UI, authentication, rate limits and Host/Origin regressions
are included in those full runs. No assertions were weakened and no skip was added. All 53 new
cases run on both platforms; the focused Windows skip is an existing symlink-privilege restriction.
The full platform skip counts are unchanged from the original implementation.

Both platforms pass Ruff (`app tests scripts`) and compileall (`app tests scripts`).
`git diff --check` passes. All 13 semantic cases and v1 capture/create/append exports match both
the accepted reference and each other byte-for-byte, retaining SHA-256
`9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de`.
All 182 Python files and the accepted reference fixture match across platform source manifests.
The rebuilt container passes SDK 2.3.0 import, clean stdio EOF, and read-only/write-enabled HTTP
smoke including disposable cleanup: four Docker/MCP checks passed, zero failed or skipped.
No required check was unavailable. CI, deployment, release and independent review are not claimed.

Reproducible commands are retained in the ignored `.agent/repair_windows_verify.py`,
`.agent/repair-linux.sh`, and `.agent/repair_docker_verify.py`. The focused command is
`python -m pytest -o addopts= -q tests/test_policy_read_repairs.py tests/test_policy_read_adapters.py tests/test_mcp_server.py tests/test_mcp_http.py tests/test_mcp_knowledge_hygiene.py`.
Full native runs use `python -m pytest -o addopts= -q --ignore=tests/e2e -r s`;
semantic parity uses `python -m tests.space_integration_evidence --output PATH` plus exact byte
comparison. Logs/XML, exports, source manifest and machine-readable counts are retained under
`.agent/repair-*`; native Linux artifacts are copied to `.agent/repair-linux/`.

The final completion command is
`python scripts/agent_finish.py --task-file .agent/vb152-a-task.md`. Its actual selected-check
result is retained in `.agent/repair-finish.log`; it regenerates `.agent/review_packet.md` only
after successful verification. This document is finalized before that command. Implementation
stops immediately after the current packet is generated, without launching independent review.

Accepted limitations remain the original Slice A limitations above. Cancellation is cooperative:
an in-flight owner must reach its safe boundary; there is no forced thread/resource termination.
Public named serving, Slices B-D, ADR changes, new dependencies and independent review are excluded.

## Original Slice A verification before acceptance repair

Executed on 2026-11-04 using native Windows Python 3.12.10 and actual Ubuntu WSL2
Python 3.12.15, Linux kernel `6.6.87.2-microsoft-standard-WSL2`. Linux fixtures used native
`/tmp` storage. All final rows below have zero failures; no new skips were introduced.

| Check | Windows | Actual native WSL/Linux |
| --- | --- | --- |
| Final Slice A real-owner/transport suite | 79 passed / 0 skipped | 79 passed / 0 skipped |
| Full non-E2E, including VB-151 A–G and all legacy compatibility suites | 3,113 passed / 316 skipped (633.37 s) | 3,337 passed / 92 skipped (387.37 s) |
| Semantic evaluation suite | 29 passed / 0 skipped | 29 passed / 0 skipped |
| All 13 semantic cases and v1 capture/create/append export | Exact accepted reference bytes | Exact Windows/reference bytes |
| Ruff, `app tests scripts` | Pass | Pass |
| Compileall, `app tests scripts` | Pass | Pass |
| Chromium Dashboard E2E | 36 passed / 0 skipped | Not separately required/run |

Windows skips retain the existing platform-specific and unavailable-symlink-privilege cases;
Linux retains 89 Windows-only cases, two native PowerShell cases and one case-insensitive-filesystem
case. Slice A's native junction/symlink containment, real stdio and HTTP cancellation, admission,
read-only SQLite and strict URI tests run without skips on both platforms. The final source manifest
compares all 181 Python files plus the reference fixture byte-for-byte across Windows/native Linux.

The rebuilt Linux container passed MCP dependency import (SDK 2.3.0), clean stdio EOF and the
read-only/write-enabled Streamable HTTP smoke, including disposable cleanup. `git diff --check`
passes. All selected checks are available; no CI, manual browser inspection, deployment, catalog,
release or independent review is claimed.

The final required completion command is
`python scripts/agent_finish.py --task-file .agent/vb152-a-task.md`. Its selected-check outcome is
retained in `.agent/a-finish.log`; `.agent/review_packet.md` is created only on success. This document
is finalized before that command so implementation stops once the current review packet exists.
Intermediate failing runs remain in ignored `.agent` logs and do not establish acceptance.


The following disjoint VB-151 groups are extracted from each full JUnit result (passed / skipped):

| Group | Windows | Linux |
| --- | --- | --- |
| A | 154 / 5 | 154 / 5 |
| B | 62 / 3 | 65 / 0 |
| C | 93 / 8 | 100 / 1 |
| D | 232 / 4 | 235 / 1 |
| E | 949 / 226 | 1094 / 81 |
| F | 46 / 0 | 46 / 0 |
| G | 19 / 0 | 19 / 0 |

## Exact changed files

```text
.gitattributes
ARCHITECTURE.md
BACKLOG.md
PROJECT_STATE.md
ROADMAP.md
app/api/dependencies.py
app/api/health.py
app/api/knowledge_hygiene.py
app/api/notes.py
app/api/private_reads.py
app/api/read_openapi.py
app/api/relationships.py
app/api/search.py
app/api/space_reads.py
app/cli.py
app/main.py
app/mcp_http.py
app/mcp_read_input.py
app/mcp_server.py
app/read_execution.py
app/repositories/semantic_reads.py
app/services/_space_compatibility.py
app/services/adapter_composition.py
app/services/persisted_space_reads.py
app/services/semantic_search.py
app/services/space_admission.py
app/services/space_operations.py
app/services/space_owners.py
app/services/space_runtime.py
app/space_read_transport.py
app/ui/named_fence.py
docs/VB152_A_VERIFICATION.md
tests/policy_read_stdio_server.py
tests/test_mcp_http.py
tests/test_mcp_read_input_security.py
tests/test_mcp_knowledge_hygiene.py
tests/test_mcp_server.py
tests/test_policy_read_adapters.py
tests/test_policy_read_repairs.py
```

The task, preflight, platform logs/XML, source manifest, exports and final review packet are ignored
local `.agent` artifacts. The accepted reference fixture has no content change relative to HEAD;
its worktree checkout bytes were restored from the original Git blob.
