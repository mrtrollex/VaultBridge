from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import DEFAULT_SEMANTIC_MODEL, Settings
from app.semantic import semantic_index_from_settings


def test_configuration_defaults_match_existing_behavior():
    settings = Settings.from_env({})

    assert settings.api_key.get_secret_value() == ""
    assert settings.vault_path == Path("/vault").resolve()
    assert settings.max_note_bytes == 1_000_000
    assert settings.semantic_data_path == Path("/vault/.obsidian-chatgpt-data")
    assert settings.semantic_model == DEFAULT_SEMANTIC_MODEL
    assert settings.semantic_chunk_chars == 600
    assert settings.semantic_chunk_overlap == 100


def test_configuration_environment_overrides(tmp_path):
    settings = Settings.from_env(
        {
            "API_KEY": "test-secret-value",
            "VAULT_PATH": str(tmp_path / "vault"),
            "MAX_NOTE_BYTES": "2048",
            "SEMANTIC_DATA_PATH": str(tmp_path / "semantic-data"),
            "SEMANTIC_MODEL": "example/model",
            "SEMANTIC_CHUNK_CHARS": "800",
            "SEMANTIC_CHUNK_OVERLAP": "200",
        }
    )

    assert settings.api_key.get_secret_value() == "test-secret-value"
    assert settings.vault_path == (tmp_path / "vault").resolve()
    assert settings.max_note_bytes == 2048
    assert settings.semantic_data_path == tmp_path / "semantic-data"
    assert settings.semantic_model == "example/model"
    assert settings.semantic_chunk_chars == 800
    assert settings.semantic_chunk_overlap == 200


def test_semantic_index_uses_typed_configuration(tmp_path):
    settings = Settings(
        vault_path=tmp_path / "vault",
        max_note_bytes=4096,
        semantic_data_path=tmp_path / "semantic-data",
        semantic_model="example/model",
        semantic_chunk_chars=800,
        semantic_chunk_overlap=200,
    )

    index = semantic_index_from_settings(settings)

    assert index.vault_root == (tmp_path / "vault").resolve()
    assert index.db_path == tmp_path / "semantic-data" / "semantic-index.sqlite3"
    assert index.cache_dir == tmp_path / "semantic-data" / "models"
    assert index.model_name == "example/model"
    assert index.max_note_bytes == 4096
    assert index.chunk_chars == 800
    assert index.chunk_overlap == 200


@pytest.mark.parametrize(
    ("environment", "expected_error"),
    [
        ({"MAX_NOTE_BYTES": "not-an-integer"}, "MAX_NOTE_BYTES"),
        ({"MAX_NOTE_BYTES": "0"}, "MAX_NOTE_BYTES"),
        ({"SEMANTIC_CHUNK_CHARS": "249"}, "SEMANTIC_CHUNK_CHARS"),
        ({"SEMANTIC_CHUNK_OVERLAP": "-1"}, "SEMANTIC_CHUNK_OVERLAP"),
        ({"SEMANTIC_CHUNK_OVERLAP": "301"}, "SEMANTIC_CHUNK_OVERLAP"),
    ],
)
def test_invalid_numeric_configuration_fails_with_environment_name(environment, expected_error):
    with pytest.raises(ValidationError, match=expected_error):
        Settings.from_env(environment)


@pytest.mark.parametrize("environment_name", ["VAULT_PATH", "SEMANTIC_DATA_PATH", "SEMANTIC_MODEL"])
def test_empty_path_or_model_configuration_is_rejected(environment_name):
    with pytest.raises(ValidationError, match=environment_name):
        Settings.from_env({environment_name: " "})


def test_api_key_is_redacted_from_settings_representation():
    secret = "must-not-appear-in-repr"
    settings = Settings.from_env({"API_KEY": secret})

    assert secret not in repr(settings)
