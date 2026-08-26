from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationInfo, field_validator, model_validator

DEFAULT_SEMANTIC_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class Settings(BaseModel):
    """Validated application settings loaded from the process environment."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, validate_default=True)

    ENVIRONMENT_VARIABLES: ClassVar[tuple[str, ...]] = (
        "API_KEY",
        "API_KEY_PREVIOUS",
        "VAULT_PATH",
        "MAX_NOTE_BYTES",
        "SEMANTIC_DATA_PATH",
        "SEMANTIC_MODEL",
        "SEMANTIC_CHUNK_CHARS",
        "SEMANTIC_CHUNK_OVERLAP",
        "SEMANTIC_INDEX_BATCH_SIZE",
    )

    api_key: SecretStr = Field(default=SecretStr(""), alias="API_KEY", repr=False)
    previous_api_key: SecretStr = Field(
        default=SecretStr(""),
        alias="API_KEY_PREVIOUS",
        repr=False,
    )
    vault_path: Path = Field(default=Path("/vault"), alias="VAULT_PATH")
    max_note_bytes: int = Field(default=1_000_000, alias="MAX_NOTE_BYTES", gt=0)
    semantic_data_path: Path = Field(
        default=Path("/vault/.obsidian-chatgpt-data"),
        alias="SEMANTIC_DATA_PATH",
    )
    semantic_model: str = Field(default=DEFAULT_SEMANTIC_MODEL, alias="SEMANTIC_MODEL")
    semantic_chunk_chars: int = Field(default=600, alias="SEMANTIC_CHUNK_CHARS", ge=250)
    semantic_chunk_overlap: int = Field(default=100, alias="SEMANTIC_CHUNK_OVERLAP", ge=0)
    semantic_index_batch_size: int = Field(default=25, alias="SEMANTIC_INDEX_BATCH_SIZE", gt=0)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if environ is None else environ
        values = {name: source[name] for name in cls.ENVIRONMENT_VARIABLES if name in source}
        return cls.model_validate(values)

    @field_validator("vault_path", "semantic_data_path", mode="before")
    @classmethod
    def validate_path(cls, value: object, info: ValidationInfo) -> Path:
        if value is None or not str(value).strip():
            environment_name = info.field_name.upper()
            raise ValueError(f"{environment_name} must not be empty")
        return Path(str(value).strip()).expanduser()

    @field_validator("vault_path")
    @classmethod
    def resolve_vault_path(cls, value: Path) -> Path:
        return value.resolve()

    @field_validator("semantic_model")
    @classmethod
    def validate_semantic_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("SEMANTIC_MODEL must not be empty")
        return value

    @model_validator(mode="after")
    def validate_chunk_overlap(self) -> Settings:
        maximum_overlap = self.semantic_chunk_chars // 2
        if self.semantic_chunk_overlap > maximum_overlap:
            raise ValueError(
                "SEMANTIC_CHUNK_OVERLAP must be less than or equal to half of SEMANTIC_CHUNK_CHARS"
            )
        return self
