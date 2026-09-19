from __future__ import annotations

import os
import subprocess
import sys


def test_e2e_collection_sanitizes_real_vaultbridge_environment():
    environment = os.environ.copy()
    environment.update(
        {
            "API_KEY": "must-not-be-loaded",
            "VAULT_PATH": "must-not-be-loaded",
            "SEMANTIC_DATA_PATH": "must-not-be-loaded",
            "SEMANTIC_CHUNK_CHARS": "invalid-real-value",
            "MCP_HTTP_ENABLED": "invalid-real-value",
        }
    )
    script = """
from pathlib import Path

import tests.e2e.conftest as e2e
from app.main import app

settings = app.state.settings
assert settings.api_key.get_secret_value() == e2e.E2E_API_KEY
assert settings.vault_path == e2e.COLLECTION_SAFE_VAULT_PATH.resolve()
assert settings.semantic_data_path == e2e.COLLECTION_SAFE_SEMANTIC_DATA_PATH
assert settings.semantic_watch_enabled is False
assert settings.rate_limit_enabled is False
assert settings.mcp_http_enabled is False
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
