# Contributing to VaultBridge

Thanks for considering a contribution.

## Before coding

1. Check `ROADMAP.md` and `BACKLOG.md`.
2. Open/choose one well-scoped issue.
3. For architecture-changing work, add or update an ADR under `docs/adr/`.

## Development expectations

- preserve Markdown as the source of truth,
- keep the default deployment single-container + SQLite,
- add tests for changed behaviour,
- never include a real vault, API key, private hostname, or generated embedding database in fixtures.

## Checks

```bash
PYTHONPATH=. pytest -q
python -m compileall -q app
```

Docker-related changes should additionally pass:

```bash
docker compose config
docker build -t vaultbridge:test .
```

## Pull requests

Describe:

- problem being solved,
- approach,
- user-visible/API changes,
- tests run,
- migration/reindex impact,
- security considerations where applicable.
