# VaultBridge product roadmap

Updated 2026-11-19, after v1.5.0 publication.
This is product direction, not authorization to implement a feature or publish a release.
[BACKLOG.md](BACKLOG.md) owns task contracts and acceptance criteria.

## 1. Product vision

Make a personal Markdown knowledge base useful to people, scripts and AI clients through a small,
reliable, self-hosted interface. Keep notes portable and usable without VaultBridge.

The default deployment should remain practical on a home server or NAS: one application container,
SQLite and local CPU embeddings. More capability should not require a proprietary knowledge store,
cloud embedding service or infrastructure that costs more to operate than the vault itself.

## 2. Current stable baseline

- Published stable: [v1.5.0](https://github.com/mrtrollex/VaultBridge/releases/tag/v1.5.0),
  November 19, 2026.
- Legacy single-vault operation remains the default. Named Knowledge Spaces are an explicit opt-in;
  there is no mandatory migration.
- Linux/amd64 container distribution; local CPU semantic retrieval with rebuildable SQLite indexes.
- REST, MCP and CLI use shared domain owners and preserve legacy compatibility contracts.
- The bundled Web Dashboard remains available in legacy mode. Named mode intentionally excludes it.

Exact source, immutable image identities, CI and operator evidence belong in the
[release ledger](docs/RELEASE_CHECKLIST.md#v150-release-evidence).
Historical v1.4.0 releases and images remain preserved; they do not support named/v2 artifacts.
See [current state](PROJECT_STATE.md) for operational limits and downstream delivery boundaries.

## 3. Delivered capabilities

| Capability | Shipped boundary |
| --- | --- |
| Legacy single vault | Existing configuration, REST aliases, operation IDs and omitted-scope behavior |
| Knowledge Spaces | One to eight explicit roots, including `default`, independent policies and derived indexes |
| REST and MCP | Authenticated/scoped retrieval and authorized one-space create/append; MCP writes default off |
| CLI workflows | Local reads, Query, Capture, Promotion, Hygiene and legacy stopped-service index maintenance |
| Local semantic search | CPU embeddings, hybrid ranking, startup reconciliation and optional filesystem watcher |
| Knowledge Query | Bounded live constraints composed before unchanged semantic ranking |
| Knowledge Capture and Promotion | CLI-only controlled writes, explicit review, provenance and replay evidence |
| Knowledge Hygiene | Bounded read-only diagnostics and explicit coverage, without automatic repairs |
| Relationships | Verified outgoing links/backlinks and portable Markdown/wikilink domain support |
| Legacy Web Dashboard | Same-origin health, search, selected-note relationships and Hygiene presentation |

These are delivered capabilities, not new roadmap milestones. Adapter-specific limits remain in
the [architecture](ARCHITECTURE.md) and [Knowledge Spaces guide](docs/KNOWLEDGE_SPACES.md).

## 4. Current priorities

The following priorities are **candidates**. They do not allocate new task IDs, approve scope,
set release dates or override existing acceptance criteria. Choose one bounded issue at a time.

### P0 — Documentation and productization

- Keep stable release claims consistent with authoritative GitHub/GHCR metadata.
- Give installation, mode selection, integration and contribution guidance a clear owner.
- Provide stable-image examples and a straightforward legacy Docker path.
- Explain named configuration, permissions, coverage and intentional Dashboard exclusion early.
- Keep current state separate from completed task contracts and dated qualification evidence.
- Remove obsolete task-selection advice while preserving the task/finish/fresh-review workflow.

The present cleanup implements the documentation structure. Its fresh review remains a separate
handoff; future upkeep should change the owning document instead of repeating status narratives.

### P1 — Reliability and developer experience

- Define one release qualification entry point over the existing legacy, named and MCP verifiers.
  Preserve separate identity, semantic/model, lifecycle, cleanup and publication evidence.
- Improve configuration diagnostics without exposing roots, credentials or note content.
- Make partial-coverage reasons actionable, including size exclusions and unavailable indexes.
  Never turn incomplete coverage into an apparent complete result.
- Make synthetic integration scenarios reproducible with explicit readiness/coverage barriers.
  Preserve causal assertions and native-platform checks; arbitrary sleeps are not a repair.
- Complete remaining TrueNAS lifecycle evidence on an explicitly authorized disposable installation.
  Catalog form/migration delivery and lifecycle validation remain independent work.

Release qualification and coverage diagnostics precede optional expansion: they directly affect
whether operators can understand and trust the capabilities already shipped.
TrueNAS lifecycle work may proceed independently when an operator environment is available.

### P2 — Performance and maintainability

- Measure full and incremental indexing latency, memory and CPU on sanitized workloads.
- Record dataset size, model/cache state, settings, source/image identity and platform with results.
- Use existing evaluation and benchmark tooling before changing retrieval or embedding behavior.
- Extract narrowly scoped responsibilities only after direct tests establish their contracts.
- Simplify duplicated test setup where it retains ownership, cancellation and failure assertions.
- Establish regression benchmarks with useful baselines before proposing latency gates.

File size alone is not a refactor criterion. Native release, filesystem containment and scoped
authorization code carry correctness obligations that a shorter implementation might obscure.
See the [maintainability audit](docs/MAINTAINABILITY_AUDIT.md) for bounded proposals and risks.

### P3 — Optional capabilities

| Candidate | Required decision before implementation |
| --- | --- |
| Named-mode Dashboard | Explicit scope UX, session/policy behavior and privacy design |
| Safe section updates | VB-032 design/ADR before VB-033; conflict and byte-preservation rules |
| Verified backlink writes | VB-034 opt-in write contract, ambiguity and retry safety |
| Additional deployment architectures | Measured operator need and explicit ownership/coordination design |
| Alternative local embedding providers | Evaluation, resource cost and index-compatibility decision |
| Multi-architecture distribution | VB-055 feasibility, supported-platform qualification and image evidence |

These are options, not promises. Do not infer named Dashboard support from REST/MCP activation,
or adopt infrastructure/API expansion merely because it appears in this table.

## 5. Next release themes

Prefer a focused reliability/productization increment over another broad capability milestone.
The release number and included tasks remain undecided until issues are scoped and accepted.

A useful next release should make at least one existing operator workflow demonstrably easier:

- diagnose a rejected configuration;
- understand why a query has partial coverage;
- reproduce exact-image qualification with a disposable synthetic dataset; or
- compare indexing cost against a recorded baseline.

Documentation can ship independently when it corrects facts without changing behavior.
Application publication, automatic catalog image updates, form delivery and operator upgrade
validation must retain distinct completion records.

## 6. Longer-term opportunities

Consider richer local integrations, larger-vault performance and additional deployment options only
when measured user value outweighs configuration, compatibility and support costs.

Relationship-aware ranking remains exploratory: VB-105's limited evaluation did not justify changing
production ranking. More evidence is needed before accepting a new retrieval signal.
Provider choice should preserve local operation, stable ranking expectations and rebuildable indexes.

Any new service, storage engine, authentication scheme, breaking API, major runtime dependency or
distributed coordination model requires an accepted ADR before implementation.

## 7. Explicit non-goals

- Replacing Markdown with a proprietary source of truth.
- Sending note content to external embedding/AI services by default.
- General filesystem APIs, arbitrary host-path access or an unapproved delete endpoint.
- Automatic note repair, silent mutation, cross-space promotion or unsafe write retries.
- Mandatory external databases, orchestration frameworks, Node builds or additional services.
- A general account/tenant system inferred from server-side Knowledge Space policy.
- Automatic in-place downgrade of named databases or v2 provenance to v1.4.0.
- Claiming production, catalog upgrade or rollback validation from source tests or a container restart.

## 8. Roadmap decision criteria

Before promoting a candidate to an implementation task, establish:

1. A concrete user/operator problem and a small observable improvement.
2. Evidence that the current owner and existing stack cannot solve it adequately.
3. Explicit API, authorization, filesystem, persistence and deployment compatibility boundaries.
4. A bounded contract, failure cases and reproducible validation using synthetic data.
5. Runtime, resource and long-term maintenance costs acceptable for home-server/NAS use.
6. An ADR where required, plus separate authority for any external delivery or operator action.

Prefer fixes that strengthen shipped behavior. Defer options with unclear demand, unmeasured benefit
or disproportionate operational cost. The roadmap does not weaken security or review gates.

## 9. Implementation and historical milestones

- [Current-work backlog entry point](BACKLOG.md#current-work-entry-point)
- [Current factual state](PROJECT_STATE.md)
- [Contributor workflow](docs/CODEX_PLAYBOOK.md)
- [Release evidence and historical ledger](docs/RELEASE_CHECKLIST.md)
- [Accepted architecture decisions](docs/adr/)
- [Historical milestone roadmaps and state snapshots](docs/history/README.md)

Completed specifications remain in BACKLOG.md under their existing IDs. Superseded roadmaps are
archived as dated evidence and are not alternate current plans.
