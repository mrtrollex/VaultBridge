# VB-152 Slice B implementation and verification

Slice B starts from accepted Slice A merge `670ca21df166f34e989e88d86a95fe6bf81d67b5`
(PR #147), on `feat/vb-152-b-knowledge-query`. HEAD and merge base matched that commit,
the branch matched the requested branch, and the worktree was clean before implementation.
The accepted adapter contract is BACKLOG.md VB-152, merged through PR #146. The task owner's
Slice A acceptance confirmation supersedes the baseline documents' pending-review wording.

Production named-space activation remains guarded. Independent acceptance returned FIXES REQUIRED
with two MEDIUM input-boundary findings. The focused repair and its evidence follow below; fresh
acceptance remains separate. Neither the implementation nor repair session launches a reviewer or
subagent, commits, pushes, creates a PR, deploys, releases or claims hosted CI.

## Focused acceptance repair: MCP input boundary

Only two production files change relative to the original Slice B handoff: `app/mcp_server.py`
and `app/mcp_read_input.py`. The regression module is `tests/test_knowledge_query_input_repairs.py`;
this report and the ignored original task file record scope/evidence. A pre-repair SHA-256 manifest,
copies of both production files, this report and the prior packet are under `.agent/b-repair-before/`.
Every other inherited repository file is preserved byte-for-byte.

**Finding 1: malformed UTF-8 became a different valid Query.** Raw fd 0 is claimed using the
installed SDK's binary stream ownership/diversion helpers. VaultBridge then created the SDK's
`_UnownedTextWrapper` with UTF-8 `errors="replace"`. Its incremental text decoder substituted U+FFFD
while reading newline-delimited frames, before `StrictScopedLines`, `normalize_scoped_message`,
the SDK JSON-RPC adapter and the shared Knowledge Query parser saw the input. A raw FF byte inside
literal_text therefore became an encodable, different string, passed validation, entered policy /
admission / scoped owner execution and matched a synthetic replacement-character note.

The wrapper now uses `surrogateescape` to retain malformed-byte evidence through the first parser's
tool/scope classification. The shared normalizer detects non-encodable text and uses its existing
irreversible, allowlisted rejection envelope for Query/scoped/resource input. No tainted value enters
normal SDK tool dispatch; the middleware consumes the marker as `invalid_request: Operation failed.`.
If rejection construction fails, the input aborts before SDK dispatch. Only unchanged genuinely
legacy lines retain the SDK's previous replacement policy, after classification; rejected input
and construction failures cannot enter that compatibility branch. Valid UTF-8, including genuine
U+FFFD, remains unchanged. SDK descriptor claim/diversion/restore, newline framing, JSON-RPC parsing,
protocol negotiation, resource selection and HTTP receive/cancellation/drain ownership are unchanged.
An encoding-tainted line whose JSON classification fails is also rejected, since failed parsing
cannot establish legacy intent. Three injected JSON parse failures initially reproduced loss of
this evidence into replacement decoding; the same cases now require a clean rejection marker,
with neither the query text nor U+FFFD forwarded. Evidence is
`.agent/b-repair-classification-before.log` and the final regression XML.

**Finding 2: non-object arguments became internal_error.** The scoped middleware checked marker
membership on arguments before validating the object's type. Null, numeric and boolean JSON values
raised TypeError; the existing unexpected-error projection correctly classified that programming
exception as internal_error. The boundary now checks exact dict type before membership. Only this
untrusted shape violation raises InvalidReadRequest; unrelated programming failures retain their
existing internal_error classification. REST/CLI validation and the accepted request schema are unchanged.

**Before/after reproduction.** Before production edits, all ten initial regression cases failed:
one real stdio FF-byte query succeeded and performed request work, and null/123/true each produced
internal_error on real stdio and both authenticated HTTP protocol paths. Evidence is
`.agent/b-repair-before.log` / `.xml`: 0 passed / 10 failed / 0 skipped. Immediately after repair,
the same ten passed: `.agent/b-repair-after-initial.log` / `.xml`, 10 / 0 / 0. Policy resolver,
admission and owner-read/session spies record no request work after rejection.

The expanded regressions cover malformed/truncated/overlong/surrogate UTF-8 encodings; valid raw
Unicode and genuine U+FFFD; all non-object JSON argument types on stdio and both HTTP paths; valid
scoped results; REST/structured CLI parity; duplicate-envelope scope erasure combined with invalid
encoding; failed rejection construction; real stdio CRLF framing and valid requests following a
rejected frame; and legacy omitted input compatibility. Direct stdio metadata advertising the older
HTTP version still receives the SDK's existing Unsupported protocol version response before tools.
Four exploratory assertions were corrected to verify that unchanged protocol priority, not to add
unsupported stdio negotiation or weaken the supported-path invalid_request assertions.

Final repair verification is recorded below before completion. Original implementation evidence
is retained in the later sections as historical evidence, not substituted for the repair runs.

**Inherited native test ordering limitation.** One intermediate final-source Linux full run
returned 3,773 passed / 1 failed / 92 skipped. The failure was the unchanged
`tests/test_scoped_promotion.py::test_actual_scheduler_indexes_only_bound_space`, reading the
default-space SQLite binding at line 363. It waits for a research-space indexing event, not for
scheduler quiescence before that default read; the captured logs show startup synchronization
still active. The exact node reproduced the same IndexBindingUnavailableError once in twelve
fresh repaired-source processes and once in twelve fresh processes using the pre-repair MCP files.
The node does not exercise the MCP input boundary. This establishes a baseline failure rather than
attributing it to the encoding/type repair. Scheduler, repository and test assertions/timeouts are
unchanged; no sleep, retry inside the test or skip was added. The full Linux suite is rerun on the
same frozen source, and the final result is reported separately. The inherited startup/read timing
limitation remains outside these two requested repairs; a passing rerun is not a stability guarantee.
Evidence: `.agent/b-ordering-baseline/`, `.agent/b-ordering-repaired/`,
`.agent/b_repair_ordering.py`, and the retained `ordering-failure.log` / `.xml` in the native
final-run artifact directory.

### Final repair verification

Executed on 2026-11-07 with Windows Python 3.12.10 and native WSL/Linux Python 3.12.15,
kernel `6.6.87.2-microsoft-standard-WSL2`. The final native source is under
`/home/<user>/vb152-b-input-repair-final-20261107`, using native temporary filesystems and SQLite.
All 188 Python source files match the Windows worktree byte-for-byte; both native runners verify
that their Python source manifest is unchanged during execution. The inherited-file manifest
confirms only the two authorized production files and this report changed, plus the new regression
module and ignored task/evidence artifacts.

Counts are passed / failed / skipped; subset rows overlap the full suite.

| Check | Windows | Native WSL/Linux |
| --- | --- | --- |
| New input-boundary repair regressions | 62 / 0 / 0 | 62 / 0 / 0 |
| Complete Slice B focused suite | 185 / 0 / 0 | 185 / 0 / 0 |
| Slice A raw-input, canonical URI and acceptance-repair modules | 252 / 0 / 0 | 252 / 0 / 0 |
| Related REST/MCP/CLI compatibility subset | 349 / 0 / 2 | 351 / 0 / 0 |
| Combined input/security/compatibility command | 601 / 0 / 2 | 603 / 0 / 0 |
| Full non-E2E, 3,866 collected | 3,550 / 0 / 316 | 3,774 / 0 / 92 |
| Domain/query/federation/integration, within full | 356 / 0 / 15 | 369 / 0 / 2 |
| Semantic evaluation | 29 / 0 / 0 | 29 / 0 / 0 |
| Chromium | 36 / 0 / 0 | Not separately run |
| Ruff and compileall, app/tests/scripts | Pass | Pass |

The final standalone Windows full run took 824.70 seconds; the final Linux rerun took 394.45
seconds. There are no new skips. The native rerun is green on unchanged source; the inherited
scheduler-test failure and baseline reproduction remain explicitly recorded above.
All 13 semantic cases and legacy v1 capture/create/append bytes and digests match the accepted
reference and the other platform byte-for-byte, retaining SHA-256
`9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de`.
The accepted fixture, ADRs, domain ranking/federation, HTTP disconnect/drain code, other adapters
and public named-serving guards have no repair changes.

Five local Docker/Compose/MCP checks pass (zero failed/skipped): Compose validation, image build,
SDK 2.3.0 import, clean stdio EOF, and read-only/write-enabled Streamable HTTP smoke with actual
Query execution and disposable cleanup. The final completion workflow also rebuilds and checks
the frozen final source. `git diff --check` passes. All required evidence is available; hosted CI,
deployment and independent review are not claimed.

Reproduction and evidence:

- Before: `.agent/b-repair-before.log` / `.xml`; same ten after initial repair:
  `.agent/b-repair-after-initial.log` / `.xml`. Source copies and prior packet:
  `.agent/b-repair-before/`; minimal production repair diff: `.agent/b-repair-delta.patch`.
- Focused command: `python -m pytest -o addopts= -q tests/test_knowledge_query_input_repairs.py tests/test_knowledge_query_adapters.py`.
  Final Windows log/XML: `.agent/b-repair-b-focused-final.*`; native focused: `.agent/b-repair-linux-final/focused.*`.
- Native runner `.agent/b_repair_verify.py`; final Windows artifacts `.agent/b-repair-windows-final/`;
  final Linux artifacts `.agent/b-repair-linux-final-rerun/`. Earlier combined regression artifacts
  are `.agent/b-repair-windows/` and `.agent/b-repair-linux/`; the final full JUnit files independently
  verify each current module's counts. `.agent/b_repair_summarize.py` and
  `.agent/b-repair-final-results.json` retain group membership, source equality and exact export comparisons.
- Chromium: `.agent/b-repair-chromium.*`; Docker runner/results:
  `.agent/b_repair_docker_verify.py`, `.agent/b-repair-docker.log`, `.agent/b-repair-docker-results.json`.
- Completion: `python scripts/agent_finish.py --task-file .agent/vb152-b-task.md`, with
  `PYTEST_ADDOPTS=-v` for visible counts only. `.agent/b-repair-finish.log` records all selected checks.
  The workflow regenerates `.agent/review_packet.md` only after success; implementation stops there.

Accepted runtime limitations in the original section remain unchanged. Valid legacy omission and
protocol behavior remain, including the explicitly tested unsupported stdio version priority.
The inherited scheduler-test ordering limitation is disclosed, not fixed or hidden by a skip.
No subagent, independent review, public named activation, scoped write, commit, push, PR or merge.

## Implementation

- `knowledge_query_transport.py` supplies one strict logical request schema and parser for REST,
  MCP and structured CLI. It constructs existing `ScopedKnowledgeQuery`, `KnowledgeQuery`,
  `MetadataPredicate` and qualified relationship/path values, and delegates to domain validation.
  It preserves omission versus null, raw collection ceilings before normalization, strict integer
  limits, UTF-8 byte bounds, portable scalar typing and existing tag/predicate comparisons.
- REST adds only POST `/api/v1/knowledge/query`, operation ID `queryKnowledgeV1`. The existing
  protected read route applies authentication, limiting, cancellable execution and safe error
  projection. Query always uses scoped validation/output, including omitted spaces. Every Query
  response has no-store. Generated OpenAPI documents the shared request, NoteMatch/SemanticMatch
  response alternatives and privacy-safe domain errors. Existing aliases/IDs and the external
  Action schema remain unchanged; schema HTTP endpoints remain disabled.
- MCP adds only `knowledge_query`, read-only annotated, on the existing server shared by stdio and
  Streamable HTTP. Its input schema is identical to REST's. Execution uses the same scoped parser
  and operations; every result URI comes from canonical qualified identity after domain execution.
  The first raw-envelope parser recognizes Query even without spaces, before duplicate-key
  collapse. Existing irreversible rejection, URI selection integrity and HTTP lifetime/drain
  protections remain. No resource template was added.
- CLI adds `--request-json` and repeatable `--qualified-path`. JSON stdin has a 1,048,576-byte
  ceiling, complete strict UTF-8 JSON parsing, duplicate/non-finite/trailing-data rejection and
  explicit content/selection conflicts. Existing argparse abbreviations cannot evade the content
  conflict check. Qualified flags opt into scoped validation/output, including default omission;
  command-line parse failures in these new modes use the existing privacy-safe parser, including
  abbreviated flag spellings, so malformed numeric values are never echoed.
  they cannot infer selection. Bare paths inherit exactly one effective selected ID. Legacy Query
  without the new flags or explicit scope retains its original result and budgets.
- Structured CLI uses the existing persisted read-only composition. Its domain preflight now also
  checks qualified-selector membership and all required relationship capabilities/dialects before
  owner construction. Execution reauthorizes through `SpaceOperations.scoped_query`; adapters do
  no owner lookup. Legacy composition constructs the existing Query service over its exact bound
  vault/relationship/semantic owners so the new default Query route/tool executes end to end.
- The query engine, budgets, live eligibility, domain ordering/ties, full-precision merge before
  score serialization, semantic floors/windows, coverage/discard rules and index format are unchanged.
  Allowlists project domain identities/items/coverage; MCP alone adds resource_uri. No fallback,
  scope expansion, partial authorization, cancellation success or cross-space edge is introduced.

## Executable evidence

`tests/test_knowledge_query_adapters.py` contains 123 new tests. They execute real registry-bound
owners with synthetic notes/indexes and a deterministic local embedding backend. Coverage includes
literal/semantic/combined execution; omitted/explicit default and multiple spaces; equal paths in
different roots; live tags/typed metadata and both relationship directions/dialects; exact raw/byte
bounds; strict JSON on REST, real stdio and real authenticated HTTP; both supported MCP HTTP protocol
paths; canonical nested resource URIs; selected-space coverage, previous index bases and unavailable
indexes/roots; global truncation; denial before admission/owner construction; error privacy; CLI
structured input, legacy formatting, content conflicts and stdin bounds; saturation and capacity
recovery; HTTP disconnect signalling of the exact caller token while retaining active ownership
until drain, with no response frames. Existing Slice A security/lifecycle assertions remain intact.

The original adapter regression run had two stale inventory expectations for the newly authorized
tool/route; the first Linux full run identified four further API/UI/document inventory expectations.
These were updated without weakening the legacy schema/operation assertions. The
28-test schema/operation-ID rerun and four targeted inventory checks passed. Intermediate failures are retained in ignored logs and
do not establish acceptance. A final CLI check additionally covers explicitly supplied default
limits, abbreviated content flags and privacy-safe argparse failures in the new structured modes.

## Original implementation native verification results

Executed on 2026-11-07 with native Windows Python 3.12.10 and actual native WSL/Linux Python
3.12.15, kernel `6.6.87.2-microsoft-standard-WSL2`. The final Linux source copy is under native
`/home/<user>/vb152-b-native-verified-20261107`; fixtures and SQLite databases use native Linux
temporary storage. All 187 Python source files match the final Windows worktree byte-for-byte.

Counts are passed / failed / skipped. Compatibility and domain rows overlap the full suite.

| Check | Windows | Native WSL/Linux |
| --- | --- | --- |
| New Slice B focused suite, final source | 123 / 0 / 0 | 123 / 0 / 0 |
| Slice A core: policy reads, acceptance repairs, raw-input security | 293 / 0 / 0 | 293 / 0 / 0 |
| Slice A plus MCP/resource/CLI/API compatibility, extracted from full JUnit | 601 / 0 / 2 | 603 / 0 / 0 |
| VB-151 domain/query/federation/integration group | 356 / 0 / 15 | 369 / 0 / 2 |
| Full non-E2E, 3,804 collected | 3,488 / 0 / 316 | 3,712 / 0 / 92 |
| Semantic evaluation | 29 / 0 / 0 | 29 / 0 / 0 |
| Final CLI/parser regression subset | 65 / 0 / 1 | Included in final full run |
| Chromium Dashboard E2E | 36 / 0 / 0 | Not separately run |
| Ruff and compileall, app/tests/scripts | Pass | Pass |

No skip was added. The inherited platform/symlink-privilege skips remain 316 on Windows and 92 on
Linux. The 161-case Slice A raw-input security module and the 38-case canonical resource-identity
module pass on both platforms within the full runs. All new Slice B tests execute on both platforms.
The full Linux run on the final source completed in 378.36 seconds. The Windows standalone full
run completed in 857.45 seconds, before the final argparse privacy/classification correction;
the final 123-case Slice B suite and 65-case CLI subset were rerun afterward. The completion
workflow's complete Windows rerun on the frozen final source then passed 3,488 / 0 / 316 in
739.26 seconds. Its remaining selected checks must also pass before a packet is generated.
This timing boundary is also explicit in `.agent/b-results.json` rather than treating a post-run
source manifest as proof of which code the earlier process imported.

All 13 semantic cases and v1 capture/create/append bytes and digests match the accepted reference
and the other platform byte-for-byte. SHA-256 remains
`9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de`.
The reference fixture, accepted ADRs and external Action schema have no diff.

The rebuilt Linux container passes SDK 2.3.0 import, clean stdio EOF and read-only/write-enabled
MCP HTTP smoke, now including a real `knowledge_query` call and disposable cleanup. Together with
Compose validation and the image build, five Docker/Compose/MCP checks pass, zero fail or skip.
The public REST smoke verifies default Query execution, auth denial before owner work, exactly
one limiter charge per request, no-store on success/401/429 and no semantic storage initialization.
`git diff --check` passes. No required check was unavailable. Hosted CI and independent review
remain unexecuted; local checks are not hosted evidence.

## Reproduction and artifacts

- Task and preflight: `.agent/vb152-b-task.md`, `.agent/task_packet.md`.
- Native runner: `.agent/b_verify.py`. Windows uses the existing development Python environment;
  native WSL/Linux uses a native home-directory source copy and native `/tmp` test filesystems,
  with persistent logs/XML. No mocked POSIX execution or Windows-mounted fixture filesystem.
- Per-platform focused, Slice A, domain, full and evaluation logs/JUnit, exact semantic/v1 exports,
  machine-readable counts and source SHA-256 manifests are retained under `.agent/b-windows/`
  and `.agent/b-linux/` (preliminary runs), with final runs under `.agent/b-windows-final/` and
  `.agent/b-linux-final/`. Final Windows focused evidence is `.agent/b-focused-verified.log` /
  `.xml`; final CLI evidence is `.agent/b-cli-final.log` / `.xml`. `.agent/b-results.json` and
  `.agent/b_summarize.py` retain exact counts, group membership and source/evidence comparisons.
- Chromium: `.agent/b-chromium.log` / `.xml`. Docker runner and evidence:
  `.agent/b_docker_verify.py`, `.agent/b-docker.log`, `.agent/b-docker-results.json`.
  Public default REST auth/limiter/no-store/read-only smoke: `.agent/b_public_smoke.py` and
  `.agent/b-public-smoke.log`; generated OpenAPI artifact: `.agent/b-openapi.json`.
- Canonical full command: `python -m pytest -o addopts= -q --ignore=tests/e2e -r s`.
  Evaluation: `python -m pytest -o addopts= -q tests/eval`.
  Reference export: `python -m tests.space_integration_evidence --output PATH`, followed by exact
  byte comparison with `tests/fixtures/vb151_g_reference.json` and the other platform's export.
- Completion command: `python scripts/agent_finish.py --task-file .agent/vb152-b-task.md`.
  The final invocation sets `PYTEST_ADDOPTS=-v` for visible counts only; selected checks and
  their coverage are unchanged. All nine selected checks must pass.
  Its selected-check log is `.agent/b-finish.log`; `.agent/review_packet.md` is generated only
  after selected checks pass. Implementation stops at the regenerated packet, without review.

## Accepted limitations and exclusions

Named serving remains disabled by every production guard. Only explicit private named composition
is exercised. C/D, scoped writes, named Dashboard/discovery/maintenance, migration/rebind,
cross-space graph features, new ranking/index formats, dependencies and accepted ADR edits are excluded.
Cancellation remains cooperative: blocking owners drain to a safe boundary. There is no forced
thread disposal, atomic multi-root snapshot, cross-process lock, hostile-writer/hardlink isolation,
freshness or wall-clock guarantee. Stopped-service CLI operation and existing model/index artifacts
remain required; unavailable persisted semantic state is not rebuilt or downloaded. ADR 0010 may
retain ambiguous ownership permanently and keep the runtime stopping. External access/proxy logs
remain outside application redaction. No operator vault, production deployment or hosted CI claim.

## Exact changed files

```text
ARCHITECTURE.md
BACKLOG.md
PROJECT_STATE.md
README.md
ROADMAP.md
app/api/knowledge_query.py
app/api/private_reads.py
app/api/read_openapi.py
app/api/space_reads.py
app/cli.py
app/knowledge_query_transport.py
app/main.py
app/mcp_read_input.py
app/mcp_server.py
app/services/adapter_composition.py
app/services/space_operations.py
app/space_read_transport.py
docs/VB152_B_VERIFICATION.md
scripts/smoke_mcp_http.py
tests/test_api.py
tests/test_api_versioning.py
tests/test_knowledge_query_adapters.py
tests/test_knowledge_query_input_repairs.py
tests/test_mcp_http.py
tests/test_mcp_server.py
tests/test_policy_read_adapters.py
tests/test_release_preparation.py
tests/test_ui.py
```

Task/preflight/review packets, scripts, logs, source archives/manifests and exports under `.agent`
are ignored local artifacts. No accepted reference fixture content changed.
