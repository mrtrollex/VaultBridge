# Development

Target runtime: **Python 3.12**. Read [AGENTS.md](AGENTS.md) and the exact task first;
[CONTRIBUTING.md](CONTRIBUTING.md) and the [Codex Playbook](docs/CODEX_PLAYBOOK.md) own contribution
and packet/review workflow. Use disposable synthetic Markdown for development and tests.

## Set up Python

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

## Run checks

From the repository root with the environment active:

```text
python -m ruff check app tests scripts
python -m pytest -q --ignore=tests/e2e
python -m compileall -q app
```

Unit/integration tests use fake embedders and must not download real models. Real-model benchmarks
are separate explicit experiments; see [evaluation tooling](tests/eval/README.md).
Native filesystem/lifecycle suites have platform-specific behavior; report actual skips and failures.

Dashboard regression checks need Chromium:

```text
python -m playwright install chromium
python -m pytest -q tests/e2e --browser=chromium --tracing=retain-on-failure --output=test-results/playwright
```

Use `python scripts/agent_check.py --dry-run` to inspect change-aware selection and
`python scripts/agent_finish.py --task-file path/to/task.md` for completion. A failed or required-but-
unavailable check is incomplete; manual reminders and independent CI remain separate requirements.

## Run a disposable application

Follow [Docker setup](docs/DOCKER.md) with a synthetic vault path and a generated throwaway key.
Use [named setup](docs/KNOWLEDGE_SPACES.md) only when the task requires it; generic Compose does not
forward named JSON automatically. Do not use a real user vault or an existing production data layout.
Do not paste resolved Compose configuration, credentials or environment dumps into an issue.

For a model-enabled experiment, wait for `/health/ready` and inspect `/health` diagnostics;
liveness alone does not establish semantic availability. Compatible prior indexes can remain
searchable while a refresh is indexing or has failed. See the [API/health guide](docs/API.md).
Stop serving/stdio before CLI writes or maintenance on the same writable semantic state.
