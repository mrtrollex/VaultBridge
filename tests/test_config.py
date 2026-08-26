from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import DEFAULT_SEMANTIC_MODEL, Settings
from app.services.semantic_search import semantic_search_service_from_settings


def test_configuration_defaults_match_existing_behavior():
    settings = Settings.from_env({})

    assert settings.api_key.get_secret_value() == ""
    assert settings.previous_api_key.get_secret_value() == ""
    assert settings.vault_path == Path("/vault").resolve()
    assert settings.max_note_bytes == 1_000_000
    assert settings.semantic_data_path == Path("/vault/.obsidian-chatgpt-data")
    assert settings.semantic_model == DEFAULT_SEMANTIC_MODEL
    assert settings.semantic_chunk_chars == 600
    assert settings.semantic_chunk_overlap == 100
    assert settings.semantic_index_batch_size == 25
    assert settings.semantic_watch_enabled is False
    assert settings.semantic_watch_debounce_seconds == 1.0
    assert settings.rate_limit_enabled is True
    assert settings.rate_limit_requests == 120
    assert settings.rate_limit_window_seconds == 60
    assert settings.rate_limit_max_clients == 1024


def test_configuration_environment_overrides(tmp_path):
    settings = Settings.from_env(
        {
            "API_KEY": "test-secret-value",
            "API_KEY_PREVIOUS": "test-previous-secret-value",
            "VAULT_PATH": str(tmp_path / "vault"),
            "MAX_NOTE_BYTES": "2048",
            "SEMANTIC_DATA_PATH": str(tmp_path / "semantic-data"),
            "SEMANTIC_MODEL": "example/model",
            "SEMANTIC_CHUNK_CHARS": "800",
            "SEMANTIC_CHUNK_OVERLAP": "200",
            "SEMANTIC_INDEX_BATCH_SIZE": "10",
            "SEMANTIC_WATCH_ENABLED": "true",
            "SEMANTIC_WATCH_DEBOUNCE_SECONDS": "0.25",
            "RATE_LIMIT_ENABLED": "false",
            "RATE_LIMIT_REQUESTS": "40",
            "RATE_LIMIT_WINDOW_SECONDS": "30",
            "RATE_LIMIT_MAX_CLIENTS": "256",
        }
    )

    assert settings.api_key.get_secret_value() == "test-secret-value"
    assert settings.previous_api_key.get_secret_value() == "test-previous-secret-value"
    assert settings.vault_path == (tmp_path / "vault").resolve()
    assert settings.max_note_bytes == 2048
    assert settings.semantic_data_path == tmp_path / "semantic-data"
    assert settings.semantic_model == "example/model"
    assert settings.semantic_chunk_chars == 800
    assert settings.semantic_chunk_overlap == 200
    assert settings.semantic_index_batch_size == 10
    assert settings.semantic_watch_enabled is True
    assert settings.semantic_watch_debounce_seconds == 0.25
    assert settings.rate_limit_enabled is False
    assert settings.rate_limit_requests == 40
    assert settings.rate_limit_window_seconds == 30
    assert settings.rate_limit_max_clients == 256


def test_semantic_search_service_uses_typed_configuration(tmp_path):
    settings = Settings(
        vault_path=tmp_path / "vault",
        max_note_bytes=4096,
        semantic_data_path=tmp_path / "semantic-data",
        semantic_model="example/model",
        semantic_chunk_chars=800,
        semantic_chunk_overlap=200,
        semantic_index_batch_size=10,
    )

    service = semantic_search_service_from_settings(settings)

    assert service.vault_root == (tmp_path / "vault").resolve()
    assert service.repository.db_path == tmp_path / "semantic-data" / "semantic-index.sqlite3"
    assert service.cache_dir == tmp_path / "semantic-data" / "models"
    assert service.model_name == "example/model"
    assert service.max_note_bytes == 4096
    assert service.chunk_chars == 800
    assert service.chunk_overlap == 200
    assert service.index_batch_size == 10


@pytest.mark.parametrize(
    ("environment", "expected_error"),
    [
        ({"MAX_NOTE_BYTES": "not-an-integer"}, "MAX_NOTE_BYTES"),
        ({"MAX_NOTE_BYTES": "0"}, "MAX_NOTE_BYTES"),
        ({"SEMANTIC_CHUNK_CHARS": "249"}, "SEMANTIC_CHUNK_CHARS"),
        ({"SEMANTIC_CHUNK_OVERLAP": "-1"}, "SEMANTIC_CHUNK_OVERLAP"),
        ({"SEMANTIC_CHUNK_OVERLAP": "301"}, "SEMANTIC_CHUNK_OVERLAP"),
        ({"SEMANTIC_INDEX_BATCH_SIZE": "0"}, "SEMANTIC_INDEX_BATCH_SIZE"),
        ({"SEMANTIC_INDEX_BATCH_SIZE": "not-an-integer"}, "SEMANTIC_INDEX_BATCH_SIZE"),
        ({"SEMANTIC_WATCH_ENABLED": "sometimes"}, "SEMANTIC_WATCH_ENABLED"),
        ({"SEMANTIC_WATCH_DEBOUNCE_SECONDS": "0"}, "SEMANTIC_WATCH_DEBOUNCE_SECONDS"),
        (
            {"SEMANTIC_WATCH_DEBOUNCE_SECONDS": "not-a-number"},
            "SEMANTIC_WATCH_DEBOUNCE_SECONDS",
        ),
        ({"SEMANTIC_WATCH_DEBOUNCE_SECONDS": "inf"}, "SEMANTIC_WATCH_DEBOUNCE_SECONDS"),
        ({"SEMANTIC_WATCH_DEBOUNCE_SECONDS": "nan"}, "SEMANTIC_WATCH_DEBOUNCE_SECONDS"),
        ({"RATE_LIMIT_ENABLED": "sometimes"}, "RATE_LIMIT_ENABLED"),
        ({"RATE_LIMIT_REQUESTS": "0"}, "RATE_LIMIT_REQUESTS"),
        ({"RATE_LIMIT_REQUESTS": "not-an-integer"}, "RATE_LIMIT_REQUESTS"),
        ({"RATE_LIMIT_WINDOW_SECONDS": "0"}, "RATE_LIMIT_WINDOW_SECONDS"),
        ({"RATE_LIMIT_WINDOW_SECONDS": "not-an-integer"}, "RATE_LIMIT_WINDOW_SECONDS"),
        ({"RATE_LIMIT_MAX_CLIENTS": "0"}, "RATE_LIMIT_MAX_CLIENTS"),
        ({"RATE_LIMIT_MAX_CLIENTS": "not-an-integer"}, "RATE_LIMIT_MAX_CLIENTS"),
    ],
)
def test_invalid_numeric_configuration_fails_with_environment_name(environment, expected_error):
    with pytest.raises(ValidationError, match=expected_error):
        Settings.from_env(environment)


@pytest.mark.parametrize("environment_name", ["VAULT_PATH", "SEMANTIC_DATA_PATH", "SEMANTIC_MODEL"])
def test_empty_path_or_model_configuration_is_rejected(environment_name):
    with pytest.raises(ValidationError, match=environment_name):
        Settings.from_env({environment_name: " "})


def test_api_keys_are_redacted_from_settings_representations():
    current_secret = "current-must-not-appear-in-repr"
    previous_secret = "previous-must-not-appear-in-repr"
    settings = Settings.from_env(
        {
            "API_KEY": current_secret,
            "API_KEY_PREVIOUS": previous_secret,
        }
    )

    representations = (repr(settings), str(settings), repr(settings.model_dump()))
    for representation in representations:
        assert current_secret not in representation
        assert previous_secret not in representation
