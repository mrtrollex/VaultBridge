# Development

## Python

Target runtime: Python 3.12.

Create a virtual environment and install dev dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

## Run tests

```bash
PYTHONPATH=. pytest -q
python -m compileall -q app
```

PowerShell:

```powershell
$env:PYTHONPATH = "."
pytest -q
python -m compileall -q app
```

Tests must use fake embedders and must not download the real embedding model.

## Docker

```bash
docker compose config
docker compose up -d --build
curl http://127.0.0.1:8765/health
```

## Verify semantic search after startup

Startup synchronizes the semantic index in the background. Wait for `/health` to report `semantic_index_ready: true`, then run:

Before the first index is ready, semantic search returns no results while indexing and HTTP `503`
after a failed initial synchronization. Restarting the application retries synchronization.

```bash
curl -X POST http://127.0.0.1:8765/api/v1/notes/related \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"professional courses and certifications","limit":5}'
```

## Pull request scope

Prefer one backlog item per PR. Refactors and feature changes should not be mixed unless one strictly enables the other.
