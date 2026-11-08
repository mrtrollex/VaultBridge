# VB-152 Slice C authorized write integration

Implementation begins at PR #148 merge
`216f84e1f3c7ad25b9a069178292f823a758768f`, the exact refreshed `origin/main` merge
base. The expected worktree/branch were verified clean before creating the task.
The task owner confirms independent acceptance of Slices A/B and merged VB-151 A–G.
Local `main` is older; it was neither moved nor used as the implementation baseline.

Slice C implements only private authorized writes under the accepted BACKLOG contract
and unchanged ADRs 0009/0010. All production `require_legacy_composition()` guards remain.
No public named serving, Slice D rollout, independent review, hosted CI, commit, push,
PR, release or deployment is claimed.

## Domain and adapters

- `SpaceOperations.scoped_create_note`/`scoped_append_note` resolve one WriteScope,
  read+write policy and the exact operation capability before admission/root/owner work.
  Registry token, exact owner bundle and named root identity are checked; admission is
  entered once and existing VaultService mutation, validation, conflict and dedupe
  behavior remains authoritative. Explicit paths reject Windows host-root syntax on
  POSIX as well as Windows; omitted legacy path spelling retains its previous rules.
- Default-only compatibility dispatch covers create/append and v1 capture/promotion.
  Named omission still authorizes default, never another space. Optional legacy
  promotion advice cannot bypass semantic/index policy. Legacy serving binds its
  existing indexer to operation admission and drains admitted work before indexer
  shutdown. Implicit legacy startup gains no root/storage validation.
- Only a new durable commit enqueues a qualified event through the exact authorized
  binding. Disabled indexing performs no enqueue. Scoped callback exceptions, including
  programming exceptions, cannot invalidate an already committed write. Existing legacy
  logging and Windows REST/MCP path spelling differences are preserved; explicit
  callback errors emit no supplied paths, content or raw exception details.
- Existing REST create/append routes and aliases retain `createNoteV1`/`appendNoteV1`
  and their old alias operation IDs. Optional singular JSON `space` selects strict
  validation, OneSpace<NoteResult>, safe domain errors and no-store. Omission retains
  the legacy request/response/error branch, including its existing bounds. Authentication,
  direct-peer limiting and request observability remain at their original boundaries.
  Generated OpenAPI describes both response/request alternatives and scoped errors.
- Existing MCP `create_note`/`append_note` gain the same explicit parser/domain dispatch.
  `MCP_WRITE_ENABLED=false` still omits both tools; true adds no policy grant. There
  are no additional write tools. Stdio and both supported HTTP protocol paths retain
  one shared operations object, limiter/auth boundaries and strict first-parser
  rejection. Malformed UTF-8, invalid escaped Unicode, duplicates and non-finite
  values cannot bypass rejection by losing scope during SDK parsing.
- Existing CLI capture/review/apply gain singular `--space`. Explicit default/nondefault
  uses v2; omission remains default v1. Bounded JSON parsing rejects duplicate/non-finite/
  invalid-encoding/trailing/oversized inputs. Scope aliases, repetitions and abbreviated
  parser failures cannot echo private supplied values. Client provenance facts and
  source/destination scope overrides never grant authority.
- Scoped review requires the exact destination; an absent create destination remains
  bound without reading an append preimage. Apply validates bounded approval fields,
  reconstructs review in the current process, decides the exact approved request and
  applies its registry-bound decision through existing immediate precommit checks.
  Create requires absent destination; append requires its reviewed hash. V1/v2 copies,
  wrong-space provenance, stale hashes and replay conflicts remain rejected.
- The private CLI write profile uses the existing SpaceRuntime, including startup
  reconciliation of unselected enabled owners, waits for scheduler quiescence and then
  shuts down the same generation. Review uses inspection-only owners. No approval
  cache, rebind, cross-process lock/RPC or automatic uncertain-write retry is added.

## Cancellation, outcomes and retained limits

Explicit transports signal the same domain token and drain blocking work. Admission
retains confirmed and uncertain outcomes over late cancellation; an uncertain
`committed=false` is not proof that no commit occurred. Omitted MCP writes retain
their established disconnect behavior. CLI interruption signals and joins the same
work rather than running another mutation. Failed cleanup cannot erase available
write evidence and returns an operational exit status.

Two supplemental main-thread Python interrupt scenarios pass on each native platform.
They interrupt actual v2 capture after physical mutation, verify cancellation reaches
the exact caller token, join the same operation, and retain confirmed or commit_unknown
evidence without another write. The uncertain case also verifies that the file exists
despite `committed=false`. These exercise Python interrupt handling, rather than claiming
an external Windows console-control or terminal-signal test.

The existing runtime and native resource owners are unchanged. ADR 0010 still permits
permanently ambiguous ownership and a stopping runtime; no completed future, numeric
handle reuse or process exit establishes successful in-process shutdown. Existing native
cleanup/reuse/certified-safe retry tests are rerun in the full suites. No forced release
of foreign resources or unrelated lifecycle redesign is introduced.

Guarantees remain limited to the existing cooperating-filesystem model. There is no
hostile-writer/hardlink isolation, atomic multi-root snapshot, hard latency guarantee,
cross-process index lock, freshness promise or universal cleanup liveness. Public named
configuration and named Dashboard/maintenance/discovery remain unavailable until their
accepted activation boundary. CLI writes require the serving application/stdio stopped.
Legacy v1 validation, owner programming-error boundaries and artifact formats remain.

## Verification

Executed on 2026-11-08 with native Windows Python 3.12.10 and native WSL/Linux
Python 3.12.15, kernel `6.6.87.2-microsoft-standard-WSL2`. All 193 Python sources
under app/tests/scripts match byte-for-byte across platforms and remained unchanged
during each final runner. Counts below are passed / failed / skipped;
overlapping subset commands are not added to full-suite totals.

| Check | Windows | Native WSL/Linux |
| --- | --- | --- |
| Slice C integration, frozen source | 190 / 0 / 0 | 190 / 0 / 0 |
| Capture/promotion and VB-151 F regressions | 195 / 0 / 11 | 205 / 0 / 1 |
| VB-151 F scoped v2, within full | 46 / 0 / 0 | 46 / 0 / 0 |
| Slice A/B security/compatibility | 516 / 0 / 0 | 516 / 0 / 0 |
| SpaceRuntime lifecycle/resource modules, 306 within full | 284 / 0 / 22 | 278 / 0 / 28 |
| Full non-E2E, 4,056 collected | 3,740 / 0 / 316 | 3,964 / 0 / 92 |
| Semantic evaluation | 29 / 0 / 0 | 29 / 0 / 0 |
| Exact 13-case semantic and legacy v1 export | Exact accepted reference | Exact accepted reference |
| Chromium Dashboard | 36 / 0 / 0 | Not separately run |
| Ruff / compileall, app/tests/scripts | Pass | Pass |

The Windows full run completed in 971.92 seconds and Linux in 618.01 seconds.
No skip was added: platform/privilege totals remain 316 and 92, as in the merged
Slice B evidence; all 190 new tests execute on both platforms. No inherited test
instability was reproduced in these final runs. Passing runs do not establish a
universal timing or cleanup-liveness guarantee.

All 13 semantic cases, full-precision score exports and legacy v1 capture/create/
append bytes and digests match both the accepted fixture and the other platform.
Reference SHA-256 remains
`9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de`.
`git diff --check` passes. No required local verification was unavailable.
Hosted GitHub CI and independent review were not executed.

The final rebuilt Docker/Compose/MCP checks pass 5 / 0 / 0: Compose validation,
image build, MCP SDK 2.3.0 import, clean stdio EOF, and read-only/write-enabled
Streamable HTTP smoke. Public default REST/CLI smoke executes real synthetic
owners, both REST aliases, auth rejection, new-commit enqueue isolation and CLI
v1/v2 capture/review/apply with a deterministic local embedding backend.

`tests/test_authorized_write_adapters.py` executes real synthetic filesystem owners,
registry/resolver/admission, actual stdio subprocess frames and both authenticated
MCP HTTP protocol paths. It covers default/nondefault writes, legacy parity, denied
policy/capability zero-work, same paths in distinct roots, traversal/foreign roots,
replacement roots and real Windows junction/POSIX symlink containment, strict input,
tool gating, v2 CLI round trips, restart reconstruction, exact approved content,
stale/copied provenance, dedupe/conflict, qualified indexing and callback failure,
precommit/late cancellation, actual in-flight shutdown and uncertain capture evidence.
Existing security, native cleanup, capture/promotion, semantic and UI tests supplement
these integration tests; operations are not replaced with adapter mocks.

### Failures found during implementation

- Initial native focused run: 184 passed / 2 failed / 0 skipped. Both failures were
  foreign Windows drive syntax accepted as relative on POSIX. The scoped-only owner
  check corrects this; the same cases pass on both final native platforms.
- Escaped invalid Unicode initially reached SDK parsing on real stdio and timed out.
  First-parser Unicode validation now produces irreversible safe rejection, with
  malformed UTF-8 and genuine valid Unicode regressions retained on both transports.
- Three existing logging tests used the previous Python endpoint injection signature.
  Their injection was updated to registry-bound operations, retaining every assertion;
  legacy mutation-before-enqueue-warning order is preserved. The focused logging run
  then passed all 26 cases within the 174-case correction run, and in both full runs.
  No skip, assertion, timeout or arbitrary sleep was added.
- A profile test proved immediate shutdown could cancel queued startup/postcommit work.
  CLI now waits on the existing scheduler before shutdown; actual default and selected
  space indexes are verified after exit, without excluding unselected enabled owners.

Earlier exploratory logs, failed fixture setup, and interrupted runs preceding source
corrections are not final acceptance evidence. No failure is attributed to inherited
instability without a before/after reproduction on the exact node.

## Reproduction and evidence artifacts

- Task/preflight: `.agent/vb152-c-task.md`, `.agent/task_packet.md`.
- Frozen-source runner: `.agent/c_verify.py`; native source transfer:
  `.agent/c_native_copy.py`. Windows uses the existing development Python 3.12.10;
  WSL uses native Python 3.12.15 under a native home-directory source checkout and
  native temporary filesystems/SQLite, rather than a Windows-mounted fixture tree.
- Final runs: `.agent/c-windows-verified/`, `.agent/c-linux-verified/`; per-command
  logs/JUnit, counts, source SHA-256 manifests and exact reference exports are retained.
  `.agent/c_collect.py` verifies current/native source equality and both reference
  exports; `.agent/c-results.json` retains exact counts and full-suite subset
  membership. `.agent/c-last-corrections.log` retains the final focused logging/parser run.
- Standalone command: `python -m pytest -o addopts= -q --ignore=tests/e2e -r s`.
  Evaluation: `python -m pytest -o addopts= -q tests/eval`. Reference export:
  `python -m tests.space_integration_evidence --output PATH`, compared byte-for-byte
  with unchanged `tests/fixtures/vb151_g_reference.json` and the other native platform.
- Final Chromium: `.agent/c-chromium-final.log` / `.xml`. Docker runner/results:
  `.agent/c_docker_verify.py`, `.agent/c-docker-final.log`, `.agent/c-docker-results.json`.
  Public default smoke: `.agent/c_public_smoke.py`, `.agent/c-public-smoke.log`,
  `.agent/c-public-smoke-results.json`; generated schema: `.agent/c-openapi.json`.
  Supplemental interrupt runner: `.agent/c_cli_interrupt.py`, with
  `.agent/c-cli-interrupt-windows.log` / `.agent/c-cli-interrupt-linux.log` (2 / 0 / 0 each).
- Completion: `python scripts/agent_finish.py --task-file .agent/vb152-c-task.md`.
  The final invocation follows the successful standalone checks above and owns another
  complete selected-check run. `.agent/c-finish.log` records its actual results;
  `PYTEST_ADDOPTS=-v` affects visible counts only. It generates `.agent/review_packet.md`
  only after all selected checks pass, and implementation stops at that handoff.
  A separate fresh review, supplied only that packet, remains required.

## Exact changed files

```text
ARCHITECTURE.md
BACKLOG.md
PROJECT_STATE.md
README.md
ROADMAP.md
app/api/notes.py
app/api/read_openapi.py
app/api/space_reads.py
app/cli.py
app/main.py
app/mcp_read_input.py
app/mcp_server.py
app/services/_scoped_promotion.py
app/services/_space_writes.py
app/services/adapter_composition.py
app/services/promotion.py
app/services/space_operations.py
app/services/vault.py
app/services/write_composition.py
app/space_read_transport.py
app/space_write_transport.py
docs/VB152_C_VERIFICATION.md
tests/authorized_write_stdio_server.py
tests/test_authorized_write_adapters.py
tests/test_logging.py
```

Task/preflight/review packets, local runners, logs, exports and source manifests under
`.agent` are ignored local artifacts. Accepted ADRs/reference fixture, persistence schema,
ranking, model defaults, dependencies and public named activation guards have no changes.
