# ADR 0002: Local embeddings and SQLite semantic index

- **Status:** Accepted
- **Date:** 2026-08-23

## Context

VaultBridge needs concept-based retrieval for a multilingual personal knowledge base while remaining practical on CPU-only home-server hardware.

## Decision

Use a local FastEmbed/ONNX embedding model and persist normalized chunk embeddings plus metadata in SQLite.

The current default model is:

```text
sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

## Rationale

- no per-query cloud embedding cost,
- vault content does not need to be sent to an embedding API,
- CPU inference is sufficient,
- SQLite keeps backup/recovery simple,
- the index is disposable derived data.

## Consequences

Brute-force similarity may eventually become a bottleneck for very large vaults. An ANN/vector extension or external vector store should only be considered after benchmark evidence.
