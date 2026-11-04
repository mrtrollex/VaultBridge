# VB-151 Slice G integration evidence

Scope: final integration/regression hardening of the private A–F domain runtime, based on
`889bac007b18be1dbbf72107735d1a3d3a69dd2c` (PR #144). No production owner, adapter, accepted ADR,
ranking, signature, schema, dependency or deployment change is made by G. Independent Slice G
acceptance belongs to a separate fresh review; this evidence is implementation verification.

## Coverage inventory and integration gaps

The existing tests already establish most of the acceptance matrix. G reuses them instead of
duplicating configuration permutations, quota probes or the accepted cleanup fault matrix.

| Contract boundary | Existing executable evidence | Added integration evidence |
| --- | --- | --- |
| Legacy/named configuration, 1–8 spaces, invalid roots/data/policy, aliases and precedence | `test_knowledge_spaces.py`, `test_config.py`, `test_space_operations.py`, `test_space_runtime.py` | Real capture/promotion/index/retrieval with every registry size 1 through 8 |
| Complete authorization before operational FS/SQLite/model work; unknown/denied/capability/disabled selections | `test_space_operations.py`, `test_scoped_federation.py`, `test_scoped_semantic_federation.py`, `test_scoped_semantic_query.py`, `test_scoped_promotion.py` | Mixed selections through the final runtime with forbidden owner/SQLite/backend counters and safe errors/logs |
| Containment, identical paths, symlink/junction escape, root replacement/loss, no fallback | `test_knowledge_spaces.py`, `test_space_operations.py`, `test_scoped_federation.py`, `test_space_runtime_source_binding.py`, `test_space_runtime_isolation_repairs.py` | Same capture IDs/destination paths through independent actual repositories; native equal-path watcher update with an enabled and disabled peer |
| List/literal/Query/semantic identities, order, budgets, cancellation, finite coverage, partial/all-unavailable | `test_scoped_federation.py`, `test_scoped_semantic_federation.py`, `test_scoped_semantic_query.py`, `test_semantic_reads.py`, `test_space_runtime_r11_boundaries.py`, `test_space_runtime_r12_ownership.py` | Runtime-written relationship sources, same-space qualified predicates, fixed quota exhaustion/discard and released cleanup reservations; four retrieval modes after real indexing |
| Compatible descriptors, precise scores/ties/local floors and unchanged evaluation | `test_semantic_candidates.py`, `test_semantic_ranking.py`, semantic federation suites, `test_space_runtime.py` | Exact baseline export of all 13 public results/ranks/best chunks and binary64 precise scores; private single-space search/Query and two-space tie merge checked against independent legacy index |
| Independent bound SQLite, one lazy model/executor, qualified callbacks, disabled work | `test_semantic_binding.py`, `test_semantic_resources.py`, `test_semantic_sync.py`, `test_space_runtime.py` | F durable writes flow through real E4/E3/E1/E2 scheduling into D/C retrieval; no mocked enqueue; terminal stopped/model/executor checks |
| Queue limits/fairness/overflow, native admission, startup/rollback, cancellation and shutdown fencing | `test_semantic_scheduler.py`, `test_shared_filesystem_watcher.py`, `test_shared_watcher_*.py`, `test_space_runtime.py` | Real native watcher update and shutdown through final runtime; peer repository unchanged and disabled storage absent |
| ADR 0010 release, certified retry, opaque failures, reuse, bounded retention and no false stopped | `test_space_runtime_cleanup.py`, `test_space_runtime_descriptors.py`, `test_space_runtime_guard_identity.py`, `test_space_runtime_initialization_retry.py`, `test_space_runtime_resource_owners.py`, `test_space_runtime_r10_ownership.py` through `test_space_runtime_r13_guard_allocation.py` | Existing real platform resource tests rerun unchanged; no expansion of the accepted failure model |
| V2 digest/markers/provenance/approval, copied artifacts/replay, uncertain commits and coordinated writes | `test_scoped_promotion.py`, `test_capture.py`, `test_promotion.py`, `test_vault_service.py` | End-to-end explicit default and named capture/promotion, byte-preserving retries and actual qualified indexing |
| V1 bytes/digests and public REST/MCP/CLI/dashboard compatibility, named-serving gate, safe representations | `test_capture.py`, `test_promotion.py`, `test_api*.py`, `test_mcp*.py`, `test_cli*.py`, `test_ui*.py`, `test_logging.py`, `test_knowledge_spaces.py` | Exact v1 capture/create/append bytes and digests from standalone and unscoped private-default owners compared to accepted baseline; full non-E2E adapter regression suite |

## Reproduction

Use a development environment with the existing requirements. No model download or cloud access is
required. On Windows use the native Python interpreter. On Linux use actual native temporary storage
(`/tmp` under WSL), not Windows symlink privilege skips as a substitute for POSIX verification.

```text
python -m pytest -o addopts= -q tests/test_space_integration.py
python -m pytest -o addopts= -q --ignore=tests/e2e -r s --junitxml=platform-results.xml
python -m tests.space_integration_evidence --output platform-evidence.json
python -m pytest -o addopts= -q tests/eval
python -m ruff check app tests scripts
python -m compileall -q app tests scripts
git diff --check
python scripts/agent_finish.py --task-file .agent/g-task.md
```

Compare `platform-evidence.json` byte-for-byte with `tests/fixtures/vb151_g_reference.json`.
The reference was generated by extracting the accepted merge with `git archive`, copying only
`tests/space_integration_evidence.py` into that isolated source tree and executing it there.
It was not regenerated from modified production code. The export excludes timestamps, host paths,
resource counters and other platform-dependent observations. It includes every result's path,
heading, snippet and three scores, using `float.hex()` for exact binary64 comparison, plus all v1
artifact bytes as hex and their SHA-256 digests. The runner asserts scoped search/Query equality
for both runtime owners and exact two-space merge order before producing an export.

Reference/export SHA-256: `9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de`.
`test_all_semantic_scores_and_v1_bytes_equal_accepted_baseline` retains this parity gate in pytest.
The reference corpus and artifacts contain synthetic test content only.

## Executed results

Executed on 2026-11-04 with native Windows Python 3.12.10 and Ubuntu WSL2 Python 3.14.4,
Linux kernel `6.6.87.2-microsoft-standard-WSL2`. Linux tests used native `/tmp` fixtures.

| Check | Native Windows | Actual WSL/Linux |
| --- | --- | --- |
| Slice G integration suite | 19 passed, 0 skipped/failed (26.64 s) | 19 passed, 0 skipped/failed (6.33 s) |
| Full non-E2E suite, including A–F and G | 3,034 passed, 316 skipped, 0 failed (425.25 s) | 3,258 passed, 92 skipped, 0 failed (242.19 s) |
| Evaluation suite | 29 passed, 0 skipped/failed | 29 passed, 0 skipped/failed |
| All 13 semantic cases and v1 artifact export | Byte-identical to accepted reference | Byte-identical to Windows and accepted reference |
| Canonical Ruff: `app tests scripts` | Pass | Pass |
| Compileall: `app tests scripts` | Pass | Pass |

The relevant A–F suites were executed inside each full invocation. The following disjoint file
groups were extracted from its JUnit XML (pass / skip); broader legacy suites are additional:

| Existing slice group | Windows | Linux |
| --- | --- | --- |
| A: `test_knowledge_spaces` | 154 / 5 | 154 / 5 |
| B: `test_space_operations` | 62 / 3 | 65 / 0 |
| C: `test_scoped_federation` | 93 / 8 | 100 / 1 |
| D: `test_scoped_semantic_federation`, `test_scoped_semantic_query`, `test_semantic_reads`, `test_semantic_candidates` | 232 / 4 | 235 / 1 |
| E: `test_semantic_binding`, `test_semantic_resources`, `test_semantic_sync`, `test_semantic_scheduler`, `test_shared_filesystem_watcher`, `test_shared_watcher_*`, `test_space_runtime*` | 949 / 226 | 1,094 / 81 |
| F: `test_scoped_promotion` | 46 / 0 | 46 / 0 |

The original G verification changed no existing test/assertion/skip. The focused CI follow-up
below adds synchronization to one existing test while retaining every assertion and skip.
Windows skips comprise 238 platform-specific cases
and 78 unavailable-symlink-privilege cases. Actual Linux ran the POSIX symlink, containment,
directory replacement, inotify and descriptor tests; its 92 skips comprise 89 Windows-specific
cases, two native PowerShell cases and one case-insensitive-filesystem case. Windows exercised
its native junction, case/short-name alias, watcher and HANDLE tests. G adds no skips.

The original Ruff attempt over `.` also reached pre-existing lint violations in the vendored
TrueNAS template library. The repository's canonical selector owns the scope `app tests scripts`,
which passes without editing vendor code or weakening checks.

`git diff --check` and documentation/status/test-reference consistency passed. Production code
and accepted ADRs are unchanged. The selector dry-run detects only docs/Python changes and selects
Ruff, full non-E2E pytest (including evaluations), and compileall; Docker, MCP containers and
browser/manual verification are not selected. The required final `agent_finish.py` reruns those
checks and creates `.agent/review_packet.md` only on success. That packet and its final log record
the completion-workflow outcome; independent acceptance is still pending.

No concrete production contract violation was reproduced, so no production repair was needed.
Local detailed logs, JUnit XML, platform exports, task/preflight and review packet are under the
ignored `.agent/` directory; this document and the checked-in fixture retain portable evidence.

## Focused PR #144 CI failure investigation and repair

After initial independent G acceptance, the task owner requested investigation of the failed
Python job in [Actions run 41787780603](https://github.com/mrtrollex/VaultBridge/actions/runs/41787780603).
The hosted runner used Ubuntu 24.04.5, CPython 3.12.15, watchdog 6.0.0 and pytest 8.4.2.
Its checkout was PR merge `095ccd04dbdad8c541d4bef125ccd532bfc09639`, with F head
`8b60be77e98a0ddb1dd15d10ca4acf6fa85c42ce`. The affected test, scheduler and watcher have
identical Git contents at that F head and the accepted G starting merge `889bac007b18`.
Ruff, Docker and dashboard E2E passed in that run; Python failed at
`test_early_full_completion_cannot_cover_later_clean_stops[3]`: three full syncs instead of two.
This local follow-up does not claim a successful rerun of hosted CI.

**Root cause: test synchronization, with a legitimate third reconciliation.** On Linux,
watcher startup can return after native registration but before the inotify reader consumes
its FIFO coverage fence. The test held the first full across several clean stop/reconstruction
cycles, then released it immediately after the last `watcher.start()`. Earlier reconstruction
had set `full_again`. In the failing ordering, the second full therefore began while the final
revision was still unrestored. Its `full_coverage` was `None`; the later genuine native receipt
correctly set `full_again` again. Only the third full could capture and reconcile the current
generation/revision. Suppressing that full would weaken the required coverage guarantee.

Native WSL/Linux transition traces reproduced the original exact assertion failure twice in
100 repetitions using the existing CPython 3.12.15 environment. A separate isolated environment
then matched all 74 installed package versions from the CI job log; the unchanged original test
failed once in 200 repetitions there. The original snapshot was independently compared with the
merged F test text. Each repetition used a fresh temporary directory, MonkeyPatch and native
Observer, with owner-local transition tracing; no sleep, timeout increase or skip was introduced.

**Exact repair:** seven added lines in `tests/test_shared_watcher_ownership_repairs.py` wait on
the existing scheduler condition for `coverage_restored == coverage_revision` before releasing
the first full. This establishes the ordering needed by the existing exact two-full assertion.
The intermediate `monitoring_recovering` and unreconciled-revision assertions, the final
`owner.begins == [None, None]`, and eventual cleared coverage debt all remain intact. No production
code, accepted ADR, API, dependency declaration, E6/F behavior or ADR 0010 cleanup semantics changed.

The repaired test passed 500/500 repetitions in each of the two CPython 3.12.15 environments,
plus 25/25 fresh pytest subprocesses using the exact CI package set.
A separate deterministic native probe held genuine coverage receipts and exercised both orderings
20 times each: receipt before the second full produced exactly two fulls; receipt after the second
full began produced exactly three. Both kept the latest revision unreconciled while necessary and
eventually reconciled it exactly, without retained full-sync debt. The probe used Events and the
scheduler condition, never fabricated coverage counters or native proof.

Fresh follow-up results (all zero failures):

| Check | Native Windows, Python 3.12.10 | WSL/Linux, Python 3.12.15, exact CI packages |
| --- | --- | --- |
| Related watcher/scheduler suites | 117 passed / 1 platform skip (86.34 s) | 342 passed / 52 platform skips (86.26 s) |
| G integration suite | 19 passed / 0 skipped (25.18 s) | 19 passed / 0 skipped (5.58 s) |
| Full non-E2E suite | 3,034 passed / 316 platform/privilege skips (412.44 s) | 3,258 passed / 92 platform skips (220.97 s) |
| All 13 semantic cases and legacy v1 exports | Exact reference bytes/digests | Exact Windows/reference bytes/digests |
| Ruff and compileall (`app tests scripts`) | Pass | Pass |

The explicit Windows related run selected `test_shared_filesystem_watcher.py`,
`test_shared_watcher_ownership_repairs.py` and `test_semantic_scheduler.py`; the Linux run also
included every `test_shared_watcher_*.py`. The full runs include all of these and the E6/F resource
regressions. The semantic/v1 export SHA-256 remains
`9cb76685a5e41bf414022bc2f35e0747276d8934a106b614d471becc684975de`.
The required `agent_finish.py --task-file .agent/g-task.md` reruns selected checks and regenerates
the review packet only on success; its outcome is retained in `.agent/g-ci-finish.log`.
Detailed evidence is retained under `.agent/g-ci-*`: original job log/package pins, environment
inventories, before/after repetitions, native receipt-order probe, JUnit, parity exports and logs.

## Accepted limits and exclusions

- The private runtime implements A–F; G validates integration. Existing public adapters still reject
  named configuration before direct owner construction. VB-152 is future, separately contracted work.
- Cooperating writers and stopped-service maintenance remain required. These tests do not prove
  hostile concurrent-writer isolation, cross-process locks, atomic multi-root snapshots or freshness
  of indexed scores against current Markdown. Final live verification may reduce result counts.
- Work quotas bound work, not blocking OS/model latency. Per-space local floors, fixed allocations,
  sequential federation, bounded local windows and no refill remain deliberate semantics.
- Under ADR 0010, ambiguous cleanup can permanently retain bounded ownership and keep the runtime
  stopping. Certified-safe retry remains required. Process exit is not successful in-process shutdown.
- No migration/rebind tooling, old-artifact conversion, cross-space promotion/relationships/duplicates,
  public health/discovery expansion, successful hosted CI rerun, release or deployment is claimed here.
- Full non-E2E coverage includes dashboard backend/security/template tests. Browser E2E and manual
  browser QA are outside this task; no user-visible adapter behavior is changed.
