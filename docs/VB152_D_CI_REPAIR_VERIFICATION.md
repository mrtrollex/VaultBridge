# VB-152 D Python CI repair verification

## Scope and baseline

Focused repair on `fix/vb-152-d-python-ci`, initially clean at
`a35fb060d61f5d6db837a54aeba90467df6b3983` (accepted PR #150).
No production code, accepted ADR, dependencies, version, architecture or reference fixture changes.
No commits, pushes, PRs, merges, deployment, publication, subagents or independent review.

The exact [CI #318 run](https://github.com/mrtrollex/VaultBridge/actions/runs/42503988321)
has successful Docker and dashboard-e2e jobs and failed Python job `128802553010`.
Its log actually identifies a pull-request run on temporary merge
`58b6cc8e26b6f2e1fd9f0fd80759eef5f17492ba`, rather than a post-merge push run.
The temporary merge and accepted merge have the same Git tree:
`e9cd9933d7fd814ef78f4e87ec43f4a5b9627219`.
The Python failure log contains exactly the two reported failing nodes; compileall was skipped
after pytest failed. CI logs are retained locally in `.agent/ci-318-python.log`.

The preceding [PR #149 CI #316](https://github.com/mrtrollex/VaultBridge/actions/runs/42470447989)
passed. Both hosted logs report Python 3.12.16 and identical installed dependency versions,
including NumPy 2.5.4. The affected scheduler, repository, semantic scorer, original test and
evidence generator have no diff between the PR #149 merge and this baseline. A passing earlier
run therefore does not establish permanent numeric or scheduling stability.

## Failure 1: test read raced another owner's index lifecycle

The original test signaled `indexed` inside research's `sync_step` after a durable indexed batch.
It immediately read research and default repositories. That receipt establishes neither scheduler
quiescence nor another owner's readiness. Index readiness is explicitly asynchronous in
`ARCHITECTURE.md`; startup reconciliation of other enabled owners is authorized work.

Native Linux reproduction with unmodified tests:

- First isolated pair: both passed.
- Twenty fresh pytest processes, varying `PYTHONHASHSEED`: three scheduler failures (iterations
  9, 12 and 18), all at the reported default `load_chunks()` assertion.
- A main-thread exception trace around the unmodified test reproduced another failure on iteration
  16. The underlying exception originated at `app/repositories/semantic.py:898`: a pre-existing
  sidecar's identity changed between the reader's probes and final snapshot acceptance. The reader
  then correctly raised `IndexBindingUnavailableError: semantic_unavailable` at line 900.
- The untouched full Linux suite passed 4110 tests with 92 existing platform skips. Full-suite
  pollution is not necessary to reproduce the failure; execution ordering alone suffices.

Retained evidence: `.agent/linux-before-repeat.log`, `.agent/before-09.log`,
`.agent/before-12.log`, `.agent/before-18.log`, `.agent/scheduler-trace.log`,
and `.agent/baseline-full.log`. The hosted exception suppresses its underlying cause, so its exact
internal rejecting predicate cannot be recovered from that log alone. The local natural failure
proves the concrete sidecar rollover race at the same assertion and classification.

The correction waits for `rt.scheduler.wait(20)` before reading either repository, then checks every
owner for running work, full-sync debt and errors. This matters because `wait` also returns for a
paused failure; idle alone is not success. Existing research-only indexing and default-empty
assertions remain. The test also runs a controlled case that queues default reconciliation at the
research receipt and holds the research turn until the test enters the drain barrier. Removing the
barrier leaves deterministic pending work, rather than relying on a lucky thread schedule.

No production binding checks, sidecar checks, isolation rules, unavailable classification or
scheduler lifecycle changed. Existing `test_live_sidecar_replacement_discards_snapshot` coverage
continues to require rejection of changed WAL/SHM identities.

## Failure 2: host BLAS reduction order in synthetic evidence

The deterministic concept embedder fixes input vectors, but their normalization and dot products
previously used host-selected BLAS reductions. On the same native Linux process environment,
changing only `OPENBLAS_CORETYPE` reproduces the CI discrepancy exactly:

| Uncorrected numeric backend | First differing byte | Differing fields | Evidence SHA-256 |
| --- | ---: | ---: | --- |
| SkylakeX | none | 0 | `9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de` |
| Haswell | 9210 | 4 | `63a997d586bc52c348d121ad2d20fbbe4004adbfb51b52094e5ce3fff3de3a12` |
| Prescott | 7006 | 24 | `703d7dc6ebf6b23725724a5964158ea2e2cca8e4df58566b5e6749017ce6962e` |

Exact Haswell field comparison, expected then actual:

| JSON path below `semantic_precise` | Accepted | Uncorrected Haswell |
| --- | --- | --- |
| `hierarchy-database-recovery/0/score` | `0x1.dcd2fc5c5c5c6p-2` | `0x1.dcd2fa0202020p-2` |
| `hierarchy-database-recovery/0/semantic_score` | `0x1.5398080000000p-1` | `0x1.5398060000000p-1` |
| `hierarchy-storage-recovery/1/score` | `0x1.71f08f2f2f2f3p-2` | `0x1.71f08cd4d4d4dp-2` |
| `hierarchy-storage-recovery/1/semantic_score` | `0x1.f17bf00000000p-2` | `0x1.f17bec0000000p-2` |

Both rows identify `Runbooks/Recovery Alpha.md`, heading `PostgreSQL > Recovery Procedure`.
Every other serialized field is equal, including all lexical/public scores, result ordering,
headings/snippets and every legacy v1 byte/digest. Comparison traverses the entire JSON document
and separately compares serialized bytes; it does not stop at byte 9210.

Instrumentation compared all 34 unique raw synthetic vectors: their bytes are identical across
kernels, but three norms differ. The Recovery Alpha vector's norm is `0x1.21fe240000000p+1` with
SkylakeX and `0x1.21fe260000000p+1` with Haswell. Its changed stored normalized embedding accounts
for the two precise semantic scores and corresponding hybrid scores. This is numeric evaluation
order, not iteration order, hash order, corpus content, a dependency-version change or a Slice D
ranking regression. The hosted CPU kernel is not logged; local kernel selection reproduces the
complete observed difference without claiming access to that hosted CPU.

The test-only generator now supplies a fixed binary32 reduction tree for its 80-dimensional
synthetic numeric backend. It folds four 16-element blocks to eight/four/two/one lanes and adds
the final 16 binary32 products in binary64 before binary32 conversion; normalization uses the same
dot reduction and binary32 square root. This makes explicit the accepted reference's reduction
order, as seen in the OpenBLAS
[sdot driver](https://github.com/OpenMathLib/OpenBLAS/blob/develop/kernel/x86_64/sdot.c) and
[SkylakeX kernel](https://github.com/OpenMathLib/OpenBLAS/blob/develop/kernel/x86_64/sdot_microk_skylakex-2.c).
The scalar/elementwise implementation does not dispatch a BLAS kernel or require AVX512 hardware.
It does not consult the fixture or round/replace any generated score.

Only the evidence export temporarily substitutes the semantic module's numeric backend; production
normalization, chunk selection, filtering, scoring weights, hybrid computation and ranking methods
execute unchanged. NumPy's shared module is never mutated. Restoration on export failure is tested.
Ordinary evaluation and runtime tests continue to exercise real NumPy/BLAS. This fixed synthetic
evidence does not assert universal bitwise equality of real model scores across BLAS kernels.

New subprocess regressions use automatic CPU selection and the SSE2 Prescott baseline with distinct
hash seeds; they compare the complete JSON and exact bytes to the untouched accepted fixture.
They do not require AVX/AVX512 instructions on older test hosts or add platform skips.
Supplemental local tests also exercise Haswell and SkylakeX on this capable host.

After correction all three kernels produce exactly
`9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de`, with zero differing fields/bytes.
All 13 cases remain, including scoped search, scoped Query, federation ordering and independent
legacy comparison. Capture, create and append v1 bytes, retry behavior and digests remain exact.
Retained exports and comparisons: `.agent/evidence-{raw,fixed}-{Haswell,SkylakeX,Prescott}.json`,
`.agent/parity-matrix.log`, `.agent/norm-differences.log` and `.agent/numeric-*.log`.

## Verification environment and commands

Windows: native Python 3.12.10, NumPy 2.5.2, existing development virtual environment; `pip check`
passes. Linux: native WSL2 Ubuntu, Linux kernel 6.6.87.2, AMD Ryzen 5 7600X, Python 3.12.15.
A dedicated Linux environment installs `requirements-dev.txt` with the same 74 package versions
reported by CI #318. Python 3.12.16 is unavailable from the installed uv distribution catalog;
the native Python 3.12 verification uses 3.12.15 and does not claim patch-version identity.
The original full Linux baseline additionally used the existing G verification environment
(NumPy 2.5.4 and repository-compatible dependencies). Test temporary data lives on native Linux
storage; source is read from the Windows worktree through WSL.

Representative commands (use each platform's environment interpreter):

```text
python -m pytest -q -ra tests/test_scoped_promotion.py::test_actual_scheduler_indexes_only_bound_space tests/test_space_integration.py::test_all_semantic_scores_and_v1_bytes_equal_accepted_baseline
python -m pytest -q -ra tests/test_scoped_promotion.py tests/test_semantic_binding.py tests/test_semantic_scheduler.py tests/test_space_runtime.py tests/test_space_runtime_source_binding.py tests/test_space_integration.py tests/test_space_integration_evidence.py tests/test_authorized_write_adapters.py tests/test_public_space_composition.py tests/eval
python -m pytest -q -ra --ignore=tests/e2e --junitxml=.agent/PLATFORM-full.xml
python -m pytest -o addopts= -q tests/eval
python -m tests.space_integration_evidence --output .agent/PLATFORM-evidence.json
python -m ruff check app tests scripts
python -m compileall -q app tests scripts
git diff --check
python scripts/agent_finish.py --task-file .agent/vb152-d-ci-repair-task.md
```

Repeated tests use fresh pytest processes, independent temporary fixtures and varying hash seeds.
The exact failing Haswell kernel is also selected in Linux repetitions. No CI rerun is used as
proof, and no new hosted CI result is claimed.

The focused Linux scheduler/binding/runtime/C-D composition/evaluation set passed 847 tests with
one existing skip (a case-alias test requires a case-insensitive filesystem). The Windows focused
original nodes plus new evidence regressions passed 6/0/0. All 29 semantic evaluation tests pass
on each platform. The final repeated original-node matrix runs 20 fresh processes per platform,
three test cases per process (the scheduler node now has two parameters): 60/0/0 on Windows and
60/0/0 on Linux with `OPENBLAS_CORETYPE=Haswell`. Additional earlier post-fix Linux repetitions
also completed 30 processes without a failure.

Retained logs: `.agent/linux-targeted.log`, `.agent/windows-eval.log`, `.agent/linux-eval.log`,
`.agent/windows-after-repeat.log`, `.agent/linux-final-repeat.log` and `.agent/linux-after-repeat.log`.
Both `.agent/windows-evidence.json` and `.agent/linux-evidence.json` have the accepted SHA-256 above.
Each export is exactly 26,158 bytes; complete parsed-JSON and byte comparisons both pass.
Independent hashing of the exported v1 destination bytes confirms:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| capture | 213 | `e7ee721f1e9d8b4724fe9d70cdcd925f35a4eac2ba6a8397eadd4e43bfe9938c` |
| create | 616 | `97b822f95a5673e42528a6a45badee03b0adb230e9fbd1c132509aaf5b275814` |
| append | 655 | `9c8b86990788350d4f7f37abb8ca50d4ff927f6eef8b28905d80e2a53491f6ea` |

Ruff, compileall of app/tests/scripts and `git diff --check` pass on the current source.
The change-aware selector requires Ruff, full non-E2E pytest and compileall. It does not select
Docker, MCP container, browser or manual gates for this test/document-only diff. The passing
hosted Docker/dashboard results describe the baseline run, not a newly executed repair-branch CI.

Final full native Linux non-E2E: **4114 passed, 0 failed, 92 skipped**, 4206 collected,
707.283 seconds (JUnit). Skip totals are unchanged from the accepted baseline. Its overlapping
subsets include production composition 146/0/0, authorized write adapters 190/0/0, runtime family
278/0/28, and scheduler/binding 347/0/1 (passed/failed/skipped).
Evidence: `.agent/linux-full.log` and `.agent/linux-full.xml`.

Final full native Windows non-E2E: **3890 passed, 0 failed, 316 skipped**, 4206 collected,
1088.340 seconds (JUnit). Skip totals are unchanged from the accepted baseline.
Its overlapping subsets include production composition 146/0/0, authorized write adapters
190/0/0, runtime family 284/0/22, and scheduler/binding 339/0/9 (passed/failed/skipped).
Evidence: `.agent/windows-full.log` and `.agent/windows-full.xml`.
Both full suites therefore contain the original 4202 cases plus four additional regression cases;
no test was removed and no skip was added. No environment-dependent failure remains in the
executed post-fix matrix.

The final completion command is:

```text
python scripts/agent_finish.py --task-file .agent/vb152-d-ci-repair-task.md
```

Its exact selected checks and final results are retained in `.agent/ci-repair-finish.log` and
`.agent/finish-windows.xml`. The selector reruns Ruff, the complete Windows non-E2E suite and
compileall; it creates `.agent/review_packet.md` only after those checks succeed. The implementation
session stops at that generated-packet handoff, with a separate fresh acceptance review still
required. No independent reviewer is launched here. The native matrix and exact parity gates
above have no unresolved local failure or required-but-unavailable check. Hosted repair-branch CI
remains independent evidence and has not been run.

## Exact changed files

- `tests/test_scoped_promotion.py`: drain all owners, reject paused/error debt, controlled late-default
  reconciliation case; retain bound-space assertions.
- `tests/space_integration_evidence.py`: fixed reductions scoped to synthetic evidence export.
- `tests/test_space_integration_evidence.py`: fresh-process numeric/hash-order parity and restoration
  on export failure.
- `docs/VB152_D_CI_REPAIR_VERIFICATION.md`: this investigation and verification record.

Ignored completion artifacts include `.agent/vb152-d-ci-repair-task.md`, `.agent/task_packet.md`,
diagnostic scripts, logs, JUnit reports, exports and the final `.agent/review_packet.md`.
The accepted fixture remains unchanged. There is no public behavior, API, security, migration,
storage format, dependency or deployment change. Existing cooperating-filesystem and lifecycle
limits remain; separate fresh acceptance review is still required.
