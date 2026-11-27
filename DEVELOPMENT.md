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

### Opt in to parallel pytest

Install the optional plugin in the same development environment that runs the checks.

```text
python -m pip install pytest-xdist==3.8.0
```

The managed workflow defaults to the serial commands. To preview the parallel command or use it
for managed validation, pass `--parallel-pytest`.

```text
python scripts/agent_check.py --dry-run --parallel-pytest
python scripts/agent_finish.py --task-file path/to/task.md --parallel-pytest
```

The flag adds four workers with load scheduling and zero worker restarts to the selected required
Python suite. Browser E2E and the other checks retain their existing commands. The validation report
records the mode and exact pytest command. The Python profiles clear inherited `PYTEST_ADDOPTS` so
those options cannot change the managed command's scope or worker count.

Run either Python suite directly with the same worker settings.

```text
python -m pytest -q --ignore=tests/e2e -n 4 --dist load --max-worker-restart 0
python -m pytest -q tests/test_agent_task.py tests/test_agent_check.py tests/test_agent_finish.py tests/test_agent_review.py tests/test_agent_validation.py tests/test_agent_evidence.py -n 4 --dist load --max-worker-restart 0
```

Phase 3D measured full-suite wall-time reductions of about 72% on Windows and 74% on native WSL.
The six-module suite was about 64–67% faster on the same 6-core host. Four workers duplicate
collection and fixtures and raised peak sampled full-suite memory to about 1.14 GiB on that host.
Actual timing and resource use depend on the machine and checkout.

An absent or different plugin version makes a selected parallel check unavailable. Install the
pinned version or omit `--parallel-pytest` for a separate serial run. Managed parallel validation
loads a guard that fails the check when xdist reports a worker crash, including before scheduling.
For diagnosis, run the failing node with serial pytest or remove the
parallel arguments from the direct suite command.

## Run a disposable application

Follow [Docker setup](docs/DOCKER.md) with a synthetic vault path and a generated throwaway key.
Use [named setup](docs/KNOWLEDGE_SPACES.md) only when the task requires it; generic Compose does not
forward named JSON automatically. Do not use a real user vault or an existing production data layout.
Do not paste resolved Compose configuration, credentials or environment dumps into an issue.

For a model-enabled experiment, wait for `/health/ready` and inspect `/health` diagnostics;
liveness alone does not establish semantic availability. Compatible prior indexes can remain
searchable while a refresh is indexing or has failed. See the [API/health guide](docs/API.md).
Stop serving/stdio before CLI writes or maintenance on the same writable semantic state.
