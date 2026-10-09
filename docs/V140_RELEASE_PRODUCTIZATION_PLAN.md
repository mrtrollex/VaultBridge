# VaultBridge v1.4.0 release productization plan

## Executive summary

**Recommendation: v1.4.0, after a read-only Hygiene Dashboard and an exact-image RC canary on real
TrueNAS.** Prepare delivery of the already-designed first-class TrueNAS MCP controls in parallel;
land the upstream package only after the stable image exists and passes its publication checks.
First-class MCP form delivery is not release-ready until existing `additional_envs` MCP values
are carried forward without value loss or duplicate environment names, with fixture and live
candidate-package upgrade proof. Before stable, the live proof must migrate explicitly saved,
non-default legacy Host and Origin allowlists and preserve accepted/rejected Origin behavior;
repeat this proof on an actual Community App upgrade after catalog publication.
Pause VB-151 Slice D for this release. Ship the existing Query, Capture, Promotion and Hygiene CLI
capabilities with accurate operator documentation. Do not add Capture/Promotion network or browser
writes, Query network adapters, or activate named spaces.

The smallest coherent value proposition is: **open the browser to find broken note relationships,
metadata problems and isolated notes, then use the same read-only diagnostics from an MCP client;
configure that client through the Community App form when its package update arrives.** Persistent
dashboard sessions already in HEAD make this a more convenient recurring workflow. Lifecycle proof,
not completion of another internal architecture milestone, determines whether it is safe to ship.

This is a proposal dated 2026-10-09, not accepted backlog scope, completed release evidence, or
authorization to publish. No implementation, release metadata, upstream package, or authoritative
status document is changed by this plan. A separate independent review and owner acceptance must
precede the proposed tasks.

## 1. Method, baseline and evidence limitations

The analysis compares annotated tag `v1.3.0`, dereferenced to
`a7e14ece0de74632d1d9be599d53678931dc64b3`, with HEAD
`3190f5f648d84b88575a90953a2dacd17f378fbd`. HEAD is also the locally available `origin/main`.
The planning branch began clean. Commands inspected included `git log --no-merges v1.3.0..HEAD`,
`git diff --stat`, `--numstat`, `--name-only`, and focused implementation diffs. The range changes
79 files, with 22,508 insertions and 679 deletions. Tests were inspected as specifications and
coverage evidence; their existence is not a claim that they ran in this analysis.

The sources of truth were [AGENTS](../AGENTS.md), [playbook](CODEX_PLAYBOOK.md), exact
[backlog contracts](../BACKLOG.md), implementation and nearest tests, then
[roadmap](../ROADMAP.md), [project state](../PROJECT_STATE.md), [architecture](../ARCHITECTURE.md),
[README](../README.md), [release checklist](RELEASE_CHECKLIST.md),
[TrueNAS design](TRUENAS_COMMUNITY_APP_DESIGN.md) and
[lifecycle runbook](VB_082_TRUENAS_LIFECYCLE_RUNBOOK.md). Accepted ADRs
[0005](adr/0005-portable-pkm-document-model.md),
[0006](adr/0006-bounded-knowledge-query-capability.md),
[0007](adr/0007-knowledge-capture-and-provenance.md),
[0008](adr/0008-knowledge-hygiene-diagnostics.md) and
[0009](adr/0009-knowledge-spaces-and-scope-policies.md) were checked against their runtime boundaries.

Important distinctions and unresolved facts:

- Published stable application/image v1.3.0 and upstream package 1.0.2/image 1.3.0 are the supplied
  baseline and the repository's recorded delivery state. No live catalog, registry or TrueNAS was
  queried for this analysis. Refresh those identities before execution; this plan is not new
  publication evidence.
- The checked-in package is expressly a historical development copy. It still says package 1.0.0,
  image/app 1.1.0 and port 30486. Current accepted upstream identity/port 30491 comes from recorded
  release/project evidence, not from pretending that local copy is current upstream source.
- README, ARCHITECTURE and older PROJECT_STATE dashboard paragraphs still describe sessionStorage
  API-key storage. Actual HEAD uses a signed HttpOnly cookie and `/ui/session`. README's MCP/API
  lists omit Hygiene, and its Capture text still says no promotion/review operation. CHANGELOG's
  initial Query entries predate its CLI. These are documentation drift to fix in release preparation,
  not grounds to infer absent runtime or silently strengthen a contract.
- Historical lifecycle classifications remain historical evidence. Their old explanation that no
  prior catalog state exists is stale for the proposed next upgrade. That does not convert an
  unexecuted upgrade into PASS.
- No present-image regression, real model performance, current catalog migration behavior, new
  browser workflow, or live TrueNAS outcome is proved here. Those are explicit execution gates below.

## 2. Complete meaningful v1.3.0 to HEAD inventory

Classification: **A** user-visible and usable now; **B** usable through technical adapters/CLI;
**C** implemented domain capability missing product UX; **D** internal/not publicly reachable;
**E** operations/security/hardening; **F** documentation/architecture only. A row has one primary
class. Related rows separate a domain implementation from its narrower adapter.

In the surfaces column, R=REST, M=MCP, C=CLI, D=Dashboard, T=TrueNAS form. A dash means no new
surface. "Existing" means an older public operation benefits without exposing a new capability.
Normal TrueNAS usability assumes a future HEAD image: catalog users currently receive v1.3.0.

| Item / origin | Class | Actual implementation and Git evidence | Available surfaces at HEAD | Normal TrueNAS use | Release-note treatment |
|---|---|---|---|---|---|
| Persistent browser session; PR #93, `834f173` | A | Signed seven-day cookie, restore/refresh/logout and current-key invalidation; session routes and UI auth integration implemented | R: schema-hidden `/ui/session`; D: unlock/restore/logout; M/C/T: — | Yes, through browser after image upgrade | Advertise remembered dashboard access with rotation/logout limits |
| Portable PKM model; VB-110/M12, `ac0a41a` | F | Accepted ADR; conceptual title/headings/document shape is not a complete new public document API | R/M/C/D/T: — | No distinct workflow | Explain portable Markdown design briefly; no title-resolution or general PKM API claim |
| Bounded frontmatter and aliases/tags; VB-111/112, `171666a`, `4d40617`, `95883a1` | C | Safe YAML 1.2 Core profile, immutable metadata/body offset, portable field diagnostics and projections; PyYAML runtime dependency added | R/M: through Hygiene diagnostics; C: Query tags and Hygiene; D/T: — | Indirectly via tools; browser benefit awaits Hygiene UI | Describe the concrete diagnostics/filtering, not a new editor or alias-based identity |
| Contained inline Markdown note relationships; VB-113/M12, `6aef8cf` | C | Source-relative parser/resolver and Markdown-specific outgoing/backlink methods implemented | R/M/D: existing wikilink contracts unchanged; C: no direct relationship command; T: — | New Markdown-link facts become useful via Hygiene | Advertise Markdown-link diagnostics when exposed, not expanded old Relationships panel |
| Normalized two-dialect relationships; VB-114, `3a8ec0c` | C | Normalized occurrence/snapshot/backlink domain APIs combine wikilinks and Markdown links | R/M: Hygiene consumes them; C: Hygiene; D/T: — | Technical diagnostic access today | Explain diagnostic coverage; no graph explorer/ranking claim |
| Knowledge Query design/runtime; VB-120/121/M13, `efc2dae`, `e44f7db` | C | Immutable request/result, live literal/tag/metadata/relationship eligibility before unchanged semantic ranking | R/M/D/T: —; C: subset in next row | No browser workflow | Mention only implemented semantics reachable through CLI; full domain predicates are not public filters |
| Knowledge Query CLI; VB-122, `e7be5dd` | B | `query` supports semantic/literal text, folder, exact paths, required tags, limit and bounded result/score/index-basis output | C: yes; R/M/D/T: — | Requires local CLI/container-operation skill | Advertise with examples and limits; metadata/relationship predicates have no CLI flags |
| Capture/provenance design; VB-130, `0fa38d1`, `9d2c568`, `06640ed` | F | Accepted intake/provenance and cooperating-writer contracts | R/M/C/D/T: — from design alone | No | Explain provenance of CLI artifacts, no automatic AI memory claim |
| Portable inbox/draft capture; VB-131/M14, `c61d7c6` | B | Domain plus JSON-stdin CLI; atomic complete-byte create-if-absent at `Inbox/Captures/<UUID>.md`, exact retry/conflict and index outcome | C: `capture`; R/M/D/T: — | Possible for power users, awkward for catalog/browser users | Advertise as local CLI capture only |
| Explicit review/promotion; VB-132/M14, `d1d7ba2`, `c75b515` | B | Domain plus local review/apply CLI; exact source/destination hashes, explicit create/append, portable provenance, retry evidence; source retained | C: `promote review`, `promote apply`; R/M/D/T: — | Power users only; no guided browser approval | Advertise explicit local workflow with stopped-service guidance |
| Hygiene definitions/runtime; VB-140/141/M15, `ceec5b2`, `d6ce90c`, `2933aaf` | C | Bounded live snapshot, relationship/isolation/frontmatter/alias/body diagnostics; optional exact-title duplicate advice and immutable derived-index categories | R/M/C: through adapters below; D/T: — | Useful once an adapter is used | Diagnostic/advisory only; no repair, reliable freshness or semantic duplicate promise |
| Hygiene REST; VB-142, `0ba7f42`, `8860d03` | B | Protected versioned-only POST, strict typed input, safe error/result mapping; existing UI cookie accepted | R: `/api/v1/knowledge/hygiene/scan`; M/C: separate rows; D/T: — | curl/integration skill needed | Advertise scan endpoint; no legacy alias or runtime OpenAPI page |
| Hygiene MCP; VB-142, `0e93977` | B | Shared read-only `knowledge_hygiene_scan` tool on stdio/HTTP with strict request and structured result | M: yes; R/C: separate rows; D/T: — | Meaningful for configured AI/MCP clients; catalog form gap obstructs setup | Headline power-user improvement; eight read-only tools, ten with existing opt-in writes |
| Hygiene CLI; VB-142, `3eb565f` | B | `hygiene scan`, human/JSON evidence with partial/truncation states | C: yes; D/T: — | Local power user only | Advertise read-only diagnostic CLI |
| Multi-space accepted design/contract; VB-150/151/M16, `45debff`, `4657020` | F | ADR 0009 and finite scope/budget/policy/provenance contract; VB-152 unscoped | R/M/C/D/T: — | No | At most developer note; no serving promise |
| Multi-space A; `aa32289` | D | Strict private JSON/types/registry/resolver, authorization-before-work; public named startup blocked | R/M/C/D/T: no named surface | Cannot use; configuring named JSON rejects startup | Do not headline or give activation instructions |
| Multi-space B; `aa36e49` | D | Explicit private local bundles, qualified one-space reads/links/advice/hygiene/inspection; deferred lifecycle handle and no semantic execution | R/M/C/D/T: — | Cannot use | Internal foundation only |
| Multi-space C; `c84bb99` | D | Internal budgeted sequential list/literal/nonsemantic Query federation, qualified predicates, cancellation/coverage | R/M/C/D/T: — | Cannot use | Internal foundation only; not cross-vault retrieval support |
| Coordinated and race-aware Markdown writes; VB-131/132 | E | Shared cooperating thread/process write coordination extends legacy create/append; captured/promoted atomic/replay/uncertain outcomes; platform-specific containment checks | R/M: existing writes benefit; C: capture/promotion; D/T: — | Existing writes benefit without enabling new UI writes | Safety improvement with cooperative-filesystem limits; no distributed/hostile-writer guarantee |
| Read-only semantic eligibility/inspection additions; VB-121/141/B | E | Eligible-path filtering, Query basis, immutable storage error categories and validated inspection target; no schema/rank/signature change | R/M/C: dependent operations; D/T: — | Indirect diagnostics/reliability | Mention actionable diagnostics, not a new index format or faster ranker |
| MCP HTTP smoke fix; `366d972` | E | Platform-neutral synthetic unreadable-note assertions; stricter full-payload checks retained; supporting tests added | No product surface; CI/runtime harness | Better validation, no new feature | Validation note, not product headline |
| Workflow/harness; `81ed6b3`, `947424a` | E | Repository guidance/skill and bounded separate-review workflow | No product surface | No | Developer-only, normally omit |
| Roadmap, release evidence, encoding and status closeouts; `9447cef`, `4b3c9f5`, `2b8fe4c`, `95b1a0d` and contract commits above | F | Planning/status/evidence and README repair; no runtime feature by themselves | No new surface | Documentation only | Release notes must be reconstructed from source, not copied from incomplete Unreleased list |

There is **no TrueNAS package, Dockerfile, Compose, pyproject or GitHub workflow delta in this
range**. The four MCP form fields and MCP create/append parity were already in v1.3.0 source.
Their missing upstream delivery is outstanding productization, not a newly implemented HEAD
feature. Existing dashboard Relationships and REST/MCP `note_links`/`note_backlinks` were also
already in v1.3.0; neither the dashboard search module nor those routers gained a new relationship
workflow in this range. Runtime relationship internals did gain additive methods and verification.

Source anchors: [frontmatter](../app/services/frontmatter.py),
[Markdown links](../app/services/markdown_links.py), [relationships](../app/services/relationships.py),
[Query](../app/services/knowledge_query.py), [Capture](../app/services/capture.py),
[Promotion](../app/services/promotion.py), [Hygiene](../app/services/knowledge_hygiene.py),
[CLI](../app/cli.py), [REST registration](../app/main.py), [MCP registration](../app/mcp_server.py),
[dashboard shell](../app/ui/index.html) and [session implementation](../app/core/ui_session.py).

## 3. Personas and practical adoption

| Persona | What v1.3.0 already provides | Meaningful new HEAD capability | Completed capability effectively invisible | Adoption gap / release response |
|---|---|---|---|---|
| Community Apps installer | Catalog install, Host Path vault, persistent derived-data option, health, Portal/dashboard; image has read MCP and opt-in create/append | Future image supplies persistent browser sessions and Hygiene tool/API; CLI workflows are packaged | Query domain, Capture/Promotion and two-dialect domain relationships | Upstream form lacks supported MCP controls; deliver them without requiring YAML. Explain install versus image versus package versions |
| Dashboard browser user | Overview, literal/semantic Search, note viewer, wikilink Relationships, API/Integration/About; tab API-key session | Cookie restore across reload/reopen within lifetime and unchanged current key | Hygiene has no screen; Query/Capture/Promotion have no panels | Add read-only Hygiene as the single new significant workflow; document session change |
| MCP/AI client user | Seven read-only tools and note Resources; HTTP default off; create/append opt-in | Eighth read-only tool, structured Hygiene coverage/reasons and canonical paths | No Knowledge Query, Capture or Promotion tools; no named spaces | TrueNAS config delivery and short client examples; preserve Bearer/allowlists, no new write tools |
| CLI/power user | Search/related/status/index maintenance | Combined Query subset; portable Capture; explicit review/promotion; Hygiene JSON | Full Query metadata/relationship predicates remain domain-only | Good examples and limits, review decision schema, stopped-service index-write procedure; no need to wait for UI |
| Docker/self-hosted user | Single-container/local model/SQLite, configurable HTTP MCP, existing API/CLI/dashboard | All reachable HEAD capabilities with existing mounts/defaults | Named federation and advanced domain-only predicates | Accurate docs, unchanged configuration and index verification; use exact image, avoid unsupported concurrency |

Hygiene does not require an MCP client or CLI for the proposed browser experience. Conversely,
Capture/Promotion remain worthwhile expert capabilities without forcing a risky browser write
workflow into the release. Do not describe the entire backend as usable by every persona.

## 4. Version decision and minimum meaningful release

| Choice | Assessment |
|---|---|
| v1.3.1 | Reject: multiple additive CLI commands, a REST operation, an MCP tool, browser authentication change and proposed new workflow exceed a maintenance-patch story |
| **v1.4.0** | **Recommend:** additive capabilities with existing REST aliases/IDs, auth clients, single-root defaults and data formats preserved; normal package revision follows image publication |
| Wait for a larger release | Reject: D–G/VB-152 and browser writes add substantial risk while withholding already useful diagnostics; architecture completion is not the product threshold |

No source evidence requires a major version. Browser session persistence changes credential
handling and deserves security review and user documentation, but Bearer integrations remain
compatible. Opt-in future named config fails closed; unsupported configurations are not advertised
as compatible multi-space deployments. Catalog package numbering is independent of application
SemVer and must follow the then-current upstream revision rather than resetting the local copy.

Publish only when all five objective criteria hold:

1. A normal browser user can unlock, run one explicit Hygiene scan, understand at least broken-link,
   metadata and isolation results, and locate affected canonical notes without a shell. Complete,
   empty, partial, truncated and unavailable evidence are visibly different. No mutations occur.
2. MCP clients can invoke the same diagnostics from the exact release image. Source/render-tested
   first-class TrueNAS settings and an upstream-ready change are prepared; install/edit behavior is
   proved wherever a supported preview permits. Catalog delivery remains a later gate.
3. Existing dashboard read/search/relationships and API/MCP create/append compatibility pass; no
   private data, raw credential or diagnostic content leaks through UI rendering/logs/evidence.
4. The RC digest passes real TrueNAS restart/persistence, existing-vault upgrade simulation,
   credential/settings and denial/recovery scenarios. Unresolved real catalog-only tests are named
   and scheduled rather than represented by Docker/custom-YAML results.
5. Documentation tells each persona what is actually available, how to use it, and how to recover
   derived data. Release notes lead with a browser workflow rather than internal slices.

Thus the minimum bundle requires a significant Dashboard workflow, MCP diagnostic capability and
lifecycle reliability evidence. It does not require UI for every CLI command or new operator-health
endpoints. Existing public Overview plus Hygiene evidence suffice for diagnostics.

## 5. Hygiene Dashboard: MUST for this release

This is a new separately accepted task, not a retroactive requirement for completed VB-142/M15.
It is the smallest work that gives ordinary dashboard users a clear reason to upgrade.

### Contract reuse and proposed screen

Reuse [POST `/api/v1/knowledge/hygiene/scan`](../app/api/knowledge_hygiene.py) unchanged, operation
ID `scanKnowledgeHygieneV1`. [Transport models](../app/knowledge_hygiene_transport.py) already expose
`findings`, `scan`, `candidates`, `derived_index` and `findings_truncated`. There is no need for a
new backend endpoint, result schema, scan job, repair API, persistence or progress API.

Add Hygiene navigation and one panel with Run scan / Refresh scan. Do not scan on page load,
unlock, navigation or a timer. Initial request can explicitly use all four groups, finding limit
500, duplicate source limit 0, semantic candidates false and derived inspection false. This keeps
the first screen focused on live Markdown diagnostics; optional duplicate/index controls can wait.
Still validate and faithfully render every coverage/status field returned by the contract.

Show scan state/reasons and inspected/eligible/unavailable counts; group the **returned** finding
counts by kind, then show each affected path, bounded related paths, category, evidence source and
the fixed safe evidence fields. Preserve backend order within groups. Counts are returned findings,
not total defects across the vault; `findings_truncated` means additional findings were omitted.
Do not invent severity, repair priority, semantic duplicate confidence or missing-heading checks.
Pathless index findings must have a vault-level label, not a fabricated filename. Optional opening
of the existing note viewer is SHOULD, not required; a readable/selectable canonical path is enough.

States: locked/checking session; idle; loading; complete with findings; complete empty; partial with
or without findings and reasons; truncated; unavailable (503/network); malformed response;
authentication failure; rate limit with retry delay; rejected request/internal failure. A partial
empty result must never say the vault is clean. `scan.state` is independent of candidate coverage
and derived-index inspection status. An unavailable candidate/index subresult is not necessarily a
failed scan. A timestamp may label when the browser received the result, never index freshness.

### Authentication, privacy and accessibility

Reuse `authenticatedFetch` in [app.js](../app/ui/assets/app.js): same-origin credentials and
`X-VaultBridge-UI-Request: 1`. [Protected dependencies](../app/api/dependencies.py) already accept
the valid cookie/marker combination; `tests/test_api_knowledge_hygiene.py` explicitly covers it.
Do not reintroduce API-key browser storage or change the auth dependency. The cookie is HttpOnly,
SameSite Strict, Secure on direct HTTPS, signed from the current key; GET restore renews its
seven-day lifetime. Current-key rotation invalidates old sessions even with previous-key Bearer
overlap. Logout/401 must abort/invalidate requests and clear all protected Hygiene data. Requests
returning after logout or a newer scan must not repopulate the panel. Retain session for 429/503.

Render values with text nodes/textContent only. Findings intentionally omit raw targets, alias
values, labels, Markdown, arbitrary metadata, signatures and SQL. Canonical paths are still
protected user data: no URLs/history/localStorage/sessionStorage, console logging or analytics.
Keep existing same-origin CSP and local assets. Add no repair links disguised as diagnostics.

Use semantic headings/lists or properly headed tables, labelled buttons, aria-live status text,
aria-busy during loading, visible focus, keyboard operation, long-path wrapping, small-screen
reflow, non-color state labels and reduced-motion-compatible behavior. Do not move focus on a
routine response; make errors/status discoverable to screen readers. Include literal HTML-like
synthetic path/category text in inert-rendering E2E coverage.

### Effort, files and performance risks

**MEDIUM**, with relatively low mutation risk but meaningful auth/stale-response and truthfulness
risk. Expected areas: `app/ui/index.html`, `app/ui/assets/app.js`, `app/ui/assets/app.css`, new
`app/ui/assets/hygiene.js`, explicit asset route in `app/ui/router.py`, `tests/test_ui.py`,
`tests/e2e/test_dashboard.py` (or focused new E2E file) and fixture wiring in
`tests/e2e/conftest.py` as needed. API/domain/MCP changes are out of scope. A new module must have
an explicit served asset path; there is no static catch-all to rely on.

The synchronous REST scan reads live Markdown in FastAPI's worker execution; it is not an
asynchronous job. Disable duplicate submissions; use AbortController/generation guards for UI
staleness. Browser abort does **not** promise cancellation of server filesystem work. Existing
Hygiene bounds are 10,000 eligible paths, 500 returned findings, 10 related paths and optional
20 duplicate sources/5 candidates per source. These are not a hard wall-clock/aggregate-byte or
all-filesystem-entry ceiling; the legacy hygiene walker and eager relationship derivation do not
inherit Slice C's federation budgets. Do not advertise instantaneous scans or hard total-work
bounds. Show loading without percentages/ETA, avoid polling, and measure a representative synthetic
NAS vault during the RC. If that fails practical responsiveness, stop and scope a separate owner
performance fix; do not conceal limits with frontend truncation or broaden into federation work.

Required tests cover every state above, cookie-only fetch, logout/401/stale response clearing,
navigation away/back, bounded grouping/count labels, malformed evidence, independent coverage,
keyboard/focus, narrow viewport and inert rendering. Existing Hygiene REST tests establish contract
reuse; existing Search/Relationships/session E2E must remain green. Real-browser acceptance and
TrueNAS RC observation complement synthetic E2E.

## 6. Capture and Promotion productization

Current source provides fixed-path inbox/draft creation, exact-byte retries, explicit review and
decision/apply, source/destination hashes, attributable destination Markdown and post-commit index
evidence. Promotion leaves the original capture intact. Advisory candidates never choose a
destination. CLI workers can write derived state and must not compete with a serving indexer;
the documented Capture stopped-service procedure also belongs in Promotion instructions.

A normal dashboard user lacks intake entry, an inbox browser, source review, destination inspection,
approved-content preview and conflict/retry handling. Existing REST/MCP create/append tools do not
constitute Capture/Promotion adapters or implement their approval/provenance contracts.

| Option | Implementation / security / UX cost | Assessment |
|---|---|---|
| A: ship current CLI and document | SMALL documentation work; retains local filesystem trust and explicit JSON decisions. Explain bounds, hashes, idempotency, hard-link requirement, commit_unknown and stopped-service indexing | Best for this release; honest power-user value with no new remote mutation surface |
| B: REST/MCP without Dashboard | LARGE, security-sensitive contracts for authenticated review/write, bounded input, explicit action/destination/content approval, current source/destination preconditions, retry/uncertainty, post-commit indexing and privacy; MCP write opt-in alone is not human approval | Useful later, but technical-only and disproportionate for the browser release threshold |
| C: browser workflow | HIGH-RISK relative to release scope: B plus inbox navigation, editable proposal/preview, deliberate commit affordance, destination selection, stale approvals/conflicts, focus/accessibility and extensive failure E2E | Material release delay and mutation risk; separate design/PR series |

**Recommendation: DEFER DASHBOARD/ADAPTER WORK TO A LATER RELEASE.** Ship the existing CLI as-is
with improved documentation. Do not introduce a write endpoint as a convenience prerequisite for
Hygiene. Do not automatically promote captures, choose targets, hide uncertain commits, or retry
after a potentially relocated artifact. CLI availability does not prove the filesystem supports
all atomic primitives; include disposable Capture/Promotion checks on the intended TrueNAS storage.

## 7. Multi-space A–C activation and release safety

[Settings.require_legacy_composition](../app/core/config.py) rejects any non-None
`KNOWLEDGE_SPACES_JSON` with `SpaceError("invalid_configuration", reason="named_serving_unsupported")`.
Malformed, empty or oversized JSON already fails strict parsing. Syntactically valid named JSON
still fails public composition; it is not an activation switch users can use today.

The guard is called before constructing public owners in `create_app`, MCP server construction,
MCP HTTP construction, CLI entry and CLI run helpers. The dashboard shares the guarded FastAPI
application. [Guard tests](../tests/test_knowledge_spaces.py) instrument these factories to fail if
owners are reached. [Private composition](../app/services/space_owners.py) is an explicit factory,
not called by public startup; its semantic facade does not search or initialize storage and its
lifecycle handle creates no executor. [SpaceOperations](../app/services/space_operations.py) and
the scoped federation modules are internal entry points. B's capture/promotion owners are structural
references; scoped execution and bound indexing remain gated. C is nonsemantic only.

**A–C can safely ship while disabled, conditional on regression/security gates passing.** Internal
code still enlarges the review/regression surface and must not be exempted from tests. Users cannot
accidentally activate named serving through supported adapters; supplying the future config can
instead prevent startup. Document that it is unsupported and omit it from install examples/forms.
Legacy absence preserves existing single-root construction and unavailable-root behavior.

**Must VB-151 be completed before v1.4.0? No.** D (precise semantic federation), E (binding/resource
lifecycle), F (scoped write provenance), G (integration hardening), and VB-152 (unscoped adapters)
are not release blockers while the activation boundary remains intact. Stop Slice D work until
after this product release. Do not advertise multiple vaults, named policies, cross-space search
or permission-aware remote serving. An optional developer-only note may say groundwork remains
disabled; omit it from user-visible headlines.

## 8. TrueNAS configuration and delivery gaps

The [checked-in questions](../ix-dev/community/vaultbridge/questions.yaml),
[template](../ix-dev/community/vaultbridge/templates/docker-compose.yaml), fixtures and
[package tests](../tests/test_truenas_package.py) already implement all four fields:

| Runtime setting | Local form / template mapping | Default and representation | Remaining delivery |
|---|---|---|---|
| `MCP_HTTP_ENABLED` | `mcp.http_enabled`, Enable MCP HTTP | Boolean false; same Web Port and `/mcp` | Port to current upstream source and validate install/edit |
| `MCP_WRITE_ENABLED` | `mcp.write_enabled`, Enable MCP Writes | Boolean false; conditional display when HTTP enabled; no REST-write or mount-permission change | Preserve opt-in through upgrade; test writes absent/present |
| `MCP_HTTP_ALLOWED_HOSTS` | `mcp.allowed_hosts` | Required comma-separated ordinary string; loopback Host defaults with port wildcards | Explain actual external Host; prove allowed/rejected Host |
| `MCP_HTTP_ALLOWED_ORIGINS` | `mcp.allowed_origins` | Required comma-separated ordinary string; loopback HTTP Origin defaults | Prove present Origin validation and no-Origin client behavior |

Secrets are `vaultbridge.api_key` and `api_key_previous`, both `private: true`; they must remain
masked in forms and absent from retained logs/render output. Allowlists are not authentication
secrets, so ordinary visible fields are appropriate. Neither runtime parsing nor local string
schema constitutes a guarantee that an operator cannot enter `*`; help text discourages that and
tests must use explicit bounded hosts/origins. Do not add speculative allowlist-policy changes.
Fresh installs and keys with no prior MCP value retain HTTP/write false and loopback allowlists;
defaults must not replace an existing installation's legacy MCP settings. Loopback defaults
intentionally reject remote MCP until the operator configures the real Host.

**Classification: MUST for the coordinated v1.4.0 product delivery.** Upstream-ready, validated
package work blocks RC preparation/stable readiness; actual upstream landing follows stable image
verification and blocks claiming the TrueNAS MCP productization delivered. Maintainer merge timing
cannot be a prerequisite for publishing an image the catalog needs. Announce application and
catalog delivery separately if they occur on different dates.

Update the current upstream definition, not the historical local directory wholesale: preserve CDN
icon, port 30491, current library/hash conventions and accepted schema/migrations. A new catalog
package revision and app_version/image 1.4.0 alignment will be needed; choose the next available
package revision at submission, not an assumed fixed 1.0.3. No application data migration follows
from the form, but configuration carry-forward is required as specified below. Local fixtures all
contain `mcp`; passing them does not prove an old-value upgrade. Do not infer that private secret
masking means lost secret persistence.

### Verified upstream baseline and migration mechanism boundary

The repair inspected upstream Git objects at recorded baseline commit
`f39f282e8a58fe57372ca0f005123c84b2ecee31`, not the historical package copy in this repository:

- [VaultBridge template](https://github.com/truenas/apps/blob/f39f282e8a58fe57372ca0f005123c84b2ecee31/ix-dev/community/vaultbridge/templates/docker-compose.yaml#L10-L17)
  forwards `vaultbridge.additional_envs` through the user-environment path.
- [VaultBridge questions](https://github.com/truenas/apps/blob/f39f282e8a58fe57372ca0f005123c84b2ecee31/ix-dev/community/vaultbridge/questions.yaml#L52-L72)
  expose that list with name/value entries and have no dedicated MCP group or fields.
- [base_v2_3_11 environment renderer](https://github.com/truenas/apps/blob/f39f282e8a58fe57372ca0f005123c84b2ecee31/ix-dev/community/vaultbridge/templates/library/base_v2_3_11/environment.py#L97-L119)
  rejects a user variable whose name is already developer-defined. Adding dedicated MCP environment
  definitions while retaining matching legacy entries therefore fails rendering; it is not an
  override mechanism. An in-memory probe of this exact renderer confirmed rejection for each of
  the four MCP names. This confirms the collision, not a working migration.
- [Upstream application metadata](https://github.com/truenas/apps/blob/f39f282e8a58fe57372ca0f005123c84b2ecee31/ix-dev/community/vaultbridge/app.yaml)
  and [image values](https://github.com/truenas/apps/blob/f39f282e8a58fe57372ca0f005123c84b2ecee31/ix-dev/community/vaultbridge/ix_values.yaml)
  identify package 1.0.2, app/image 1.3.0 and library 2.3.11.

[Contributor migration guidance](https://github.com/truenas/apps/blob/f39f282e8a58fe57372ca0f005123c84b2ecee31/CONTRIBUTIONS.md#updates-and-migrations)
documents version-bounded `app_migrations.yaml` and Python value-transformation scripts, with an
[existing migration declaration](https://github.com/truenas/apps/blob/f39f282e8a58fe57372ca0f005123c84b2ecee31/ix-dev/stable/storj/app_migrations.yaml)
and [script example](https://github.com/truenas/apps/blob/f39f282e8a58fe57372ca0f005123c84b2ecee31/ix-dev/stable/storj/migrations/auth_token_rm_migration).
VaultBridge at this baseline has neither a migration declaration nor migration scripts. This
establishes an upstream migration facility, not its proven invocation/order for the proposed
VaultBridge change. **P2 must first determine and demonstrate the supported upgrade normalization
or migration hook**, version applicability, access to prior saved values before target defaults,
clear failure propagation, and edit-time validation before render. Do not invent a migration API
or assume template-side mutation persists saved form values. The acceptance contract is behavior,
independent of which supported hook P2 proves suitable.

### Required per-key legacy MCP carry-forward policy

Only the following four exact, case-sensitive names become reserved developer-owned MCP settings:

| Legacy `additional_envs` name | New dedicated representation | Default only when no saved dedicated or legacy value exists |
|---|---|---|
| `MCP_HTTP_ENABLED` | `mcp.http_enabled` | `false` |
| `MCP_WRITE_ENABLED` | `mcp.write_enabled` | `false` |
| `MCP_HTTP_ALLOWED_HOSTS` | `mcp.allowed_hosts` | `127.0.0.1:*,localhost:*,[::1]:*` |
| `MCP_HTTP_ALLOWED_ORIGINS` | `mcp.allowed_origins` | `http://127.0.0.1:*,http://localhost:*,http://[::1]:*` |

Apply the following independently to each key using the **prior saved configuration**, preserving
the distinction between explicitly saved dedicated values and newly injected schema defaults:

- **Legacy only:** carry the old value into the dedicated field, then consume only that exact
  legacy entry after all migration validation succeeds. Preserve enabled/disabled behavior,
  including an explicit false value; hidden write fields are not permission to discard a value.
  Boolean conversion must preserve the existing runtime's accepted meaning, never generic string
  truthiness. Preserve Host/Origin allowlist semantics without broadening, replacing or silently
  sanitizing them. A value that cannot be represented safely must produce a clear pre-render
  configuration error with no silent fallback or partial saved migration.
- **No legacy value:** preserve an already saved dedicated value. Only when neither representation
  has a prior value may the documented default be applied. Target-schema defaults must not overwrite
  legacy values or be misidentified as prior user choices.
- **Both saved dedicated and legacy values:** reject before container render with a clear
  upgrade/configuration error identifying the reserved name and the two conflicting representations,
  without logging values or credentials. This policy rejects even equal values; it uses no silent
  precedence. The operator must explicitly resolve the conflict. If the supported hook cannot
  distinguish saved values from injected defaults, P2 must resolve that provenance problem or stop;
  comparing a value to the default is not proof of its origin.
- **Unrelated `additional_envs`:** preserve entries, names, values and ordering unchanged and in
  user control. Migration touches only the four names above; it must not empty or rebuild the whole
  list, discard other variables, or weaken the base renderer's collision protection.

After successful migration, first-class fields are the sole owners of these four environment
names. Assert that none remains in forwarded `additional_envs`, each is rendered exactly once,
and the resulting container environment preserves the prior meaning. Re-running normalization is
idempotent. Later edits that reintroduce a reserved legacy entry alongside dedicated settings must
receive the same clear pre-render conflict error. Duplicate legacy entries for one reserved name
or invalid values must fail clearly before render rather than using last-value-wins. Defaults
alone and manual deletion of legacy entries are not migration solutions.

This compatibility is **BLOCKING BEFORE TRUENAS CATALOG UPDATE**. Because first-class MCP delivery
is coordinated scope, P2 fixtures and an RC/custom-package upgrade exercising the actual supported
migration path must pass before stable/catalog submission, including the mandatory non-default
legacy Host/Origin baseline and before/after Origin enforcement checks in section 10. A custom image
receiving a hand-merged environment is not this proof. If no supported candidate-package path is
available, the live gate remains unverified; do not mark coordinated form delivery ready or silently
waive the gate. The upstream catalog merge itself remains after stable image publication, as in
section 11.

Other settings: `KNOWLEDGE_SPACES_JSON` is the only added Settings environment name in the actual
range and must stay out of the form while serving is blocked. Hygiene request knobs and
Capture/Promotion inputs are operation parameters, not install settings. Existing advanced model,
chunk, embed-batch, ONNX arena, rate-limit and note-size settings are not newly added in this range;
their omission from the curated form is not a reason to expose every environment variable now.
Watcher, UID/GID, storage, port/resources and current/previous key controls already exist in local
source. Keep the package focused on settings a normal operator needs.

## 9. VB-082 lifecycle debt reassessment

Historical PASS below means retained sanitized runtime evidence, generally custom YAML v1.1.0 on
TrueNAS 25.10.6; OPERATOR-CONFIRMED means recorded observation without equivalent raw evidence.
Neither is a fresh v1.4.0 PASS. S=before stable RC evidence; C=before catalog submission/update;
P=after catalog publication on the actual generated upgrade. The full VB-082 task remains partial
until its catalog-only acceptance evidence is resolved.

| Lifecycle check | Current repository evidence | Status now | Blocker | Required next action |
|---|---|---|---|---|
| Fresh Community App install / Portal | 2026-09-17 catalog install/form/masked keys/Portal observed | OPERATOR-CONFIRMED PASS, historical | S for RC runtime; P for new generated package | Fresh disposable RC install; repeat actual catalog install after delivery |
| Existing Host Path vault | Historical captured custom runtime plus observed catalog mount/visibility | PASS / OPERATOR-CONFIRMED, historical | S | Reuse synthetic existing vault, compare relative-path hashes/UID/GID/ACL before/after |
| ixVolume derived-data persistence | Catalog ixVolume configuration observed; generic restart/persistence operator-confirmed | Current exact storage survival not retained | S/C | Record actual mount identity, stopped DB inventory and model-cache survival across redeploy; test existing catalog state now |
| Stop/start | Restart/persistence operator-confirmed, not separate retained scenario | REQUIRES LIVE VERIFICATION for candidate | S | Explicit stop then start; restore readiness, note search and unchanged vault hashes |
| App restart | Historical operator confirmation | REQUIRES LIVE VERIFICATION for candidate | S | Restart candidate; prove persistent data and UI/MCP recovery |
| TrueNAS host reboot | No retained distinct reboot result | REQUIRES LIVE VERIFICATION | S on disposable host | One controlled reboot proves app autostart/storage mounting; never reboot shared production for this plan |
| Edit-form persistence | Install form observed; stored form reopen not proved | REQUIRES LIVE VERIFICATION | C; repeat P | Reopen 1.0.2 form now, edit unrelated resources, retain keys/storage/port; future form also retain MCP values |
| API-key rotation | Captured custom overlap/removal PASS; catalog migration observed | PASS historical, new UI session interaction unproved | S/C | Current+previous 200; after removal old 401/new 200; cookie invalidates on current-key change; no key leaks |
| Port edit / persistence | Custom runtime PASS; strict authenticated UI read after change not retained | PASS runtime only; catalog edit still open | S/C | New port health/unlock/read/MCP; old port closed; reopen form; occupied-port failure/recovery |
| Permission failure / recovery | Dedicated POSIX dataset mode denial/restoration, hashes/ownership retained | PASS historical | S | Repeat isolated denial; liveness survives, readiness fails, no ACL auto-repair; restore and verify safe recovery |
| Uninstall with external vault | Captured custom Host Path vault/data hash preservation | PASS custom only | S for candidate ownership; C/P catalog delete | Delete only disposable app and verify external vault bytes/metadata untouched |
| ixVolume retain | Configuration observed, delete choice not exercised | REQUIRES LIVE VERIFICATION | C on existing catalog; P new package | Select retain, prove derived dataset remains; record actual UI wording and scope |
| ixVolume remove | No executed delete outcome | REQUIRES LIVE VERIFICATION | C on existing catalog; P new package | Separate disposable installation, remove app-managed volume, prove only derived dataset removed and external vault survives |
| Upgrade from real catalog baseline | Repo records current 1.0.2/image 1.3.0; old ledger says no prior package | ELIGIBLE BASELINE EXISTS; NOT TESTED | S for runtime/data simulation; P for real package upgrade | Install/retain actual 1.0.2, warm data; test copied state against RC, then actual generated catalog update later |
| Legacy additional_env MCP -> dedicated MCP field upgrade | Upstream forwarding/collision confirmed; no carry-forward execution evidence | NOT YET VERIFIED | S for supported RC/custom-package migration; C; repeat P on generated catalog | Fixture + live package upgrade validation from 1.0.2/image 1.3.0: mandatory enabled HTTP and saved non-default legacy Host/Origin values; preserved dedicated Origin field/runtime environment, accepted/rejected Origin behavior, unrelated entries and no reserved duplicates; repeat actual catalog upgrade |
| Rollback feasibility | Historical block because no prior catalog revision | Historical rationale obsolete for future update; execution pending | S recovery plan, C documented limits, P real rollback | Preserve installed 1.0.2 revision and snapshots; test runtime restore on copies now, then available catalog rollback; document exact storage/snapshot scope |

Package 1.0.2/image 1.3.0 provides a valid supported prior state for the future package. It cannot
prove a future catalog update before that package exists. Do not retain "UNSUPPORTED / NO VALID
PRIOR PACKAGE STATE" as a current blocker explanation. Rollback also requires actual retained
installed revision/history and the current platform's available mechanism, not simply two version
numbers in a document. Never promise a catalog rollback that restores an externally mounted vault.

## 10. Practical real TrueNAS canary

### Fixtures, safety and evidence

Use a disposable TrueNAS host/VM with supported Apps and Linux/amd64, isolated app names, ports,
datasets and two newly generated non-production keys. Record full TrueNAS build, timestamp, package
revision, source SHA, exact tag plus OCI index/runtime digest and mount type. Keep a baseline
Community 1.0.2 installation intact for the later upgrade, and use a separate RC custom app with
copied fixtures. Never point two running indexers at the same `/data` or test with private notes.

Prepare an existing persistent synthetic vault before installation and an equivalent fresh-install
copy. Use plain UTF-8 Markdown under `Notes/`, `Projects/`, `Archive/` and `Inbox/Captures/`:

- `Notes/Alpha.md` and `Notes/Beta.md`: unique astronomy/database text for literal/semantic checks,
  valid wikilinks both ways, and one contained inline Markdown link.
- `Notes/Broken.md`: one missing wikilink and one missing relative Markdown target.
- `Notes/Isolated.md`: nonempty ordinary curated note with no resolved links.
- `Notes/BadMetadata.md`: duplicate YAML key; `Notes/Empty.md`: valid metadata, whitespace body.
- Two valid notes with the same alias plus one repeated alias and an empty portable tag, to show
  separate diagnostic kinds without exposing values.
- A valid inbox capture with no links: verify it is not reported isolated merely for being intake.
  Optionally add equal filename stems in separate folders for opt-in exact-title candidates.

Keep a SHA-256 manifest of relative filenames/bytes and numeric ownership/modes. Build baseline
derived SQLite/model cache with the v1.3.0 image and cleanly stop before snapshot/copy. Back up the
test app configuration without retaining plaintext credentials. For database verification inspect
stopped copies; no filesystem-immutable CLI check against active WAL/SHM is expected to succeed.
Separate default-off watcher and enabled-watcher runs. MCP writes stay false except the explicit
write-parity case; Capture/Promotion CLI is tested offline on another disposable copy.
Retain at least one working real Community package 1.0.2/image 1.3.0 baseline with all three of
`MCP_HTTP_ENABLED=true`, `MCP_HTTP_ALLOWED_HOSTS` and `MCP_HTTP_ALLOWED_ORIGINS` explicitly saved
through `vaultbridge.additional_envs`, plus a harmless unrelated user variable. Both allowlists
must be non-default. Use the disposable canary's explicit reachable Host and port (for example,
`truenas-canary.example.test:<canary-port>`, substituted for the actual test address), and set the
legacy Origin value to exactly `https://mcp-canary.example.test`. This reserved test-domain Origin
is a deliberate request header, not a service to contact; send requests to the real disposable
canary endpoint with valid Host and Bearer authentication. Do not substitute the loopback Origin
default for this proof. Retain sanitized old-form/saved-value evidence showing the explicit legacy
Origin entry **before upgrade**, plus selected runtime-environment evidence and a working client.
Retain the existing legacy write-setting coverage across isolated fixtures and the mandatory
opt-in write-parity test in section 10A.6; this Origin requirement does not weaken either.

**Mandatory live Origin enforcement pair, before and after each upgrade:** send a valid MCP HTTP
initialize/read sequence with `Origin: https://mcp-canary.example.test`; require successful
initialization and a read-only tool call. Send an otherwise equivalent authenticated MCP request
with `Origin: https://mcp-denied.example.test`; require Origin rejection (HTTP 403), not an auth,
Host or malformed-protocol failure. Hold Host, endpoint, credentials and valid request shape
constant so Origin is the differing input. Record bounded status/protocol assertions for both
requests on old and new packages; no-Origin success is not a substitute. The accepted/rejected
results must remain consistent across migration. A changed enforcement outcome fails the gate,
even if the app starts and strings appear copied. This pair and non-default saved baseline are
mandatory for both the pre-stable candidate-package upgrade and the separate post-catalog upgrade.
A second no-legacy baseline proves defaults; it must not stand in for either legacy upgrade.

Evidence types: **UI** means sanitized screenshots plus operator observation; **CMD** means
status-only requests, selected safe image/user/mount metadata, relative hashes, test-client
assertions and bounded logs; **BOTH** requires both. Never retain full container environment,
Authorization headers, cookies, keys, private URLs, or a resolved secret-bearing Compose file.

### A. Pre-release RC / custom-image validation

1. **Install (BOTH).** Resolve the exact RC image anonymously and select its digest in a supported
   custom app/rendered Compose. Mirror approved non-root UID/GID, capabilities, liveness check,
   Host Path `/vault`, persistent `/data`, one published port and intended limits. Validate ixVolume
   through a supported UI route if available; if YAML cannot prove app-managed ixVolume semantics,
   mark that portion catalog-only and test current 1.0.2 separately. Do not label a YAML app as a
   Community wizard test.
2. **Startup/semantic readiness (CMD).** `/health/live` is 200 during model/index initialization;
   poll `/health/ready` with a bounded deadline chosen for the host, observe 503 then 200 when
   appropriate; record `/health` safe counts/state. For a warmed upgrade, compatible data may be
   immediately usable rather than undergoing a full rebuild. No mandatory model redownload.
3. **Browser workflow (BOTH).** Open `/ui/`, see Overview while locked, reject wrong key, unlock
   with current key, literal search unique text, semantic search the other topic, read note, inspect
   outgoing/backlinks. Reload/reopen within session lifetime; logout and confirm protected data
   clears. Check headers and keyboard/narrow-view behavior. Relationships remain existing wikilinks.
4. **Hygiene (BOTH).** Navigate, run explicit scan, match fixture kinds and canonical paths, see
   counts/reasons and read-only advice. Compare REST result with MCP/CLI on equivalent fixtures.
   Confirm vault manifest unchanged. Exercise complete empty on a separate well-linked valid small
   vault; partial via an unreadable disposable note; unavailable via an inaccessible disposable
   vault. Malformed/stale/rate-limit states can be automated E2E evidence rather than forced NAS
   damage. Observe a larger synthetic vault scan for practical responsiveness; no private corpus.
5. **MCP client (CMD plus optional client UI).** Configure explicit allowed Host/Origin for test
   address, current-key Bearer and exact `/mcp`. Use an official protocol client as in
   `scripts/smoke_mcp_http.py`. Check initialization, all eight read-only tool names, note Resource,
   list/read/literal/related/duplicate/links/backlinks and Hygiene. Assert wrong/missing credential,
   disallowed Host and present Origin reject; absence of Origin is valid with permitted Host/auth.
   HTTP disabled must leave `/mcp` unavailable. Cookie sessions must not become MCP authentication.
6. **Optional feature enabled, mandatory validation if offered (CMD).** With MCP writes deliberately
   true, verify exactly two additional tools, create one disposable note without overwrite, append
   with dedupe retry and targeted search visibility, then turn writes off. Compare manifests with
   only the intended additions. The release retains this supported opt-in, so this case must pass
   before stable even though ordinary canary use remains read-only. Read-only vault mount must fail
   writes without authoritative changes. This is not a Capture/Promotion remote workflow.
7. **Offline CLI (CMD).** Stop serving the copied state, run Query tag/literal and persisted semantic
   cases, Hygiene JSON, Capture exact retry/conflict, Promotion review/create/append/retry and source
   retention. Confirm destination-only intended mutations and independent index outcome. Restart
   server after the offline worker exits. Verify POSIX hard-link/write-lock support on selected
   storage; do not run index-writing CLI inside a live serving container.
8. **Restart/lifecycle (BOTH).** Stop/start and app restart separately. Compare vault hashes, data
   mount identity, model-cache inventory and search results; SQLite bytes/counts may change normally
   during startup synchronization, so byte equality of derived state is not required. Change port,
   then unlock/read and connect MCP at the new port; check old port, allowlists and retained values.
   Rotate keys with bounded overlap/removal and cookie invalidation. Toggle watcher and edit a
   synthetic note externally: enabled eventually refreshes; disabled does not claim automatic
   freshness, startup reconciliation recovers it. Exercise collision and isolated permission
   failure/restoration without changing shared ACLs.
9. **Host reboot (BOTH).** On the disposable host only, perform one controlled reboot with app
   autostart enabled. Confirm mounts, app health, readiness, UI/MCP and data after boot. This checks
   a NAS-specific lifecycle boundary not proved by container restart.
10. **Upgrade/recovery simulation (CMD/BOTH).** Use a cleanly stopped copy of real 1.0.2/image 1.3.0
    test state and preserved configuration. Run RC over the copy; verify Markdown, compatible index,
    cached model, keys and behavior. Stop RC and run old image over a copied/snapshotted state to
    establish recovery, including readable new portable artifacts. Never call this a catalog
    upgrade/rollback. If old derived data cannot be reused, test rebuild on a disposable derived
    copy and document the exact limitation before stable.
    **Also required before stable for coordinated MCP delivery:** use a supported RC/custom-package
    upgrade path to exercise the P2 normalization/migration logic over saved 1.0.2/image 1.3.0 values
    containing the mandatory saved non-default legacy Host/Origin baseline above, in section 10B's
    order. Require successful render/startup, the preserved non-default dedicated Origin field and
    runtime environment, removal of its legacy entry, no reserved duplicate reaching render,
    unchanged unrelated entries, and the mandatory accepted/rejected Origin pair before and after
    upgrade. Prove conflict rejection, preserved connectivity and other saved form values where
    exposed. Merely changing the image or manually preconverting values is insufficient.
    If a supported path cannot execute this, record NOT YET VERIFIED and stop that delivery gate.
11. **Uninstall (BOTH).** Delete only the RC disposable app and verify external vault and external
    host-path data ownership/bytes remain. Separately run actual current-catalog ixVolume retain
    and remove cases on two sacrificial installs before upstream submission. Preserve the baseline
    instance reserved for upgrade testing. Verify dataset existence/removal explicitly; the dialog
    alone is insufficient evidence.

### B. Post-publication Community App upgrade validation

After stable exact-image checks and upstream review/generation, refresh the catalog and record the
actual new package/image/digest. On the retained 1.0.2 synthetic installation:

1. **BOTH:** before upgrade, configure MCP through the actual 1.0.2/image 1.3.0
   `vaultbridge.additional_envs` form. Mandatory: `MCP_HTTP_ENABLED=true` and explicit non-default
   `MCP_HTTP_ALLOWED_HOSTS` **and** `MCP_HTTP_ALLOWED_ORIGINS` on at least one working baseline.
   Use the section 10 Origin value `https://mcp-canary.example.test` and retain old-form/saved-value
   evidence proving that legacy entry existed before this actual catalog upgrade. Run the mandatory
   accepted/rejected Origin pair on the old package; defaults cannot satisfy this baseline. Preserve
   existing write-migration coverage across isolated fixtures, enabling writes only in its separate
   intentional test. Add a harmless unrelated variable, record its value/order, masked keys and
   port/storage/watcher/resources, and prove MCP connection/auth and non-default Host semantics.
2. **BOTH:** upgrade with the actual catalog action. The required package path is: load prior
   saved values -> inspect exact reserved MCP entries in `additional_envs` -> carry their existing
   values into dedicated fields -> consume only those migrated entries -> apply defaults only for
   keys with neither prior representation -> validate no reserved duplicate or unresolved conflict
   remains -> render the package -> verify resulting container environment. Conflicts must fail
   clearly before render, preserving original saved values for explicit resolution. Do not allow
   the base renderer to discover the conflict first. No image-only or manually preconverted setup
   substitutes for this package migration test.
3. **BOTH:** verify successful app startup with no duplicate-env rendering failure. MCP must remain
   enabled if enabled previously, write enablement must be preserved independently, and Host/Origin
   allowlists must retain baseline acceptance/rejection semantics. Require the migrated dedicated
   Origin field in Edit App to contain the saved non-default value and selected runtime environment
   to reflect it. Repeat the mandatory Origin pair: successful initialization/read with the accepted
   Origin and HTTP 403 with the rejected Origin, holding valid Host/auth/request shape constant;
   compare both results with the old package. Confirm `MCP_HTTP_ALLOWED_ORIGINS` is removed from
   `additional_envs` and no duplicate reached render. Verify other migrated dedicated fields where
   exposed, no reserved entries left in `additional_envs`, unrelated entries unchanged, each reserved
   runtime variable present exactly once, and existing keys plus MCP
   client connectivity/authentication still working. Inspect only allowlisted non-secret environment
   fields; never retain the full secret-bearing container environment. Save an unrelated resource
   change, reopen and restart, then repeat the preservation assertions. A separate no-legacy upgrade
   and fresh install must retain HTTP/write false and accepted loopback defaults.
   Invoke Hygiene and run browser unlock/search/read/relationships/Hygiene and key rotation/port edit.
   Deliberately test existing write parity only where writes were intentionally configured. Test a
   conflicting saved state in the supported candidate path to prove clear pre-render rejection.
4. **CMD:** compare authoritative vault manifest and derived mount/model continuity to baseline;
   record readiness and index compatibility. No migration is inferred from an upgrade notification.
5. **BOTH:** if the UI offers the retained prior revision, rollback on this disposable instance and
   verify prior package/image/configuration plus note/search access. Record whether app-pool
   snapshot rollback was chosen and what it covers. External Host Path contents are not assumed
   snapshotted or undone. Then re-upgrade and verify recovery. If unavailable, retain bounded UI
   evidence and publish a tested manual recovery procedure, not a rollback success claim.
6. **BOTH:** fresh install of the new generated package, then separate retain/remove uninstall cases;
   verify external vault preservation, actual derived dataset behavior and secret masking.

Actual catalog upgrade/form/rollback failures cannot be known before that package is delivered
unless a supported preview reproduces them. Treat this as an explicit residual delivery gate:
execute immediately, hold wider catalog-success claims and investigate a confirmed regression
through a narrowly reviewed follow-up. Do not claim VB-082 complete from the RC custom app.
Legacy MCP carry-forward is a required live release/catalog compatibility gate, not just a unit
fixture. The supported RC/custom-package execution in section 10A must PASS the non-default legacy
Origin migration and runtime pair before stable/catalog submission. Separately repeat the same
non-default legacy Origin proof on an actual 1.0.2/image 1.3.0 -> generated Community App package
upgrade after publication. Candidate-package PASS does not satisfy post-catalog proof; catalog
lifecycle validation remains incomplete until that actual upgrade and its Origin assertions PASS.

## 11. Release sequence and STOP decisions

**Choose Option B.** Option A (finish features, publish stable, then test stable on TrueNAS) exposes
ordinary stable users to lifecycle problems before the NAS canary can discover them. Option B uses
a real prerelease artifact while preserving stable/catalog separation:

1. Accept this plan; finish/review the Hygiene UI, package preparation, release docs and validation
   harness changes. Keep named serving disabled. **STOP** on review findings or required failed/
   unavailable checks; a checked-in test is not execution evidence.
   Package preparation must prove the legacy MCP carry-forward/conflict policy through the supported
   upgrade hook; missing-field defaults are insufficient for existing `additional_envs` settings.
2. Align all RC version metadata, freeze reviewed source, pass exact-source CI and publish GitHub
   prerelease `v1.4.0-rc.1` using the existing workflow. It accepts v-prefixed prerelease SemVer,
   builds verified source, publishes exact `1.4.0-rc.1`, and skips stable aliases. **STOP** if tag/SHA,
   prerelease state, metadata, platform or anonymously pullable digest disagree.
3. Run immutable-image functional/MCP gates, then the real TrueNAS RC/custom-app canary above.
   Prepare package validation against current upstream and exercise available preview/current
   catalog lifecycle surfaces, including live saved-values migration from the mandatory non-default
   legacy Host/Origin baseline and the before/after accepted/rejected Origin pair through a supported
   candidate-package path. **STOP** if this path or Origin proof is unavailable/unverified,
   MCP values are lost, reserved duplicates reach rendering, or conflicts lack a clear pre-render
    failure; hand-merged custom-image environment is insufficient. **STOP** on data loss, auth/write
    exposure, persistence/config loss,
   unusable Hygiene, unbounded practical loading failure or unexplained compatibility/recovery.
4. Fix only confirmed issues in reviewed PRs; publish a new RC exact version/digest and rerun affected
   gates. Do not silently replace a tested tag. Stable source must contain no untested behavioral
   changes after the successful RC; metadata-only differences are still recorded and validated.
5. Prepare stable 1.4.0 metadata/source and exact-source CI, obtain release authorization, publish
   stable `v1.4.0`. Rebuilds can resolve different ranged dependencies/base layers, so inspect and
   smoke the **stable digest** as well; RC digest proof is not stable digest proof. Prefer a bounded
   TrueNAS smoke on stable digest before downstream submission. **STOP downstream delivery** if
   stable publication identity/pull/runtime differs or fails.
6. Submit the prepared upstream package against current upstream only after the stable image exists
   and is verified. Select image 1.4.0, align app metadata, choose a new package revision, run official
   validation/migration tests and normal maintainer review. **STOP** on missing image, incompatible
   normalization, leaked secrets, wrong mounts/defaults or failed upstream checks.
7. After upstream merge/catalog generation, run the real 1.0.2-to-new-package upgrade/rollback/form
   and fresh-install tests. Record exact evidence and reconcile VB-082 only for proved gates.
   **STOP promotion of catalog-success claims** on failure; preserve evidence and scope a fix.

The current workflow starts on a **published** GitHub Release and only recovery-dispatches an
existing published release. It cannot build a nonexistent stable version for a prior Community
App selection. It also automatically updates stable aliases after publishing; manual runtime/TrueNAS
gates are not enforced by that job. Therefore "before stable" below means successful RC evidence
and an operator publication decision. Fresh stable digest verification necessarily follows artifact
creation and blocks catalog submission. If automatic alias gating is desired later, it needs its
own workflow contract; this plan does not pretend it already exists or require that expansion.

## 12. Finite release gates

### Blocking before RC

- [ ] Accepted scope and fresh independent reviews for each proposed implementation/preparation PR;
  no reviewer is launched by this analysis. No unresolved correctness/security findings.
- [ ] Hygiene UI complete against unchanged API, all specified state/auth/privacy/accessibility cases;
  local focused checks plus regression E2E and browser acceptance.
- [ ] Current-source Python/non-E2E, Ruff, compileall, Linux containment/concurrency cases, isolated
  Chromium E2E, Compose/build and MCP container checks pass through normal workflow/CI. Include
  actual POSIX coverage; Windows privilege skips do not establish symlink safety.
- [ ] TrueNAS MCP changes prepared against current accepted source, safe defaults and old-value
  carry-forward/conflict fixtures in P2 passed, no accidental historical port/icon rollback. The
  supported hook and saved-versus-default provenance/order are established; no duplicate reserved
  variable reaches render and unrelated additional_envs survive unchanged. Official package
  render/deployable checks pass where applicable; manual form limits remain labelled.
- [ ] RC pyproject/APP_VERSION/MCP_SERVER_VERSION and release text agree; documentation/session/API/
  CLI drift repaired; unsupported named config omitted from user setup. Exact-source CI passes.
- [ ] Synthetic canary fixtures/procedures, baseline 1.0.2 state and recovery copies ready. Smoke
  harness covers eight read-only/ten write-enabled tools, Hygiene payloads and session behavior.

### Blocking before stable

- [ ] RC publication workflow succeeds for exact source; OCI source/revision/version/license and
  Linux/amd64 runtime/attestation identity recorded; anonymous exact-digest pull passes. No stable
  rolling alias moves from the RC.
- [ ] RC immutable digest passes dependency/stdio/HTTP MCP disabled+enabled+write smoke, full
  functional health/dashboard/API/literal/semantic/relationships/CLI gates and clean shutdown/logs.
- [ ] Real TrueNAS RC canary passes install, existing vault, persistence, stop/start, app restart,
  one disposable-host reboot, settings/key/port/permission recovery, Dashboard Hygiene and MCP.
- [ ] **PASS mandatory live candidate-package migration with non-default legacy Origin:** supported
  RC/custom-package upgrade from saved 1.0.2/image 1.3.0 additional_envs with HTTP enabled and explicit
  non-default Host/Origin values. Prove successful render/startup, preserved dedicated Origin field
  and runtime environment, reserved-entry removal/no duplicate, unchanged unrelated entries and the
  accepted/rejected Origin pair before/after upgrade. Preserve Host/write behavior and connectivity.
  Deliberate conflict fails clearly before render. Defaults or form/string checks alone cannot pass.
  This gate is NOT YET VERIFIED until executed; a custom image/manual conversion does not satisfy it.
- [ ] Baseline-to-RC copied-state compatibility and old-image recovery demonstrated; authoritative
  hashes preserved outside intentional writes; database/model behavior and rollback limits recorded.
- [ ] New product UX is usable on representative synthetic NAS data; no hidden scan freshness or
  completeness claim. Stable candidate source differs from tested RC only by reviewed metadata/docs,
  or changed behavior has another RC and affected canary pass.
- [ ] Stable metadata/docs/version alignment and exact-source CI pass; upstream-ready MCP package
  and lifecycle evidence prepared, remaining catalog-only tests explicitly assigned to post-delivery.
  Release authorization obtained separately.

### Blocking before TrueNAS catalog update submission

- [ ] Stable publication and exact source/digest identity, OCI labels, aliases and anonymous pull
  verified; stable digest functional/MCP smoke passes and TrueNAS bounded smoke passes.
- [ ] Image tag exists; current upstream app_version/image/new package revision align. Official
  schema/render/deployable validation and the full P2 fixture matrix pass. **Legacy additional_env
  MCP -> dedicated field compatibility is BLOCKING BEFORE TRUENAS CATALOG UPDATE:** supported live
  candidate-package upgrade passes the mandatory non-default legacy Origin baseline and runtime
  acceptance/rejection pair, preserves prior MCP values, consumes only reserved legacy entries,
  rejects ambiguous saved dual configuration before render and leaves unrelated additional_envs
  unchanged. Defaults apply only to keys without prior values; renderer collision protection remains.
  Secret carry-forward, fresh-install disabled defaults, explicit allowlists, one port and unchanged
  mounts are verified. Repeat on the actual generated catalog package after publication.
- [ ] Existing catalog Edit App persistence and ixVolume retain/remove behavior have real evidence
  on disposable installs; future form tested in supported preview if available. Unavailable preview
  is accurately labelled and does not masquerade as a live new-form PASS.
- [ ] Exact proposed diff reviewed; user/operator instructions, compatibility/rollback limitations
  and post-delivery test owner/baseline are ready. No app-private secret becomes visible in artifacts.

### After publication: required delivery checks versus non-blocking follow-ups

- [ ] **Required catalog delivery verification:** actual generated-package fresh install, Edit App
  MCP/secret persistence, actual 1.0.2/image 1.3.0 legacy additional_env carry-forward upgrade with
  saved non-default Host/Origin and enabled HTTP. **PASS non-default Origin migration before catalog
  lifecycle validation is complete:** preserved dedicated Origin field/runtime environment,
  accepted/rejected Origin pair before/after upgrade, removed legacy Origin entry/no duplicate and
  unchanged unrelated entries. This repeats section 10B on the published catalog package, separately
  from pre-stable candidate proof. Preserve connectivity/allowlists/writes, retained-revision
  rollback/explicit supported recovery and uninstall retention outcomes. These close evidence debt
  after the artifact exists; they are not optional merely because they cannot block an already
  completed merge.
- [ ] **Required publication closeout:** sanitized exact source/CI/workflow/image/platform/runtime
  ledger and documentation reflecting actual outcomes. Do not upgrade planned gates to PASS.
- [ ] **Non-blocking:** Capture/Promotion browser/network adapters, Query REST/MCP/dashboard,
  multi-space D–G/VB-152, Hygiene repair/semantic duplicates/history/export, multi-arch publication,
  graph ranking, optional section/backlink writes and screenshot/CDN polish.

## 13. Index and authoritative-data migration risk

| Concern | Evidence from actual delta | Required upgrade action |
|---|---|---|
| Markdown migration | New parsers are read-only; Capture/Promotion create/append only on explicit invocation; portable fields live in Markdown | **None.** Existing notes are not rewritten, reclassified or relocated automatically |
| SQLite schema | Repository diff adds inspection/error/validated-path behavior, not DDL/schema tables; existing schema/signature generation unchanged | **No schema migration required** for this range |
| Semantic index | `INDEX_FORMAT_VERSION = v3-heading-context`, `semantic-index-v2:` signature and embedding fingerprint contract unchanged; ranking helpers are additive eligibility/basis | **No mandatory rebuild** for an otherwise compatible v1.3.0 index with unchanged config/artifacts |
| Model/cache | Default model, FastEmbed pin, Dockerfile and signature model artifacts unchanged; dependency ranges still resolve at build time | **No mandatory model redownload.** Preserve cache; a missing cache/artifact change may download/revalidate normally |
| Metadata/provenance | New intake/promotion write portable fields/markers only when requested; existing legacy create/append remains compatible | **No retroactive migration.** Old runtime can read new Markdown but lacks new workflow semantics |
| Filesystem mounts | Docker/Compose unchanged, single-root `/vault` and existing derived path retained; named binding/lifecycle not activated | **No new mount** required |
| Permissions/primitives | No new UID/GID or ACL migration; new CLI writes use coordinated lock/hard-link/staging primitives | **No automatic permission change.** Verify hard links and directory locking on intended disposable filesystem before recommending CLI writes there |
| Browser auth | Session changes from JS-stored key to signed cookie | Re-enter key for first new session; current-key changes invalidate cookies; no vault/index migration |
| TrueNAS form config | Existing 1.0.2 can configure MCP through additional_envs; dedicated fields would collide if entries remain | Required per-key carry-forward/consumption and pre-render conflict validation; defaults only for absent keys. P2 must prove supported hook and live candidate upgrade; no Markdown/SQLite migration follows |

Relevant evidence is [repository diff target](../app/repositories/semantic.py),
[signature/search owner](../app/services/semantic_search.py),
[write coordination](../app/services/_vault_writes.py), [requirements](../requirements.txt),
[Dockerfile](../Dockerfile), [generic Compose](../docker-compose.yml) and
[TrueNAS Compose](../compose.truenas.yml). A future missing/incompatible/corrupt derived index may
be rebuilt from Markdown using the stopped-service procedure; that is recovery of disposable data,
not migration of authoritative knowledge. Before claiming reversible upgrade, test both directions
on copies of baseline state. Ranged dependencies and image rebuilds require exact-image validation
even with unchanged application schema/model pins.

## 14. Proposed release narrative

This is draft positioning, not final release notes or a statement that proposed work is complete.

**User-visible headlines:** "Inspect your Markdown vault's health from the dashboard: find broken
note relationships, conflicting aliases, invalid metadata and isolated notes, with explicit scan
limits and no automatic edits. Return to the dashboard through a remembered secure session."

**Power-user / MCP / CLI improvements:** "Use the same read-only Hygiene scan from MCP, REST or
the CLI. Filter live notes with the Knowledge Query CLI, capture selected material into portable
inbox Markdown, and review explicit local create/append promotion with inspectable provenance."
For TrueNAS, add "Configure MCP HTTP, writes and allowlists through Edit App" only after the new
catalog package is delivered and verified; prior to that state it as separately pending delivery.

**Internal foundations not to oversell:** portable parsing and normalized relationships support
these workflows. Named multi-space serving remains unsupported. No cross-vault search, policy
UI, new graph ranker, automatic knowledge repair or browser capture/promotion is promised.

**Operational / safety improvements:** clearer bounded diagnostic/coverage failures, coordinated
local write protections and tested restart/upgrade/recovery on the exact candidate. Claim live
TrueNAS results only once recorded, with the actual tested build/package/digest.

The answer to "Why should a v1.3.0 TrueNAS user install this?" is a recurring browser task they
could not previously perform: find and understand concrete vault-quality issues without a CLI,
with the same evidence available to an MCP assistant. Backend milestones alone do not answer it.

## 15. Decisive recommended scope

| Candidate item | Status now | User value | Work remaining | Risk | Decision |
|---|---|---|---|---|---|
| Hygiene Dashboard | Deferred; absent | Main normal-user upgrade reason | Read-only panel/controller/states/E2E | Medium auth/staleness/performance | MUST BEFORE RELEASE |
| Hygiene REST/MCP/CLI | Implemented/merged | Shared actionable diagnostics | Docs and exact-image validation | Low mutation, bounded-evidence nuance | SHIP AS-IS |
| Capture CLI | Implemented | Portable intake for experts | Examples, limits and offline procedure | Filesystem/uncertain-write semantics | SHIP AS-IS |
| Promotion CLI | Implemented | Explicit reviewed knowledge transfer | Review/apply examples and recovery docs | Write/provenance and index concurrency | SHIP AS-IS |
| Capture/Promotion Dashboard | Absent | Broader adoption later | New approved mutation product flow | High | DEFER |
| Capture/Promotion REST/MCP | Absent | Automation later | Separate adapter/approval/security contract | High | DEFER |
| Knowledge Query CLI | Implemented subset | Combined live filtering/ranking | Accurate flags/examples | Low; whole-vault work limits matter | SHIP AS-IS |
| Knowledge Query REST/MCP/dashboard | Absent | Useful later, redundant scope now | Adapter/UI contract and tests | Medium/large | DEFER |
| Multi-space A–C | Internal, gated | No direct release value | Preserve activation guards/regressions | Latent integration surface | SHIP AS-IS |
| Multi-space D–G | Pending | Future scoped retrieval/writes | Separate large domain/resource work | High | DEFER |
| VB-152 | Unscoped, absent | Future public spaces | Accepted contract plus adapters | High | DEFER |
| TrueNAS MCP form | Present locally; upstream missing; legacy additional_env carry-forward NOT YET VERIFIED | Enables normal catalog MCP setup without breaking existing MCP | Current-upstream port, per-key carry-forward/conflict fixtures and live candidate-package upgrade, review/delivery | Medium package/persistence; upgrade can fail without migration | MUST BEFORE RELEASE |
| VB-082 lifecycle validation | Partial, historical evidence | Trustworthy NAS operation | RC/current-catalog gates, then future upgrade proof | High evidence/data boundary | MUST BEFORE RELEASE |
| Release/runtime smoke | Existing harness, Hygiene added | Proves exact deliverable | Refresh session/new feature gate and run exact digests | Medium | MUST BEFORE RELEASE |
| Documentation/release notes | Partial and stale public lists/auth wording | Discoverability and safe use | Rewrite from inventory, examples, limits | Low but release-critical accuracy | MUST BEFORE RELEASE |
| Hygiene path opens existing reader | Existing reader, no Hygiene integration | Convenience | Reuse viewer controller only if small | Medium coupling | SHOULD BEFORE RELEASE |

For the two delivery-dependent MUST rows, "before release" means the relevant phase gates in
section 12: package preparation and RC lifecycle proof before stable, actual generated catalog
upgrade proof immediately after its publication. It does not make an impossible future catalog
state a precondition for building the stable image. Optional path-opening should not delay the
minimal usable path list.

## 16. Phased execution plan: small reviewed changes

Proposed IDs below are planning labels, not newly allocated authoritative VB backlog entries.
Prefer **five PRs**: P1 UI; P2 package preparation/tests; P3 docs/smoke/RC preparation; P4 stable
preparation with RC evidence; P5 upstream package submission. Live operations produce sanitized
evidence for those PRs/closeout, rather than a giant cross-layer feature PR. Additional RC fixes
must be small confirmed-finding PRs. Changing prohibited status files requires later explicit
acceptance/authorization; nothing in this document changes them now.

### Phase 1 — P1: Read-only Knowledge Hygiene Dashboard

- **Goal/scope:** add the minimal browser scan described in section 5, unchanged API, no new request
  knobs necessary, accurate counts/coverage, shared session lifecycle and safe rendering.
- **Files:** UI shell/controller/styles/new hygiene module, explicit asset route, UI tests and E2E
  fixtures/tests; narrowly scoped user documentation if needed.
- **Out of scope:** mutation/repair, async scan jobs, history/export, semantic duplicates, Query UI,
  Capture/Promotion, spaces, authentication redesign.
- **Prerequisites:** accepted plan and separately scoped task; existing VB-142 contract retained.
- **Acceptance:** a locked-to-unlocked browser user scans a synthetic vault and locates findings;
  all states and limits honest; logout/401/stale responses cannot expose old protected results.
- **Tests:** focused UI/REST reuse checks, complete E2E state/keyboard/reflow/inert-text cases,
  existing dashboard regression; browser acceptance; `agent_check` selections and fresh review.
- **Fresh review:** required. **Gate:** RC product value/auth/E2E. **Complexity: MEDIUM** — controller
  integration and error semantics, not backend algorithm work.

### Phase 2 — P2: TrueNAS MCP delivery preparation

- **Goal/scope:** port the existing four field definitions to current accepted upstream shape in a
  reviewable preparation artifact; retain current port/icon/storage defaults; migrate only the four
  reserved MCP additional_env names under section 8's carry-forward/conflict policy. First prove
  the supported migration/normalization hook, version selection, access to prior saved values
  before defaults and clear errors before render; do not invent a package API.
- **Files/areas:** checked-in package development source/tests/fixtures and delivery instructions
  as appropriate after acceptance; compare current upstream schema/generator/migration conventions.
  A patch/preparation artifact can carry the proposal without mutating upstream during preparation.
- **Out of scope:** live upstream mutation/publication at this phase, new MCP tools/auth/ports,
  named-space settings, exposing every environment variable, version guessing or copying stale
  local metadata over current accepted defaults.
- **Prerequisites:** accepted package task; refresh current upstream revision read-only; v1.3.0
  already provides runtime fields. Stable image selection waits until P5.
- **Acceptance:** prior MCP behavior survives, including hidden/false write settings and exact
  allowlist semantics; migrated reserved entries no longer reach user-environment render; first-class
  fields solely own those names; unrelated additional_env values/order and keys/mounts/port survive.
  Defaults apply only to absent prior representations. Reject both explicitly saved dedicated and
  legacy values before render even when equal; retain original values on failure, with a clear error
  naming the conflicting representations but no value leakage. Re-normalization is idempotent;
  subsequent edits cannot reintroduce reserved duplicates. Upstream-ready diff has no historical
  regressions. Require these **1.0.2/app image 1.3.0 saved-value fixtures**, not only complete target
  schema fixtures:

  | Fixture | Prior saved configuration | Required outcome |
  |---|---|---|
  | 1 | Only `MCP_HTTP_ENABLED` in additional_envs | Carry its non-default true value into HTTP field |
  | 2 | Only `MCP_WRITE_ENABLED` in additional_envs | Preserve true write setting even when HTTP independently defaults false |
  | 3 | Only `MCP_HTTP_ALLOWED_HOSTS` in additional_envs | Preserve explicit non-loopback Host allowlist |
  | 4 | Only `MCP_HTTP_ALLOWED_ORIGINS` in additional_envs | Preserve explicit non-loopback Origin allowlist |
  | 5 | All four reserved MCP keys together | Preserve all four, including enabled behavior/allowlist semantics |
  | 6 | Unrelated additional_envs mixed with one or more reserved MCP keys | Consume only reserved entries; retain unrelated names/values/order |
  | 7 | No legacy MCP keys and no dedicated MCP values | Apply only accepted disabled/loopback defaults; retain unrelated entries |
  | 8: deliberate conflict | A reserved legacy value and explicitly saved dedicated value for the same key; test differing and equal values | Clear rejection before render, no partial saved mutation; operator resolves explicitly |

  For **each successful fixture 1–7**, assert upgrade normalization succeeds, prior MCP value/behavior
  is preserved, no duplicate name reaches render, unrelated additional_envs remain unchanged,
  defaults apply only to keys without prior values, and each reserved name appears exactly once in
  the rendered environment. Repeat normalization to prove idempotence and distinguish schema-injected
  defaults from saved dedicated values. Extend the conflict fixture over all four reserved names;
  assert the renderer is never invoked for the unresolved state. Include valid explicit false
  booleans as preservation variants rather than testing only truthy strings.
- **Mandatory live acceptance in addition to unchanged fixtures:** before stable, upgrade a working
  1.0.2/image 1.3.0 baseline with HTTP enabled and both explicit non-default legacy Host and Origin
  values in additional_envs. Section 10's Origin baseline and accepted/rejected runtime pair must
  PASS before/after upgrade; successful render/startup, preserved dedicated Origin field/runtime
  environment, legacy Origin removal/no duplicate and unrelated-entry preservation are required.
  Repeat on an actual Community App upgrade after catalog publication before lifecycle closeout;
  neither defaults, static fixtures nor the candidate PASS substitute for this separate live proof.
- **Tests:** official package/schema/render/deployable validation, `tests/test_truenas_package.py`,
  the matrix above through the supported upgrade hook, and live TrueNAS legacy-MCP -> dedicated
  candidate-package upgrade before stable/catalog submission, repeated on the actual generated
  package after publication. Preserve current catalog edit/retain/remove checks. A container started
  from hand-merged env is not migration proof; unavailable supported live path keeps this gate open.
- **Fresh review:** required. **Gate:** RC/stable package readiness and catalog submission.
  **Complexity: MEDIUM** — source reconciliation, saved/default provenance and pre-render config
  carry-forward; failed migration can prevent existing apps starting. No new runtime feature.

### Phase 3 — P3: Documentation, smoke and release-candidate preparation

- **Goal/scope:** correct public session/API/MCP/CLI lists, document Hygiene and Promotion commands,
  offline index-write discipline and uncertainty/recovery, prepare concise release notes and RC
  source/version alignment. Update the reusable exact-image harness for session/Hygiene behavior
  where missing, preserving prior strict checks and eight/ten MCP tool sets.
- **Files:** README/README_TRUENAS, CHANGELOG, architecture/session text, release/dashboard checklists,
  lifecycle runbook, `scripts/verify-vb075-image.sh` and smoke tests only as justified;
  pyproject, APP_VERSION and MCP_SERVER_VERSION only in this later authorized release task.
- **Harness constraint:** `verify-vb075-image.sh` currently hardcodes the v1.1.0 digest, source,
  workflow and OCI version. Do not run it unchanged and call that RC proof. Add explicit validated
  release identity inputs or an equivalently reviewed reusable gate, retaining historical evidence,
  strict assertions, disposable cleanup and the new Hygiene asset check.
- **Out of scope:** features, data migration, workflow alias redesign, upstream image selection or
  unsupported completion claims. This planning task itself changes none of those files.
- **Prerequisites:** P1 review/checks and P2 preparation; exact scope accepted separately.
- **Acceptance:** persona-correct examples match commands/routes/tool registration; immutable
  harness verifies new browser/session and diagnostic behavior; RC versions agree and exact-source
  CI passes; all remaining live gates explicitly pending before publication.
- **Tests:** docs paths/links, diff check, version contract, shell syntax/focused harness tests,
  `agent_finish` selected runtime/E2E/container checks, independent CI on frozen source.
- **Fresh review:** required. **Gate:** RC preparation. **Complexity: MEDIUM** — several evidence and
  metadata touchpoints must agree; no new domain behavior intended.

### Phase 4 — O1: Publish RC and execute live TrueNAS canary

- **Goal/scope:** authorized prerelease publication, verify exact GHCR digest, execute section 10A
  including supported candidate-package legacy MCP migration, and current real-catalog residual
  lifecycle tests with synthetic data. Preserve baseline app for
  eventual real upgrade. Capture failures and limits without modifying product code opportunistically.
- **Files/areas:** sanitized release/lifecycle evidence for later P4; external disposable app only
  under separate operator authorization. No filled secret-bearing runbook committed.
- **Out of scope:** production notes/keys, upstream catalog publication, stable tag, unreviewed fixes.
- **Prerequisites:** P3 reviewed, exact-source CI, explicit publication/operator authorization and
  disposable TrueNAS host. RC exact artifact must exist before custom-app image selection.
- **Acceptance:** all before-stable RC gates PASS, including the mandatory saved non-default legacy
  Origin migration and runtime pair; remaining catalog-only gaps named; each failure
  fixed through a narrow reviewed task and new RC. Stopped-state upgrade/recovery verified.
- **Tests:** immutable functional/MCP harness, full human canary, actual data hashes/mounts/permissions,
  UI screenshots/observation and CMD evidence as assigned.
- **Fresh review:** required for evidence incorporated into P4 and any code fixes.
  **Gate:** stable readiness. **Complexity: HIGH-RISK** — data/lifecycle observations and exact
  artifact identity across a real NAS, even with disposable data; not an hours estimate.

### Phase 5 — P4 / O2: Stable preparation and publication

- **Goal/scope:** review sanitized RC evidence and compatibility limits; align stable metadata,
  freeze stable source and rerun exact-source CI; after authorization publish and verify stable image.
- **Files:** release metadata/changelog/checklists/runbook and factual status reconciliation only
  when authorized; no behavior changes bundled into the stable-preparation PR.
- **Out of scope:** new features, claiming future catalog upgrade, automatic reuse of RC digest proof.
- **Prerequisites:** O1 successful, no untested behavior delta, P2 upstream-ready package.
- **Acceptance:** stable source/tag/metadata match, workflow succeeds, correct OCI identity/anonymous
  pull/aliases, exact stable smoke and bounded TrueNAS smoke pass; otherwise hold downstream work.
- **Tests:** version/source assertions, selected checks and exact-source CI, stable functional/MCP/
  image identity checks. Compare resolved dependencies/platform to RC.
- **Fresh review:** required for PR/evidence; publication authorized separately.
  **Gate:** catalog submission. **Complexity: SMALL** for metadata PR; operational verification is
  **MEDIUM** because stable is a separately built immutable artifact.

### Phase 6 — P5: Upstream TrueNAS package update

- **Goal/scope:** after stable verification and authorization, submit the prepared MCP form and
  exact 1.4.0 image/app metadata with next available package revision using normal upstream review.
- **Files:** then-current upstream `ix-dev/community/vaultbridge/` metadata/questions/template/test
  values and migration file only if proved necessary; official generated output as upstream requires.
- **Out of scope:** application changes, new mount/permission scheme, historical port/icon changes,
  unrequested catalog/media cleanup or named spaces.
- **Prerequisites:** P2 and P4/O2 gates, current upstream refresh and owner authorization.
- **Acceptance:** official upstream checks and maintainer review pass; generated catalog selects the
  existing stable image, forms/defaults/legacy additional_env carry-forward and conflict rejection
  align; P2 matrix and live candidate-package migration passed before submission. Record actual revision/digest.
- **Tests:** official full package validation and migration/render fixtures; supported preview
  UI if available. Static/render proof remains distinct from live catalog proof.
- **Fresh review:** required before submission plus upstream maintainer review.
  **Gate:** catalog delivery readiness. **Complexity: MEDIUM** — external schema/review and delivery.

### Phase 7 — O3: Actual catalog upgrade and lifecycle closeout

- **Goal/scope:** execute section 10B from retained real 1.0.2 state, including fresh install, form
  persistence, legacy additional_env MCP carry-forward, upgrade, rollback/recovery and two uninstall
  outcomes; capture sanitized evidence.
- **Files/areas:** release/lifecycle evidence follow-up; authoritative VB-082 state only after
  separately accepted factual reconciliation. No speculative completion or production changes.
- **Out of scope:** product fixes without a new reviewed task; production vaults; assuming custom
  apps prove catalog forms or snapshot rollback covers external mounts.
- **Prerequisites:** P5 catalog generation/availability, retained baseline and disposable copies.
- **Acceptance:** actual catalog upgrade from 1.0.2/image 1.3.0 repeats the mandatory non-default
  legacy Origin baseline, dedicated field/environment preservation, accepted/rejected runtime pair,
  legacy-entry removal and unrelated-entry preservation with PASS before lifecycle completion.
  Pre-stable candidate proof does not replace this check. Each other catalog-only gate has actual
  PASS or exact supported limitation plus tested recovery; unresolved failure keeps VB-082 partial
  and prevents success claims.
- **Tests:** operator screenshots/observations plus status/hash/config-survival evidence, catalog
  rollback if available, post-rollback re-upgrade and stable read/MCP/Hygiene checks.
- **Fresh review:** required for evidence closeout; small documentation follow-up may be needed.
  **Gate:** coordinated release closeout. **Complexity: HIGH-RISK** — real update/storage/rollback
  behavior cannot be substituted by source tests.

## 17. Validation of this planning change

Implementation claims were checked against registered routes/tools/CLI handlers, UI navigation and
session code, owning services, nearest tests, actual Git range and unchanged deployment/version
files. Documentation path/link validation and `git diff --check` are required for this artifact;
docs-only `agent_finish.py` generates the separate handoff packet. Actual executed commands/results
are recorded in `.agent/review_packet.md`, not replaced by the proposed future checklist above.
No full product test run, CI run, image build/pull, browser acceptance or live NAS result is claimed
for this analysis. The independent reviewer should inspect this file and source directly; no
reviewer is launched as part of this task.

## 18. Explicit decisions and final recommendation

1. **Should Slice D stop until after this release? Yes.** Finish the minimal product/operational
   bundle before another internal federation slice.
2. **Is Hygiene Dashboard worth a release blocker? Yes.** It is the smallest significant browser
   workflow that makes completed diagnostics usable by a normal TrueNAS user.
3. **Should Capture/Promotion UI wait? Yes.** Ship local CLI workflows; defer their browser/network
   approval and mutation contracts.
4. **Should first-class TrueNAS MCP controls block release? Yes, as coordinated delivery scope.**
   Require fixture and supported live candidate-package proof of legacy additional_env carry-forward,
   including saved non-default Host/Origin and accepted/rejected Origin behavior, before stable/catalog
   submission; form delivery is not release-ready until it passes. Require
   actual catalog landing after verified stable image, and actual form/upgrade proof before claiming
   TrueNAS delivery complete, repeating the non-default Origin migration/runtime proof on the actual
   Community App upgrade before catalog lifecycle validation is complete.
5. **Publish an RC before stable? Yes.** The exact RC image enables a real TrueNAS canary before
   stable users receive it.
6. **Can A–C ship while disabled? Yes.** Retain public startup rejection and legacy regression proof;
   do not expose named config or advertise serving.
7. **Exact user-visible benefit?** Run a read-only vault-quality scan in the browser, understand
   broken relationships/metadata/isolation with canonical paths and honest coverage, and ask an MCP
   assistant for the same diagnostics; remembered access and first-class catalog controls reduce
   recurring setup friction.

RECOMMENDED NEXT RELEASE:
    v1.4.0

MINIMUM PRODUCT SCOPE:
    Read-only Hygiene Dashboard; existing Hygiene REST/MCP/CLI and Query/Capture/Promotion CLI;
    persistent dashboard sessions; coordinated first-class TrueNAS MCP form delivery.

MUST COMPLETE BEFORE RC:
    Reviewed Hygiene UI/E2E; supported MCP migration hook and legacy carry-forward/conflict fixtures;
    upstream-ready package preparation; accurate public docs/release narrative; updated exact-image
    smoke; aligned RC metadata and exact-source CI.

MUST COMPLETE BEFORE STABLE:
    Exact RC publication/pull/OCI/runtime gates; real TrueNAS canary; copied baseline upgrade and
    recovery/persistence evidence; PASS supported live candidate-package migration from enabled HTTP
    and non-default legacy Host/Origin, including accepted/rejected Origin runtime behavior and
    conflict rejection; no untested behavior changes; stable source/metadata/CI readiness.

MUST VALIDATE ON REAL TRUENAS:
    Install/Portal/unlock/search/read/relationships/Hygiene/MCP; Host Path and persistent derived
    data; restart/stop-start/disposable-host reboot; key/port/form persistence and denial recovery;
    external-vault preservation and ixVolume retain/remove; actual catalog upgrade/rollback after
    package publication, including separate PASS for non-default legacy Origin -> dedicated field/
    runtime preservation, accepted/rejected Origin behavior, no legacy reserved duplicates and
    unchanged unrelated entries, with explicit recovery limits where rollback is unavailable.

DEFER UNTIL AFTER RELEASE:
    VB-151 D–G and VB-152; Query REST/MCP/dashboard; Capture/Promotion network/browser writes;
    Hygiene repair, semantic duplicates and optional advanced UI; unrelated architecture work.

FIRST IMPLEMENTATION TASK:
    P1 — Read-only Knowledge Hygiene Dashboard over the unchanged scan REST contract.
