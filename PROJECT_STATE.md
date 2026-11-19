# Current project state

Snapshot: 2026-11-19. This file records current facts and limits; task contracts belong in
[BACKLOG.md](BACKLOG.md), direction in [ROADMAP.md](ROADMAP.md), and immutable identities and
historical execution evidence in the [release ledger](docs/RELEASE_CHECKLIST.md#v150-release-evidence).
The [previous accumulated snapshot](docs/history/PROJECT_STATE-pre-v150-cleanup.md) remains historical.

## Stable baseline

**v1.5.0 is published stable**, released November 19, 2026. Read-only GitHub/GHCR inspection
confirmed the annotated tag target, exact-source CI, publication jobs, image labels and digest.
The `1.5`, `1` and `latest` aliases resolve to that stable image at this snapshot.
Historical `v1.4.0`, `1.4.0` and `1.4` remain preserved with their independent source/image identities.
No release, image or external repository was changed by this documentation cleanup.

VB-151 A–G and VB-152 A–D are accepted and merged. Named REST/MCP/CLI composition ships in v1.5.0.
Release publication does not establish every deployment, downstream-form or lifecycle gate.

## Working production characteristics

- Markdown is authoritative and usable without VaultBridge; SQLite semantic indexes are rebuildable.
- The default is legacy single-vault composition, one Linux/amd64 container and local CPU embeddings.
  Omit `KNOWLEDGE_SPACES_JSON` to keep existing configuration and legacy behavior.
- Explicit named mode supports one to eight disjoint roots, including exactly one `default`,
  independent policy/index ownership and bounded explicit retrieval federation. An omitted scope
  selects default only; it never expands to the registry.
- REST `/api/v1` and legacy aliases preserve their operation IDs and compatibility behavior.
  HTTP uses current/previous Bearer keys; these authenticate a deployment, not per-user space grants.
  Configured policy/capabilities authorize named operations before owner dispatch.
- Local MCP stdio and opt-in HTTP `/mcp` expose exactly nine default read-only tools:
  `list_notes`, `read_note`, `search_notes`, `related_notes`, `duplicate_candidates`,
  `note_links`, `note_backlinks`, `knowledge_hygiene_scan`, `knowledge_query`.
  `MCP_WRITE_ENABLED=true` adds `create_note` and `append_note` (eleven total).
  HTTP Host/present-Origin allowlists and Bearer checks remain separate from stdio OS trust.
- CLI supports reads, bounded Query, Capture, explicit Promotion and read-only Hygiene. Capture and
  Promotion remain CLI-only. Explicit scoped provenance uses v2; omitted selection preserves v1.
- Legacy `/ui/` provides Overview, Search, selected-note relationships and Hygiene. `/ui/session`
  establishes a signed HttpOnly session cookie; protected requests use `X-VaultBridge-UI-Request: 1`,
  with no raw request credential in sessionStorage/localStorage. Logout and `401` clear protected
  page state; current-key rotation invalidates sessions. See [cookie limits](docs/DASHBOARD.md).
- Named Dashboard/assets/session, named registry discovery, status/index maintenance, root/ID rebind,
  artifact migration and cross-space promotion remain unsupported.
- Startup reconciliation, optional filesystem watching and persisted compatible-index reuse share
  existing semantic owners. Named readiness covers the authorized default, not every configured space.
  Coverage is explicit; default `MAX_NOTE_BYTES` is 1,000,000 bytes.

## Verification boundaries

Exact-release CI passed Python, Docker and Dashboard E2E jobs; the release publication workflow's
three jobs passed. These were inspected remotely, not rerun or relabelled as local results.
Anonymous registry manifest/configuration readback confirms identity; it is not a full image pull,
model regression or deployment qualification.

The task owner reports isolated stable-image passes for REST authentication/space isolation,
semantic retrieval/index reuse, watcher updates, graceful restart and MCP HTTP/stdio read-only
behavior. These are **operator-provided** observations, not independent CI evidence.
The earlier RC2 copy-vault coverage shortfall is explained by an oversized Markdown file exceeding
the default size limit; no subsequent complete 801/801 regression is established.
Details and evidence limits belong in the [release ledger](docs/RELEASE_CHECKLIST.md#v150-release-evidence).

No MCP-write stable canary, live production migration, public-catalog upgrade or rollback completion
is inferred. Old per-task verification reports retain their original source and platform boundaries.

## TrueNAS delivery

Read-only upstream inspection at `8fd9c079ad13e823d4bd274655deabdbb6d7d97c` found development and
generated package **1.0.12**, app **1.4.0**, still without dedicated MCP form fields.
The generated image remains pinned to the historical 1.4.0 digest at this dated snapshot.
Catalog application image versions update automatically; no image-version-only PR is needed.

Dedicated MCP fields and legacy `additional_envs` migration require a separately scoped source/form
change against refreshed upstream. Named configuration/mount support is not implied by that work.
VB-082 remains partial: Edit App persistence, host reboot, actual generated-catalog upgrade,
rollback/recovery and ixVolume retain/remove evidence remain open. A Custom App restart/image swap
does not close those gates. See [TrueNAS setup](README_TRUENAS.md) and the
[lifecycle runbook](docs/VB_082_TRUENAS_LIFECYCLE_RUNBOOK.md).

## Retained constraints and open work

No cross-process semantic-index lock, atomic multi-root snapshot or hostile-writer/hardlink isolation
is claimed. Stop serving/stdio and other writers before CLI writes or maintenance on the same state.
An uncertain write must be reconciled before retry. ADR 0010 allows opaque native cleanup to retain
a generation in `stopping`; process exit does not prove successful in-process release.
Native dependency security uncertainties retained by the RC publication are not classified as fixed
by this documentation task; no new vulnerability or reachability audit was performed.

Deferred options include VB-032/VB-033 section updates, VB-034 backlink writes and VB-055 multi-arch.
Reliability/diagnostics/benchmark/refactor candidates need individual accepted contracts.
Use the [backlog navigation](BACKLOG.md#current-work-entry-point) and
[maintainability audit](docs/MAINTAINABILITY_AUDIT.md), not historical implementation sequences.
