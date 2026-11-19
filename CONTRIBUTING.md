# Contributing to VaultBridge

Choose one small problem from an issue or the [backlog current-work entry point](BACKLOG.md#current-work-entry-point).
The [roadmap](ROADMAP.md) describes candidate direction; it does not approve implementation scope.

## Before coding

Read [AGENTS.md](AGENTS.md), the exact issue/task and matching backlog contract, then the responsible
implementation and nearest tests. Consult architecture/ADRs or current state when relevant.
Preserve Markdown authority, local embeddings, SQLite/single-container defaults, compatibility,
authorization/scope and filesystem safety. Architecture-changing work needs an accepted ADR first
where AGENTS.md requires one. Keep refactors and features separately reviewable.

## Implementation and review path

1. Set up the [Python development environment](DEVELOPMENT.md).
2. Run `python scripts/agent_task.py --task-file path/to/task.md` and inspect the task packet.
3. Implement only the scoped change, with focused behavior/failure tests where behavior changes.
4. Run relevant checks and `python scripts/agent_finish.py --task-file path/to/task.md`.
5. Hand only `.agent/review_packet.md` to a separate fresh read-only review session and stop.
6. Fix only confirmed findings in a subsequent session and rerun completion; obtain fresh approval
   when the implementation materially changes, then use the approved PR/independent CI path.

The [Codex Playbook](docs/CODEX_PLAYBOOK.md) owns the exact packet, check and review rules.
Required checks must pass; unavailable verification must be reported. Never weaken tests to pass.
Agents must not commit, push, open PRs, publish or deploy without explicit user instruction.

## Documentation and privacy

Use the [ownership table](docs/README.md#document-ownership) and update the document that owns the
changed behavior/fact. Keep public docs in English. Preserve original dated release evidence and
task acceptance criteria; archive superseded plans with working links rather than deleting them.
Never include real vault content, API keys, private hostnames or generated semantic databases.

## Pull requests

Describe the concrete problem and resulting behavior, validation actually executed, compatibility/
reindex impact and material security or operational limitations. Keep scope narrow and link the issue.
Do not infer production, catalog delivery or lifecycle validation from source CI.
