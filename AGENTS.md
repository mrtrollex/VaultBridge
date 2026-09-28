# AGENTS.md — VaultBridge

This file defines the instructions every coding-agent session must follow in this repository.

## Mission

VaultBridge is a small self-hosted REST and semantic-search bridge for Obsidian Markdown vaults.
Markdown remains the source of truth, and the project must remain practical for a home server or NAS
using Docker, SQLite, and CPU-only embeddings.

## Non-negotiable invariants

- Preserve the minimum API and operational surface necessary.
- Do not add arbitrary filesystem operations or a delete endpoint without an explicit project decision.
- Keep vault paths vault-relative and protect every filesystem boundary against traversal and symlink escape.
- Do not send note content to external embedding or AI services by default.
- Keep SQLite and the single-container deployment as defaults. A new service, storage engine,
  authentication scheme, breaking API change, major runtime dependency, or distributed-coordination
  model requires an accepted ADR before implementation.
- Preserve the public compatibility contract documented in `ARCHITECTURE.md` and implemented by the
  registered routes, including compatibility aliases, operation IDs, and request/response semantics,
  unless the exact task authorizes a migration.
- Keep the vault usable without VaultBridge and keep semantic indexes rebuildable from Markdown.
- Never log or commit API keys, credentials, vault contents, model caches, or generated semantic databases.

## Sources of truth

Start with the smallest authoritative context, in this order:

1. This `AGENTS.md`.
2. The exact GitHub issue and, when applicable, the matching section of `BACKLOG.md`.
3. The relevant implementation files and nearest tests.

Consult `ARCHITECTURE.md`, accepted ADRs, `PROJECT_STATE.md`, and `ROADMAP.md` only when their subject
matter affects the task. `docs/CODEX_PLAYBOOK.md` owns the detailed Codex workflow and project-specific
conventions. If authoritative sources disagree, stop and report the conflict instead of guessing.

## Issue implementation and review

For GitHub issue implementation, confirmed-finding repair, or fresh review, use
`$vaultbridge-issue-workflow` or follow `.agents/skills/vaultbridge-issue-workflow/SKILL.md`.

- Inspect existing ownership and behavior before proposing a rewrite.
- Make the smallest coherent change that satisfies the exact task; do not implement the next backlog item.
- Preserve unrelated and pre-existing working-tree changes.
- Add or update focused tests for changed behavior, including failure cases for security-sensitive changes.
- Update documentation only when behavior or factual project state changed.
- Do not commit, push, publish, release, or deploy unless explicitly requested.

## Validation and review contract

- Preserve the `agent_task.py -> implementation -> agent_finish.py -> fresh review` workflow described in
  `docs/CODEX_PLAYBOOK.md`.
- `scripts/agent_check.py` owns change-aware check selection. A failed or required-but-unavailable check
  means the task is incomplete; GitHub CI remains independent evidence.
- Fresh review is read-only and findings-only. Report concrete findings by severity and finish with exactly
  `APPROVE` or `FIXES REQUIRED`.
- Fix only confirmed findings, rerun the completion workflow, and repeat fresh review when the fix
  materially changes the implementation.

## Security and dependencies

Treat path resolution, authentication, note writes, API keys, public deployment guidance, and content-size
limits as security-sensitive. Test relevant denial and failure paths explicitly.

Before adding a dependency, explain the concrete problem, why the standard library and current stack are
insufficient, runtime and operational impact, and whether it adds a service or network dependency. Do not
add an LLM orchestration framework to the core project.

## Documentation and privacy

Write public repository documentation in English. Use placeholders in deployment examples and never include
the author's real credentials, private hostnames, or private vault content.
