# VaultBridge v1.5.0-rc.1 — prepared release notes

**Unpublished candidate source: 1.5.0-rc.1.** These notes describe accepted VB-151 A–G and
VB-152 A–D functionality. They do not announce publication, image qualification or deployment.
Published stable [v1.4.0](https://github.com/mrtrollex/VaultBridge/releases/tag/v1.4.0) does not
contain named Knowledge Spaces. Its tag, image and historical verification remain separate.

## Delivered functionality

- Explicit opt-in named composition for one to eight independent Markdown roots, each with its
  own derived SQLite state, policy, capabilities and immutable owner binding. Markdown remains
  authoritative; local CPU embeddings and rebuildable indexes remain the defaults.
- Named REST, MCP stdio, MCP Streamable HTTP and stopped-service CLI adapters. Reads, literal and
  semantic federation, relationships, Hygiene and Knowledge Query use the accepted shared owners
  and unchanged semantic ranking. Equal relative paths in different spaces remain distinct.
- Scoped REST/MCP create and append; CLI capture and promotion with explicit approval, replay,
  source/destination checks and v2 space-bound provenance. Capture/promotion remain CLI-only.
- One policy-authorized runtime generation, shared model/scheduler/watcher resources, startup
  reconciliation, cooperative cancellation and fenced shutdown. Disabled indexing starts no
  semantic owner work; native ownership retains the accepted ADR 0010 safety boundaries.

## Compatibility and configuration

Omit `KNOWLEDGE_SPACES_JSON` to retain legacy single-vault composition, Dashboard and maintenance.
Legacy API paths, aliases, operation IDs, request/response contracts and omitted-scope v1
capture/promotion bytes remain compatible. There is no mandatory migration.

Named mode requires explicit strict JSON, exactly one `default`, disjoint accessible roots and
derived-data paths, and matching legacy `VAULT_PATH`/`SEMANTIC_DATA_PATH` for default. Supply mounts
and environment to the container explicitly. Generic Compose does **not** forward named JSON
from a local `.env` automatically. See the [operator guide](KNOWLEDGE_SPACES.md) and
[executable RC procedure](V150_RC_RUNBOOK.md).

Omission selects default only; it never discovers or expands scope. Explicit `default` uses the
strict scoped contract. REST/MCP single-space operations use `space`; federated operations and
Query use `spaces`; CLI uses repeated `--space`. Query omission still returns qualified output.
Explicit CLI capture/promotion uses v2 even when selecting default; omission retains v1.

Current/previous Bearer keys authenticate the deployment over HTTP. They do not grant per-user or
per-space permissions. Configured space policy and capabilities authorize operations; a mixed
allowed/denied selection fails wholly. Stdio trusts local process/OS permissions. MCP HTTP and
MCP writes remain separate opt-ins; nine read-only tools become eleven with writes enabled.

## Deliberate limits and recovery

Named Dashboard, assets and session paths return static 404/no-store. Named registry discovery,
status/index maintenance, root/ID rebind, artifact migration, cross-space promotion and REST/MCP
capture/promotion are unsupported. Named readiness checks default only after policy authorization;
it does not certify the entire registry. Disabled default indexing returns not-ready.

Stop serving and stdio before CLI writers/maintenance on the same writable derived state. There
is no cross-process lock, atomic multi-root snapshot, hardlink/hostile-writer isolation or universal
latency guarantee. A confirmed Markdown commit survives enqueue failure. `commit_unknown` with
`committed:false` can still mean physical bytes changed: preserve IDs/requests, inspect authorized
artifacts and reconcile before any retry. Do not retry automatically.

Opaque native release outcomes can retain the same generation in `stopping` indefinitely under
[ADR 0010](adr/0010-safety-first-e6-cleanup-outcomes.md). Process exit is not evidence of successful
in-process release. Rollback requires stopped writers, independent Markdown/config/artifact backups
and a separate prior-image/layout copy; v1.4.0 does not interpret named/v2 artifacts. Never repoint
old code at incompatible bound databases or move immutable tags to recover.

## Remaining release gates

This preparation requires separate acceptance and independent CI on its eventual exact merge SHA.
RC publication needs explicit authority for both GitHub prerelease and the resulting GHCR push.
The exact immutable RC image must pass legacy and named tests, real model/index/watcher checks,
and separately authorized disposable canary/restart/recovery before stable authorization.
Semantic-disabled smoke proves none of those semantic/deployment gates.

The eventual `1.5.0` transition requires separate review, limited to release metadata/documentation
unless new evidence requires a bounded repair and requalification. Stable publication rebuilds
the image and advances `1.5`, `1` and `latest`; verify the stable digest separately. Existing
`1.4.0`/`1.4` remain historical identities. TrueNAS Community App form delivery and catalog lifecycle
are separate: no named-space form support, catalog update or production deployment is claimed.
