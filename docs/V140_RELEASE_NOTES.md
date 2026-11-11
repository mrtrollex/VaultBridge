# VaultBridge v1.4.0

Published on 2026-10-10 from `16488735e7a19a6283707a1b6995249432916ca8`;
[publication evidence](RELEASE_CHECKLIST.md#v140-stable-publication--reconciled-2026-11-11).
These notes preserve that release's scope, including its disabled named serving and CLI-only
Query/Capture/Promotion. Current main has subsequently accepted VB-151/VB-152 scoped REST/MCP/CLI
composition; [VB-153](VB153_RELEASE_READINESS.md) plans its provisional v1.5.0 qualification.
No historical v1.4.0 capability or test outcome is extended by that later source acceptance.

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
These changes do not establish public catalog delivery or live catalog migration proof. At the
2026-11-11 pinned upstream inspection, Community package 1.0.11 selects app 1.4.0
but its development/generated forms still omit dedicated MCP fields. Refreshed package delivery, real
catalog migration, Edit App persistence, reboot, rollback/recovery and ixVolume lifecycle validation
remain separate pending work.
