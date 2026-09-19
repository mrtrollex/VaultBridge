from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import uvicorn
from playwright.sync_api import ConsoleMessage, Page, Request, Response

from app.core.config import Settings
from app.repositories.semantic import SemanticRepository
from app.services.indexer import BackgroundSemanticIndexer
from app.services.semantic_search import SemanticSearchService
from app.services.vault import VaultService

E2E_API_KEY = "vaultbridge-e2e-only-key"
E2E_EMBEDDING_FINGERPRINT = "embedding-v1:" + ("0" * 64)
COLLECTION_SAFE_ROOT = Path(tempfile.gettempdir()) / "vaultbridge-e2e-unused-collection"
COLLECTION_SAFE_VAULT_PATH = COLLECTION_SAFE_ROOT / "vault"
COLLECTION_SAFE_SEMANTIC_DATA_PATH = COLLECTION_SAFE_ROOT / "semantic-data"


def _sanitize_collection_environment() -> None:
    for name in Settings.ENVIRONMENT_VARIABLES:
        os.environ.pop(name, None)
    os.environ.update(
        {
            "API_KEY": E2E_API_KEY,
            "VAULT_PATH": str(COLLECTION_SAFE_VAULT_PATH),
            "SEMANTIC_DATA_PATH": str(COLLECTION_SAFE_SEMANTIC_DATA_PATH),
            "SEMANTIC_WATCH_ENABLED": "false",
            "RATE_LIMIT_ENABLED": "false",
            "MCP_HTTP_ENABLED": "false",
        }
    )


_sanitize_collection_environment()


def _load_create_app():
    from app.main import create_app

    return create_app


create_app = _load_create_app()


class OfflineOnlyEmbedder:
    def embed(self, texts):
        raise AssertionError("The dashboard E2E suite must not invoke semantic embeddings")


@dataclass(frozen=True)
class E2EServer:
    base_url: str
    vault_path: Path


HttpFailure = tuple[str, int, str, str]


class BrowserFailureMonitor:
    def __init__(self, page: Page) -> None:
        self._page = page
        self._page_errors: list[str] = []
        self._console_errors: list[str] = []
        self._request_failures: list[str] = []
        self._http_failures: Counter[HttpFailure] = Counter()
        self._allowed_http_failures: Counter[HttpFailure] = Counter()
        page.on("pageerror", lambda error: self._page_errors.append(str(error)))
        page.on("console", self._record_console_error)
        page.on("requestfailed", self._record_request_failure)
        page.on("response", self._record_response)

    @staticmethod
    def _http_failure(method: str, status: int, url: str) -> HttpFailure:
        parsed = urlsplit(url)
        return method, status, parsed.path, parsed.query

    def allow_http_failure(self, method: str, status: int, url: str) -> None:
        self._allowed_http_failures[self._http_failure(method, status, url)] += 1

    def _record_console_error(self, message: ConsoleMessage) -> None:
        if message.type != "error":
            return
        if message.text.startswith("Failed to load resource:"):
            return
        self._console_errors.append(message.text)

    def _record_request_failure(self, request: Request) -> None:
        if request.response() is not None:
            return
        self._request_failures.append(f"{request.method} {request.url}: {request.failure}")

    def _record_response(self, response: Response) -> None:
        if response.status >= 400:
            failure = self._http_failure(response.request.method, response.status, response.url)
            self._http_failures[failure] += 1

    def assert_clean(self) -> None:
        self._page.evaluate("undefined")
        unexpected_http = self._http_failures - self._allowed_http_failures
        missing_http = self._allowed_http_failures - self._http_failures
        failures: list[str] = []
        if self._page_errors:
            failures.append(f"Unexpected page errors: {self._page_errors}")
        if self._console_errors:
            failures.append(f"Unexpected VaultBridge console errors: {self._console_errors}")
        if self._request_failures:
            failures.append(f"Unexpected failed requests: {self._request_failures}")
        if unexpected_http:
            failures.append(f"Unexpected HTTP failures: {list(unexpected_http.elements())}")
        if missing_http:
            failures.append(f"Expected HTTP failures were not observed: {list(missing_http.elements())}")
        assert failures == [], "\n".join(failures)


def _wait_for_liveness(base_url: str, thread: threading.Thread, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("Disposable VaultBridge server stopped during startup")
        try:
            with urllib.request.urlopen(f"{base_url}/health/live", timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as error:
            last_error = error
        threading.Event().wait(0.05)
    raise RuntimeError("Disposable VaultBridge server did not become live") from last_error


@pytest.fixture(scope="session")
def e2e_server() -> Iterator[E2EServer]:
    with tempfile.TemporaryDirectory(prefix="vaultbridge-e2e-") as temporary_root:
        root = Path(temporary_root)
        vault_path = root / "vault"
        semantic_data_path = root / "semantic-data"
        (vault_path / "Knowledge").mkdir(parents=True)
        semantic_data_path.mkdir()
        (vault_path / "Knowledge" / "Playwright Guide.md").write_text(
            "# Playwright Regression Guide\n\n"
            "The quartz lighthouse phrase proves literal retrieval through the real dashboard.\n",
            encoding="utf-8",
        )
        (vault_path / "Unrelated Archive.md").write_text(
            "# Unrelated Archive\n\nThis note discusses offline garden planning.\n",
            encoding="utf-8",
        )

        settings = Settings(
            API_KEY=E2E_API_KEY,
            VAULT_PATH=vault_path,
            SEMANTIC_DATA_PATH=semantic_data_path,
            RATE_LIMIT_ENABLED=False,
            SEMANTIC_WATCH_ENABLED=False,
        )
        semantic_service = SemanticSearchService(
            vault_root=vault_path,
            repository=SemanticRepository(semantic_data_path / "index.sqlite3"),
            embedder=OfflineOnlyEmbedder(),
            embedding_fingerprint=E2E_EMBEDDING_FINGERPRINT,
        )
        semantic_indexer = BackgroundSemanticIndexer(lambda _cancel_event: None)
        application = create_app(
            settings=settings,
            vault_service=VaultService(
                vault_root=vault_path,
                max_note_bytes=settings.max_note_bytes,
            ),
            semantic_search_service=semantic_service,
            semantic_indexer=semantic_indexer,
        )

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                application,
                host="127.0.0.1",
                port=port,
                access_log=False,
                log_level="warning",
                lifespan="on",
            )
        )
        thread = threading.Thread(
            target=server.run,
            kwargs={"sockets": [listener]},
            name="vaultbridge-e2e-server",
        )
        thread.start()
        base_url = f"http://127.0.0.1:{port}"
        try:
            _wait_for_liveness(base_url, thread)
            yield E2EServer(base_url=base_url, vault_path=vault_path)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            listener.close()
            if thread.is_alive():
                server.force_exit = True
                thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError("Disposable VaultBridge server did not stop")


@pytest.fixture
def browser_failures(page: Page) -> BrowserFailureMonitor:
    return BrowserFailureMonitor(page)
