# VaultBridge v1.5.0 — prepared stable release notes

**Stable release preparation; not yet published.** This metadata-only transition builds on
the published [v1.5.0-rc.2](https://github.com/mrtrollex/VaultBridge/releases/tag/v1.5.0-rc.2)
candidate and its accepted VB-151 A–G / VB-152 A–D functionality. Application behavior,
API contracts, index formats and persistent data formats are unchanged from RC2.
Published stable [v1.4.0](https://github.com/mrtrollex/VaultBridge/releases/tag/v1.4.0) does not
contain named Knowledge Spaces and retains its independent immutable release identity.

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

## RC2 qualification evidence and known canary finding

The immutable RC2 image passed named-space and semantic smoke tests, followed by an isolated
TrueNAS Custom App canary using copied Markdown, not production mounts. On TrueNAS, both named
spaces reached a compatible semantic index; the default index reconciled 800 notes. An external
new Markdown file in `research` was detected and indexed automatically via the watcher, and
retrieval returned it with complete research coverage. Persisted indexes were reused after an
application configuration redeploy.

**Open canary finding:** Default federated semantic reads reported
`note_unavailable` with 801 Markdown paths enumerated and 800 evaluated. All 801 copied
files passed a host-side UTF-8/readability check as UID 568, but the precise cause remains
unresolved. Do not claim complete default-space coverage, or conflate the missing indexed
note with the failed federated read without further evidence.

This canary does not independently establish an isolated v1.4-to-v1.5 upgrade, an explicit
restore demonstration, or all TrueNAS Community App lifecycle gates. The stable digest has
not been qualified by the RC2 canary alone.

## Stable release gates

The stable metadata/documentation delta requires review, an authorized merge and successful
independent CI on the resulting exact commit SHA. Obtain separate explicit authorization
before creating the `v1.5.0` tag and publishing its non-prerelease GitHub Release.
Stable publication **rebuilds** the image and moves the `1.5`, `1` and `latest` GHCR
aliases; it is not an RC image rename. Verify the published stable digest, source
revision, OCI labels, dependency/model equivalence, legacy and named functional gates,
and restart/persistence evidence before declaring the release qualified. If equivalent
runtime inputs cannot be established, repeat a bounded stable-digest canary.

Existing `1.4.0`/`1.4` retain their historical identities. TrueNAS Community App form
delivery and catalog lifecycle are separate: no named-space form support, catalog update,
production upgrade or automatic rollback is claimed.
