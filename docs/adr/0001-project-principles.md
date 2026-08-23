# ADR 0001: VaultBridge project principles

- **Status:** Accepted
- **Date:** 2026-08-23

## Context

The project began as a personal bridge between ChatGPT and an Obsidian vault. It is being prepared as a reusable self-hosted application.

## Decision

VaultBridge will remain:

- Markdown-first,
- local-first for embeddings,
- minimal in API surface,
- single-container by default,
- SQLite-backed for derived semantic metadata,
- client-agnostic.

The project will not adopt distributed infrastructure without measured need.

## Consequences

This favors operational simplicity and inspectability over maximum theoretical scale. The semantic index may be rebuilt from the vault at any time.
