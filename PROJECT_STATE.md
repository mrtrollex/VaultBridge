# Current Project State

This document is a snapshot for future Codex sessions so the agent does not have to rediscover the baseline.

## Baseline date

2026-08-23

## Working production characteristics

- FastAPI application
- Python 3.12 container
- current code concentrated in `app/main.py` and `app/semantic.py`
- local semantic model through FastEmbed/ONNX Runtime
- default model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- SQLite semantic index using WAL
- chunk configuration currently defaults to 600 chars / 100 overlap
- semantic search uses cosine similarity plus lexical/title/path reranking
- TrueNAS container commonly runs as UID/GID 568
- existing production deployment uses port 8765 → 8000
- separate `/vault` and `/data` mounts are used by the TrueNAS compose file

## Known limitations observed during real use

1. First semantic indexing can take a long time and appears to block the request.
2. Indexing progress is not visible through the health endpoint.
3. Search currently calls sync inline.
4. Current chunking is only partially Markdown-aware.
5. Application architecture is still prototype-sized and concentrated in two modules.
6. Default ranking thresholds require evaluation rather than ad-hoc tuning.
7. GPT clients can invent wikilinks unless client instructions explicitly require verified existing notes.

## Verified baseline tests

At preparation time:

```text
6 passed
```

using:

```bash
PYTHONPATH=. pytest -q
```

## Compatibility note

Do not rename existing TrueNAS paths/container names as part of a documentation-only/project-identity task. Treat runtime naming migration as an explicit later task.
