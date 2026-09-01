# Caveman + Codex experiment

This experiment evaluates whether Caveman reduces the real token/context usage of Codex while working on VaultBridge.

It is intentionally isolated from the VaultBridge runtime. Caveman is a coding-agent wrapper/proxy, not a replacement for VaultBridge semantic search, SQLite, or the embedding model.

## Goal

Compare the same representative VaultBridge task in two fresh Codex sessions:

1. normal Codex
2. Codex wrapped by Caveman

The relevant metric is provider/session input + output usage, not response length alone.

## Install

Requires Node.js 18+.

```bash
npm install -g @caveman-ai/cli
caveman setup --install
```

Verify:

```bash
caveman --help
codex --version
```

## A/B test

Use a clean working tree and start both runs from the same commit.

### A — baseline Codex

```bash
cd VaultBridge
git switch experiment/caveman-codex
codex
```

### B — Caveman-wrapped Codex

Start a fresh session from the same repository state:

```bash
cd VaultBridge
git switch experiment/caveman-codex
caveman wrap codex
```

If persistent wrapping is preferred after the test:

```bash
caveman codex
```

## Suggested test task

Use exactly the same prompt in both runs, for example:

> Inspect the current semantic search implementation. Explain the retrieval pipeline, identify the three biggest likely performance or memory costs, and propose the smallest measurable optimization. Do not modify files.

A read-heavy task is useful because Caveman Proxy claims its largest benefit from reducing context/tool payloads rather than only making final answers shorter.

## Record

For each run record:

| Metric | Baseline | Caveman |
|---|---:|---:|
| Input tokens | | |
| Output tokens | | |
| Cached/context tokens if reported | | |
| Total tokens | | |
| Wall time | | |
| Correctness / useful findings | | |
| Files/tool output read | | |

Also collect:

```bash
caveman stats
```

For a built-in A/B workflow, Caveman also exposes:

```bash
caveman trial -- codex
caveman trial report
```

## Decision rule

Keep Caveman only if repeated equivalent tasks show lower total provider-reported usage without worse retrieval quality, missed code paths, additional retries, or materially higher latency.

Do not infer savings from shorter final answers alone. The Caveman project explicitly notes that its response skill adds prompt overhead and can be net-negative on terse coding workloads; the proxy must therefore be measured on VaultBridge's actual workflow.

## Cleanup

Caveman is not added as a VaultBridge application dependency. Removing or abandoning this branch leaves the application unchanged.
