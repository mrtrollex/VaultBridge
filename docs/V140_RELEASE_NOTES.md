# VaultBridge v1.4.0

VaultBridge expands its portable Markdown knowledge tools while keeping Markdown authoritative,
SQLite indexes rebuildable, and embeddings local by default.

## Highlights

- Read-only Knowledge Hygiene Dashboard with explicit scans, canonical finding paths and clear
  partial/coverage states. The same bounded diagnostics are available through REST
  `POST /api/v1/knowledge/hygiene/scan`, MCP `knowledge_hygiene_scan` and CLI `hygiene scan`.
- Persistent dashboard sessions use signed HttpOnly cookies. Unlock once, restore across reloads,
  and clear protected data on logout or authentication failure; JavaScript does not persist raw keys.
- Knowledge Query CLI combines live Markdown constraints, literal/semantic text, folders, paths
  and required tags using the existing semantic ranker.
- Portable Capture CLI creates inbox/draft Markdown with provenance and retry protection.
  Explicit Promotion review/apply CLI supports human-approved create/append with hash checks and
  replay evidence, retaining the capture source.
- Bounded YAML frontmatter, aliases and tags, plus normalized inline Markdown links and wikilinks,
  support shared diagnostics without a persistent graph or a new storage service.
- Cooperating Markdown writers share local thread/process coordination and containment checks.

## Upgrade and operations

Existing REST compatibility routes remain supported. MCP HTTP remains opt-in and MCP writes remain
separately disabled by default. Hygiene is diagnostic and advisory: it does not repair, merge or
remove notes. Query, Capture and Promotion are CLI capabilities; they add no network or browser
adapters. Named multi-space serving remains disabled.

Current-key rotation invalidates signed dashboard sessions; unlock again with the current key.
Keep semantic index maintenance as a stopped-service CLI operation. Back up authoritative Markdown
and retain deployment configuration before upgrading; derived semantic indexes remain rebuildable.

## TrueNAS boundary

This application release includes preparation for dedicated MCP package fields and legacy Host/Origin
allowlist migration, plus a watcher-disabled debounce rendering repair in package preparation.
These changes do not establish public catalog delivery or live catalog migration proof. The upstream
Community package does not yet expose the new MCP form fields. Refreshed package delivery, real
catalog migration, Edit App persistence, reboot, rollback/recovery and ixVolume lifecycle validation
remain separate pending work.
