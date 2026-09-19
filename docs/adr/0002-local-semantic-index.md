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

Persisted embeddings are compatible only when the versioned semantic-index signature matches. The
signature includes the `v3-heading-context` content/chunking contract, configured model, effective
chunk size and overlap, and an embedding fingerprint. The fingerprint combines VaultBridge's
application-owned FastEmbed mean-pooling/float32/L2-normalization contract with SHA-256 hashes of the
resolved ONNX graph and tokenizer/configuration files actually used by FastEmbed.

FastEmbed is pinned exactly. A dependency upgrade may retain the application-owned backend contract
only after its pooling and adapter behavior are verified; an incompatible change requires a new
contract identifier. Runtime tuning such as embedding batch size and ONNX CPU memory-arena selection
does not define the embedding space.

## Rationale

- no per-query cloud embedding cost,
- vault content does not need to be sent to an embedding API,
- CPU inference is sufficient,
- SQLite keeps backup/recovery simple,
- the index is disposable derived data.

Fingerprint resolution precedes compatibility mutation. Until it succeeds, an old index is not
searchable. Resolution failure leaves recoverable rows and their stored signature intact, reports an
error, and permits a later retry. A known mismatch clears only derived index rows and causes one full
rebuild from Markdown. Rolling back to code with an older signature format is safe but causes another
derived-data rebuild; mixed-version writers remain unsupported.

## Consequences

Brute-force similarity may eventually become a bottleneck for very large vaults. An ANN/vector extension or external vector store should only be considered after benchmark evidence.
