# Documentation

Start with the [product README](../README.md). Most installations need only the legacy Docker or
TrueNAS path; named Knowledge Spaces are optional.

## User and operator guides

- [Docker setup and operations](DOCKER.md) — source builds, settings, storage, health and recovery
- [TrueNAS Community App and manual setup](../README_TRUENAS.md)
- [Named Knowledge Spaces](KNOWLEDGE_SPACES.md) — roots, policies, scopes and named-mode exclusions
- [REST API](API.md), [MCP integration](MCP.md), [CLI workflows](CLI.md)
- [Legacy Web Dashboard](DASHBOARD.md) — features, sessions, privacy and screenshots
- [Security policy](../SECURITY.md)

## Document ownership

| Owner | Responsibility |
| --- | --- |
| [AGENTS.md](../AGENTS.md) | Mandatory repository/agent invariants |
| [README.md](../README.md) | Public product entry point, default installation and navigation |
| [ROADMAP.md](../ROADMAP.md) | One forward-looking product roadmap; candidate priorities and non-goals |
| [BACKLOG.md](../BACKLOG.md) | Authoritative task contracts/acceptance criteria, IDs and current-work navigation |
| [PROJECT_STATE.md](../PROJECT_STATE.md) | Compact dated factual snapshot and current limits |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | Current ownership, compatibility, security and lifecycle contracts |
| [Codex Playbook](CODEX_PLAYBOOK.md) | Task selection, implementation, required checks and separate fresh review |
| [Release checklist/ledger](RELEASE_CHECKLIST.md) | Durable release checks and dated source/image/CI/operator evidence |
| [CHANGELOG.md](../CHANGELOG.md) | Published release changes and explicitly separated unreleased work |
| [ADRs](adr/) | Accepted architecture decisions and their original scope |
| [Operator guides above](#user-and-operator-guides) | Detailed configuration and usage for each workflow |
| [History index](history/README.md) | Superseded roadmaps/plans and historical verification records |

Link to the owner rather than copying its status narrative. Summaries may state the current version
or a critical limit, but exact SHA/digest/job evidence belongs in the ledger. Historical documents
retain original claims under a clear dated-context notice; they are not alternate current plans.

## Contributors and evidence

Read [Contributing](../CONTRIBUTING.md), [development setup](../DEVELOPMENT.md) and the
[Codex Playbook](CODEX_PLAYBOOK.md). The [maintainability audit](MAINTAINABILITY_AUDIT.md) records
this cleanup's findings, bounded refactor candidates, evidence limitations and proposed next tasks.
Release-specific [v1.5.0 notes](V150_RELEASE_NOTES.md) explain delivered behavior and recovery limits.

The root release-checklist and original-roadmap filenames remain navigation stubs for existing
links. No documentation framework, generated site or frontend dependency is required.
