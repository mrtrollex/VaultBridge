# Post-v1.5.0 documentation and maintainability audit

Date: 2026-11-19. Source baseline: `90ec06fefe6d4f9ea77b090787b794861ef57235`.
Scope: documentation consolidation, read-only upstream reconciliation and source/test inspection.
No runtime, test, dependency, deployment, CI or API behavior is changed. This is not a new exhaustive
security/lifecycle review. Validation results for the final diff are recorded below and in the local
completion packet; historical run counts are not reused as current test results.

## Findings before changes

Every significant finding is classified below. A proposed improvement is a candidate, not accepted
implementation scope; an open operator gate is not a code defect inferred from missing evidence.

| ID | Classification | Evidence / consequence | Implemented disposition |
| --- | --- | --- | --- |
| D01 | confirmed stale | README badge/status, PROJECT_STATE and release checklist called v1.4.0 current stable and v1.5.0 unpublished/RC-only despite published v1.5.0 metadata and successful exact-source workflows. | Current summaries corrected; identities centralized in the release ledger. |
| D02 | confirmed stale | CHANGELOG and V150 notes still labelled stable preparation; Knowledge Spaces guide described a future transition. | Published dates/status corrected, preparation context retained where historical. |
| D03 | confirmed stale | TrueNAS guide mixed package 1.0.3 and 1.0.11 as current; upstream now shows 1.0.12/app 1.4.0. | Dated pinned snapshot and automatic image-update/form/lifecycle separation. |
| D04 | duplication | Two root roadmaps, two release checklists and repeated release/task narratives had overlapping ownership. | One canonical roadmap/checklist; old files become navigation stubs; original contents archived. |
| D05 | historical content | ROADMAP mixed completed milestones, detailed specifications and future work across 1,460 lines; PROJECT_STATE accumulated 1,048 lines of snapshots. | Historical snapshots retained; concise current roadmap/state replace them. |
| D06 | historical content | BACKLOG's recommended execution chain and VB-153 plan could be read as current instructions. | Current-work navigation added; old chain/plan explicitly historical, contracts intact. |
| D07 | confirmed contradiction | ARCHITECTURE claimed Knowledge Query was CLI-only although REST/MCP Query profiles ship; security prose still limited the application to one root. | Current composition and contained named binding ownership corrected. |
| D08 | confirmed contradiction | Architecture's Search paragraph said it performed no note reads while the selected-note viewer uses `/api/v1/notes/read`. | Viewer read behavior clarified; mutation exclusion retained. |
| D09 | confirmed contradiction | Original ADR 0003 sessionStorage design looked current despite VB-076 signed-cookie sessions. | Additive implementation-context notice; original accepted decision text preserved. Similar phased-context notices added to ADRs 0004/0006/0009. |
| D10 | confirmed stale | VB-152 links used an obsolete `slices-a-b-and-c-private-activation-blocked` fragment after its heading changed. | Every repository Markdown occurrence repaired, including archives. |
| D11 | confirmed stale | Codex Playbook kept an old next-task narrative and incorrectly said AGENTS.md retained underlying check commands. | Task selection routes to current backlog; selector owns check selection; separate review boundary explicit. |
| D12 | proposed improvement | README's 904 lines hid setup below detailed session/API/CLI/operations material. | Short product entry point; detailed guides extracted with navigation and existing assets. |
| D13 | confirmed contradiction | README's generic Compose variable table included inference settings absent from Compose's environment allowlist. | Extracted Docker guide distinguishes application settings requiring explicit overrides from forwarded variables. |
| D14 | confirmed stale | Release notes retained an unresolved RC2 `note_unavailable` diagnosis after the task owner's oversized-file explanation. | Generic size-policy explanation added, explicitly without a subsequent 801/801 claim. External Release body left unchanged. |
| D15 | genuinely open task | VB-082 catalog Edit/reboot/upgrade/rollback/ixVolume evidence and dedicated MCP form/migration delivery lack completion proof. | Retained as separate operator/downstream work; no completion inferred. |
| D16 | historical content | VB153/VB154/runbooks include source-specific procedures/counts and ignored local receipts. | Context banners/history index; no publication commands executed, old identities/results retained. |
| D17 | proposed improvement | New-user installation should identify a stable image and mode without forcing named JSON. | Published-image Compose overlay example; source-build path retained in Docker guide; mode comparison added. |
| D18 | confirmed stale | ADR 0010 contained one Windows-1252 en dash in an otherwise UTF-8 document, preventing strict UTF-8 reads. | Only that byte encoded as UTF-8; decision meaning unchanged. |
| D19 | proposed improvement | Large security/lifecycle modules mix several necessary responsibilities; existing transport helpers already centralize much validation. | Bounded proposals below; no functional refactor in this scope. |
| D20 | confirmed stale | A few source docstrings, including `space_operations.py`, still describe public named adapters as gated on VB-152. | Recorded for a separate comment-only task; runtime dispatch is active and current architecture is corrected. |
| D21 | proposed improvement | One historical MCP cleanup ledger entry exposed an absolute temporary path containing the workstation username. | Replaced only that path with a generic temporary-root placeholder; retained timestamp, digest, synthetic test paths and cleanup assertions. |

## Evidence and privacy boundary

Current release/tag/workflow and GHCR observations are recorded in the
[release ledger](RELEASE_CHECKLIST.md#v150-release-evidence), including the preserved v1.4.0 identity.
TrueNAS metadata was read at a pinned upstream commit; no upstream repository was modified.
The task owner's stable canary observations are separate from remote CI and local checks.
The public GitHub Release body still contains the older unresolved RC2 diagnosis; correcting remote
release text would require separate authority. This task changes repository documentation only.

The public-document scan found placeholders and established manual compatibility paths, plus one
historical host-user temporary path now redacted. No credential-shaped hits were found by the
targeted token/private-key scan. Resolved Compose/environment output, copied-vault content and raw canary logs were
not added. The oversized-file explanation contains only aggregate size/count facts, with no original
title, path, subject or content. No real vault or production data was opened.
This is a targeted public-document/diff scan, not a forensic audit of all Git history or binaries.

## Documentation architecture and preservation

The [ownership table](README.md#document-ownership) defines the canonical hierarchy. Detailed Docker,
REST, MCP, CLI and Dashboard guidance was extracted from README; mode-specific operator guidance
stays in Knowledge Spaces/TrueNAS. Root compatibility filenames remain available.

### File inventory

All 39 changed/public files are Markdown (26 modified, 13 new); none was deleted or physically
renamed. Historical bodies were copied into clearly labelled archives and compatibility filenames
retained. AGENTS.md and the repository workflow skill remain unchanged.

| Disposition | Files |
| --- | --- |
| Modified root documentation | `README.md`, `README_TRUENAS.md`, `ROADMAP.md`, `PROJECT_STATE.md`, `BACKLOG.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `DEVELOPMENT.md` |
| Root navigation stubs | `RELEASE_CHECKLIST.md`, `VaultBridge-ROADMAP.md` |
| Modified workflow/operator/release references | `docs/CODEX_PLAYBOOK.md`, `docs/DASHBOARD_RELEASE_CHECKLIST.md`, `docs/KNOWLEDGE_SPACES.md`, `docs/RELEASE_CHECKLIST.md`, `docs/V140_RELEASE_NOTES.md`, `docs/V140_RELEASE_PRODUCTIZATION_PLAN.md`, `docs/V150_RC_RUNBOOK.md`, `docs/V150_RELEASE_NOTES.md`, `docs/VB153_RELEASE_READINESS.md`, `docs/VB154_VERIFICATION.md` |
| ADR context/encoding corrections | `docs/adr/0003-web-dashboard-architecture-and-security.md`, `docs/adr/0004-mcp-integration.md`, `docs/adr/0006-bounded-knowledge-query-capability.md`, `docs/adr/0009-knowledge-spaces-and-scope-policies.md`, `docs/adr/0010-safety-first-e6-cleanup-outcomes.md` |
| New guides/index/audit | `docs/API.md`, `docs/CLI.md`, `docs/DASHBOARD.md`, `docs/DOCKER.md`, `docs/MCP.md`, `docs/README.md`, `docs/MAINTAINABILITY_AUDIT.md` |
| New history index/snapshots | `docs/history/README.md`, `docs/history/ROADMAP-original.md`, `docs/history/ROADMAP-pre-v150-cleanup.md`, `docs/history/PROJECT_STATE-pre-v150-cleanup.md`, `docs/history/RELEASE_CHECKLIST-original.md`, `docs/history/V150_RELEASE_NOTES-pre-cleanup.md` |

Historical plans/state and the original stable-preparation notes/canary statement were archived
with their original claims and relative links rebased.
Task specifications/acceptance criteria remain in BACKLOG.md; no open task was silently completed.
Release source SHA/digest strings and accepted ADR decisions remain preserved. Architecture retains
its detailed lifecycle/compatibility reference with a navigation entry point; shortening it blindly
would risk losing normative detail. No documentation framework or frontend dependency was added.

## Runtime/module inspection and proposed debt

Line counts below describe the release baseline, not a quality score. Inspected the named owners,
supporting adapters/transport helpers, nearest test suites, Docker/Compose, typed settings and CI.
Existing shared owners are a reason to avoid mechanically deduplicating transport boundaries.

| Priority / candidate | Source evidence and existing tests | Smallest future scope / proof | Risk |
| --- | --- | --- | --- |
| P1: actionable size/coverage diagnostics | `space_read_transport.py:CoverageOutput`, bounded live reads in `vault.py`, scoped federation and Query report `note_unavailable`; relevant `test_scoped_semantic_federation.py`, `test_scoped_semantic_query.py`, `test_space_operations.py`. | First add a synthetic over-limit/allowed-note regression and map existing reason codes to safe operator advice; preserve wire schemas and privacy. | Low for documentation/fixtures; medium if changing diagnostics or public response fields. |
| P1: common qualification evidence manifest | `verify_release_image.py`, `verify_named_image.py`, `smoke_mcp_http.py` have distinct legacy/named/MCP coverage; tests `test_release_preparation.py`, `test_named_image.py`, `test_smoke_mcp_http.py`. | Define a bounded shared result/identity manifest before considering an orchestration wrapper; retain cleanup and partial-failure evidence. | Medium; source, image and semantic equivalence cannot be collapsed into one green flag. |
| P2: semantic-search responsibility extraction | `semantic_search.py` 2,706 lines; `_index_files` 115 lines, `sync_step` 91; existing `semantic_sync.py`, `semantic_reads.py` already separate responsibilities. Tests cover service/repository/ranking/sync and scoped candidates. | Profile first; extract one pure chunk/input or result-projection helper with identical bytes/order/signature. Avoid moving sync ownership during a cosmetic split. | Medium/high for model/chunking/ranking or cancellation drift. |
| P2: bounded-vault snapshot readability | `vault.py` 1,855 lines; `bounded_markdown_snapshot` 226, capture create 142. Two local reverse-order heap wrapper classes share comparison shape. Existing vault, capture/promotion and native scoped-read tests. | At most consolidate a proven pure ordering helper in a separate task; retain alias limits, symlink identity, byte/commit evidence and platform tests. Do not rewrite filesystem ownership. | High for broad extraction; low benefit from class deduplication alone. |
| P2: CLI presentation/dispatch seams | `cli.py` 1,542 lines; parser 143 and `_run_write_cli` 103. Shared `knowledge_query_transport.py`, `knowledge_hygiene_transport.py`, `space_read_transport.py` already own common contracts. `test_cli.py`, CLI Hygiene and scoped write/Query tests. | Extract one presentation-only formatter or command registration group if change frequency warrants it; preserve exit codes, stdin limits, interruption and result-before-cleanup semantics. | Medium; a universal error mapper could erase confirmed/uncertain-write evidence. |
| P2: MCP registration readability | `mcp_server.py` 1,208 lines; `_read_server` 194 and `_execute` 84. Explicit tool schemas, resource routing, privacy middleware and input binding have separate obligations. MCP server/HTTP/input-security/resource-identity/output-schema tests. | Consider a registration helper only with byte/shape-equivalent discovery/tool/resource tests. Keep scope/privacy routing in its current owner. | Medium/high; superficially repeated error translations are transport-specific. |
| P2: test setup inventory | Many native ownership fault-injection suites and adapter fixtures deliberately control different causal orderings. | Inventory exact fixture duplication before sharing only synthetic root/settings factories; keep owner/release/reuse faults local and assertion-rich. | Medium; shared fixtures can hide ordering and teardown regressions. |
| Retain: repository lifecycle boundaries | `repositories/semantic.py` 1,232 lines; `_connect_bound` 76, initialization/cleanup and immutable-read helpers are already separated. Semantic repository/binding/reads and stopped-lifecycle regressions. | No immediate cleanup justified by file length. A future refactor needs explicit connection/allocation/cleanup failure evidence on actual Windows/Linux. | High; descriptor reuse and partial initialization are correctness boundaries. |
| Retain: small runtime/facade owners | `space_runtime.py` 283 lines and `space_operations.py` 314, with supporting scheduler/admission/compatibility modules. Space runtime/operations and native cleanup suites. | Keep current decomposition. Document stale source comments in a later separately reviewable comment-only scope if useful; no dead helper was proved by this inspection. | High for ownership/lifecycle changes; low for comments. |

No small runtime cleanup had enough value to justify combining it with this documentation diff.
No API, validation, serializer, exception/status translation, concurrency or storage code changed.
Unifying validations without preserving pre-collapse JSON scope checks, authorization ordering and
transport-specific errors would be a substantive contract change, not a cosmetic cleanup.

## Recommended next three small tasks

These are proposals, not newly accepted backlog entries or allocated IDs:

1. Add a reproducible synthetic default-size-limit coverage scenario, with one permitted note and
   one oversized note; assert size-policy behavior and document existing safe diagnostics without
   exposing identifiers or adding wire fields.
2. Inventory configuration forwarding across generic Compose, manual Docker and TrueNAS; add a
   bounded operator diagnostic/example check against typed settings, without changing defaults,
   automatically rewriting configuration or contacting a live installation.
3. Specify a common qualification evidence manifest for the existing legacy/named/MCP runners,
   with source/image identity and independent semantic/lifecycle/cleanup outcomes. Review that
   contract before any orchestration or runner refactor.

Remaining questions: whether/when to authorize the external Release-body correction, who can supply
the missing disposable TrueNAS lifecycle receipts, and which measured workload should anchor future
performance work. None blocks preserving current compatibility or the documentation hierarchy.

## Validation of this cleanup

| Check | Executed result / limitation |
| --- | --- |
| Ruff | PASS: `python -m ruff check app tests scripts` |
| Python compilation | PASS: `python -m compileall -q app scripts` |
| Focused pytest | **360 passed, 4 skipped, 0 failed/errors** across release preparation, TrueNAS package/MCP preparation, Docker cache, task/check/review/finish workflow, configuration, CLI, API versioning and MCP output-schema suites. JUnit/log retained locally under `.agent/docs-cleanup-pytest.*`. |
| Final release/documentation regression rerun | **48 passed, 0 skipped/failed/errors**, `tests/test_release_preparation.py`, after guide/ledger/archival corrections; local `.agent/docs-cleanup-final-release.*`. |
| Existing skipped cases | Four symlink tests could not create file/directory symlinks on this Windows account (`WinError 1314`, missing privilege). Tests/assertions were not modified. No Linux rerun or native production lifecycle certification is claimed. |
| Local links, fragments, assets and fences | PASS: 60 Markdown documents, 389 relative links/fragments/assets, zero missing targets or unbalanced fences. Covers root/public `docs/`, `ix-dev/` Markdown and evaluation README; synthetic corpus links are intentionally not treated as documentation. |
| Historical preservation | PASS: five archived bodies match original release-commit content except rebased/repaired links; all 79 task contract bodies/acceptance criteria and all 62 baseline Markdown SHA/digest identities retained. An expanded scan also retains all 76 raw 40–64 hex identities. |
| Documented Compose overlay | PASS: extracted README overlay validates with placeholder-only environment; configured stable image, legacy root/data locations and loopback binding asserted. No container launch or model download. |
| Manual TrueNAS documentation/package review | Source/settings/manual compatibility values and package tests checked; pinned upstream form/version readback recorded in ledger. No TrueNAS host, catalog deployment or operator lifecycle checks executed. |
| Scope/privacy | Markdown-only tracked/untracked diff; no app/test/script/dependency/CI/deployment changes. Targeted token/private-key/private-host scan and diff inspection; one historical workstation temp path redacted. |
| Completion workflow | The change-aware selector classifies docs plus TrueNAS guidance, selects no automatic runtime checks and reports a manual TrueNAS reminder. The extra checks above supply documentation/source evidence; live operator gates remain open. Final completion/packet status is reported at handoff. |

The broader runtime, Docker/MCP container and browser suites were not rerun for this documentation-only
diff; selected remote exact-release CI is recorded separately. Independent fresh review remains a
separate session starting from `.agent/review_packet.md`; no self-approval is claimed.
