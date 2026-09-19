from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from itertools import batched
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol, runtime_checkable

import numpy as np

from app.core.config import DEFAULT_SEMANTIC_MODEL, Settings
from app.core.logging import log_event
from app.repositories.semantic import (
    ImmutableIndexInspectionUnavailableError,
    SemanticIndexStatus,
    SemanticRepository,
    StoredChunk,
    StoredNote,
)
from app.services.vault import SEMANTIC_EXCLUDED_DIRECTORIES, eligible_markdown_files

DEFAULT_MODEL = DEFAULT_SEMANTIC_MODEL
INDEX_STATE_METADATA_KEY = "index_state"
LAST_SUCCESSFUL_SYNC_METADATA_KEY = "last_successful_sync"
INDEX_FORMAT_VERSION = "v3-heading-context"
INDEX_SIGNATURE_PREFIX = "semantic-index-v2:"
EMBEDDING_FINGERPRINT_PREFIX = "embedding-v1:"
FASTEMBED_BACKEND_CONTRACT_ID = (
    "fastembed-dense-text/attention-mask-mean/"
    "vaultbridge-float32-flat-l2/v1"
)
FASTEMBED_POOLED_IMPLEMENTATION = "fastembed.text.pooled_embedding.PooledEmbedding"
TOKENIZER_ARTIFACT_FILES = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
)
HEADING_DIGEST_LENGTH = 12
SEMANTIC_RANK_WEIGHT = 1.0
LEXICAL_RANK_WEIGHT = 0.70
HYBRID_RANK_WEIGHT_TOTAL = SEMANTIC_RANK_WEIGHT + LEXICAL_RANK_WEIGHT
RELATIVE_RESULT_FLOOR = 0.78

logger = logging.getLogger("vaultbridge.semantic")


class IndexState(str, Enum):
    UNINITIALIZED = "uninitialized"
    INDEXING = "indexing"
    READY = "ready"
    ERROR = "error"


class SemanticSearchUnavailableError(RuntimeError):
    """No compatible completed semantic index is currently searchable."""


class SynchronizationCancelledError(RuntimeError):
    """Semantic synchronization stopped cooperatively at a safe boundary."""


class SemanticIndexOperationError(RuntimeError):
    """An expected semantic index I/O or embedding operation failed."""


class TargetedSynchronizationError(RuntimeError):
    """A requested note could not be authoritatively refreshed."""


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[np.ndarray]: ...


@runtime_checkable
class EmbeddingFingerprintProvider(Protocol):
    def resolve_embedding_fingerprint(self) -> str: ...


@runtime_checkable
class EmbeddingBackendValidator(Protocol):
    def validate_embedding_backend(self) -> None: ...


@runtime_checkable
class EmbeddingCompatibilityCache(Protocol):
    def invalidate_embedding_compatibility_cache(self) -> None: ...


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for block in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_embedding_fingerprint(
    *,
    model_dir: Path,
    model_file: str,
    backend_contract_id: str,
) -> str:
    """Hash the effective inference files and explicit adapter contract."""
    try:
        resolved_model_dir = model_dir.resolve(strict=True)
    except OSError:
        raise SemanticIndexOperationError(
            "FastEmbed resolved model directory is unavailable"
        ) from None
    logical_files = (model_file, *TOKENIZER_ARTIFACT_FILES)
    artifacts: list[dict[str, str]] = []
    for logical_name in logical_files:
        normalized_name = PurePosixPath(logical_name.replace("\\", "/"))
        if normalized_name.is_absolute() or ".." in normalized_name.parts:
            raise SemanticIndexOperationError(
                f"FastEmbed artifact path is invalid: {logical_name}"
            )
        artifact_path = resolved_model_dir / Path(*normalized_name.parts)
        try:
            artifact_is_file = artifact_path.is_file()
        except OSError:
            artifact_is_file = False
        if not artifact_is_file:
            raise SemanticIndexOperationError(
                f"FastEmbed artifact is unavailable: {logical_name}"
            )
        try:
            artifact_digest = _sha256_file(artifact_path)
        except OSError:
            raise SemanticIndexOperationError(
                f"FastEmbed artifact could not be read: {logical_name}"
            ) from None
        artifacts.append({"name": normalized_name.as_posix(), "sha256": artifact_digest})

    manifest = {
        "artifacts": sorted(artifacts, key=lambda artifact: artifact["name"]),
        "backend_contract": backend_contract_id,
        "schema": 1,
    }
    encoded = json.dumps(
        manifest,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{EMBEDDING_FINGERPRINT_PREFIX}{hashlib.sha256(encoded).hexdigest()}"


class FastEmbedder:
    """Load the ONNX embedding model only when semantic search is first used."""

    def __init__(
        self,
        model_name: str,
        cache_dir: Path,
        *,
        batch_size: int = 4,
        enable_cpu_mem_arena: bool = True,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.batch_size = batch_size
        self.enable_cpu_mem_arena = enable_cpu_mem_arena
        self._model = None
        self._embedding_fingerprint: str | None = None
        self._lock = threading.Lock()

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                try:
                    from fastembed import TextEmbedding
                except ImportError as exc:  # pragma: no cover
                    raise SemanticIndexOperationError("fastembed is not installed") from exc
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = TextEmbedding(
                    model_name=self.model_name,
                    cache_dir=str(self.cache_dir),
                    providers=["CPUExecutionProvider"],
                    enable_cpu_mem_arena=self.enable_cpu_mem_arena,
                    lazy_load=True,
                )
        return self._model

    def resolve_embedding_fingerprint(self) -> str:
        if self._embedding_fingerprint is not None:
            return self._embedding_fingerprint

        model = self._get_model()
        with self._lock:
            if self._embedding_fingerprint is not None:
                return self._embedding_fingerprint
            resolved_model = getattr(model, "model", None)
            implementation = (
                f"{type(resolved_model).__module__}.{type(resolved_model).__qualname__}"
            )
            if (
                self.model_name.casefold() == DEFAULT_MODEL.casefold()
                and implementation != FASTEMBED_POOLED_IMPLEMENTATION
            ):
                raise SemanticIndexOperationError(
                    "FastEmbed did not resolve the expected attention-mask mean-pooling "
                    "implementation"
                )
            backend_contract_id = (
                FASTEMBED_BACKEND_CONTRACT_ID
                if implementation == FASTEMBED_POOLED_IMPLEMENTATION
                else (
                    "fastembed-dense-text/"
                    f"model-specific:{implementation}/"
                    "vaultbridge-float32-flat-l2/v1"
                )
            )
            model_dir = getattr(resolved_model, "_model_dir", None)
            description = getattr(resolved_model, "model_description", None)
            model_file = getattr(description, "model_file", None)
            if not isinstance(model_dir, Path) or not isinstance(model_file, str):
                raise SemanticIndexOperationError(
                    "FastEmbed did not expose the resolved model artifacts"
                )
            self._embedding_fingerprint = build_embedding_fingerprint(
                model_dir=model_dir,
                model_file=model_file,
                backend_contract_id=backend_contract_id,
            )
            return self._embedding_fingerprint

    def invalidate_embedding_compatibility_cache(self) -> None:
        """Discard artifact identity and partially initialized lazy backend state."""
        with self._lock:
            self._embedding_fingerprint = None
            self._model = None

    def validate_embedding_backend(self) -> None:
        """Force one real inference before an incompatible persisted index is discarded."""
        try:
            vectors = self.embed(("VaultBridge semantic compatibility probe",))
        except SemanticIndexOperationError:
            raise
        except Exception as exc:
            raise SemanticIndexOperationError(
                "FastEmbed backend validation failed"
            ) from exc

        if len(vectors) != 1:
            raise SemanticIndexOperationError(
                "FastEmbed backend validation returned an unexpected vector count"
            )
        vector = np.asarray(vectors[0], dtype=np.float32).reshape(-1)
        if (
            vector.size == 0
            or not np.all(np.isfinite(vector))
            or float(np.linalg.norm(vector)) == 0
        ):
            raise SemanticIndexOperationError(
                "FastEmbed backend validation returned an invalid embedding"
            )

    def embed(self, texts: Sequence[str]) -> list[np.ndarray]:
        if not texts:
            return []
        model = self._get_model()
        return [
            np.asarray(vector, dtype=np.float32)
            for vector in model.embed(list(texts), batch_size=self.batch_size)
        ]


@dataclass(frozen=True)
class SemanticResult:
    path: str
    title: str
    score: float
    semantic_score: float
    lexical_score: float
    snippet: str
    heading: str | None


@dataclass(frozen=True)
class _LexicalScoreComponents:
    title: float
    path: float
    heading: float
    content: float
    exact_match: float

    @property
    def score(self) -> float:
        return min(1.0, self.title + self.path + self.heading + self.content + self.exact_match)


@dataclass(frozen=True)
class _RankedChunk:
    score: float
    semantic_score: float
    lexical_score: float
    chunk: StoredChunk

    @property
    def note_selection_key(self) -> tuple[float, float, float, int]:
        """Prefer relevance signals, then the earliest source chunk for an exact tie."""
        return (
            self.score,
            self.semantic_score,
            self.lexical_score,
            -self.chunk.chunk_index,
        )


@dataclass(frozen=True)
class SemanticHealthStatus:
    state: IndexState
    search_available: bool
    indexed_notes: int
    semantic_chunks: int
    last_successful_sync: str | None


@dataclass(frozen=True)
class SemanticIndexInspection:
    storage_exists: bool | None
    storage_initialized: bool
    storage_error: bool
    index_signature: str | None
    signature_compatible: bool
    persisted_state: str | None
    state: IndexState
    search_available: bool
    indexed_notes: int
    semantic_chunks: int
    last_successful_sync: str | None


@dataclass(frozen=True)
class _MarkdownSection:
    hierarchy: tuple[str, ...]
    content: str


@dataclass(frozen=True)
class _MarkdownSectionGroup:
    hierarchies: tuple[tuple[str, ...], ...]
    content: str


class SemanticSearchService:
    """Orchestrate incremental indexing and hybrid semantic search."""

    _STOPWORDS = {
        # English
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "i", "in", "is",
        "it", "my", "of", "on", "or", "that", "the", "this", "to", "was", "what", "when", "where",
        "which", "with", "you", "your",
        # Slovak / Czech words that are too common to help ranking.
        "a", "aby", "aj", "ako", "ale", "alebo", "bol", "bola", "bolo", "co", "čo", "do", "ho", "ich",
        "je", "ja", "ju", "k", "ked", "keď", "ma", "má", "mi", "moj", "môj", "na", "nie", "o", "od",
        "po", "pre", "pri", "sa", "si", "som", "su", "sú", "ta", "tak", "ten", "to", "tu", "v", "vo",
        "z", "za", "ze", "že",
    }

    def __init__(
        self,
        *,
        vault_root: Path,
        repository: SemanticRepository,
        model_name: str = DEFAULT_MODEL,
        cache_dir: Path | None = None,
        max_note_bytes: int = 1_000_000,
        chunk_chars: int = 600,
        chunk_overlap: int = 100,
        embed_batch_size: int = 4,
        onnx_cpu_mem_arena: bool = True,
        index_batch_size: int = 25,
        embedder: Embedder | None = None,
        embedding_fingerprint: str | None = None,
    ) -> None:
        self.vault_root = vault_root
        self.repository = repository
        self.model_name = model_name
        self.cache_dir = cache_dir or repository.db_path.parent / "models"
        self.max_note_bytes = max_note_bytes
        self.chunk_chars = max(250, chunk_chars)
        self.chunk_overlap = max(0, min(chunk_overlap, self.chunk_chars // 2))
        if index_batch_size <= 0:
            raise ValueError("index_batch_size must be positive")
        self.index_batch_size = index_batch_size
        if embedder is None:
            self.embedder = FastEmbedder(
                model_name,
                self.cache_dir,
                batch_size=embed_batch_size,
                enable_cpu_mem_arena=onnx_cpu_mem_arena,
            )
        else:
            self.embedder = embedder
        self._sync_lock = threading.Lock()
        self._configuration_lock = threading.Lock()
        self._availability_lock = threading.Lock()
        self._embed_lock = threading.Lock()
        self._fingerprint_lock = threading.Lock()
        self._search_available = False
        self._persisted_read_only_search = False
        self._state_init_lock = threading.Lock()
        self._state_initialized = False
        self._fingerprint_resolution_failed = False
        self._explicit_index_signature = (
            self._build_index_signature(embedding_fingerprint)
            if embedding_fingerprint is not None
            else None
        )
        self._resolved_index_signature = self._explicit_index_signature

    @property
    def db_path(self) -> Path:
        return self.repository.db_path

    @property
    def index_signature(self) -> str:
        return self._resolve_index_signature(persist_failure=False)

    def _build_index_signature(self, embedding_fingerprint: str) -> str:
        if not re.fullmatch(r"embedding-v1:[0-9a-f]{64}", embedding_fingerprint):
            raise ValueError("Embedding fingerprint must use the embedding-v1 SHA-256 format")
        payload = {
            "chunk_chars": self.chunk_chars,
            "chunk_overlap": self.chunk_overlap,
            "content_contract": INDEX_FORMAT_VERSION,
            "embedding_fingerprint": embedding_fingerprint,
            "model": self.model_name,
            "schema": 2,
        }
        return INDEX_SIGNATURE_PREFIX + json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    def _stored_signature_matches_configuration(self, signature: str | None) -> bool:
        if signature is None or not signature.startswith(INDEX_SIGNATURE_PREFIX):
            return False
        try:
            payload = json.loads(signature.removeprefix(INDEX_SIGNATURE_PREFIX))
        except (TypeError, ValueError):
            return False
        return (
            isinstance(payload, dict)
            and payload.get("schema") == 2
            and payload.get("content_contract") == INDEX_FORMAT_VERSION
            and payload.get("model") == self.model_name
            and payload.get("chunk_chars") == self.chunk_chars
            and payload.get("chunk_overlap") == self.chunk_overlap
            and isinstance(payload.get("embedding_fingerprint"), str)
            and re.fullmatch(
                r"embedding-v1:[0-9a-f]{64}",
                payload["embedding_fingerprint"],
            )
            is not None
        )

    def _resolve_index_signature(self, *, persist_failure: bool) -> str:
        if self._resolved_index_signature is not None:
            return self._resolved_index_signature
        with self._fingerprint_lock:
            if self._resolved_index_signature is not None:
                return self._resolved_index_signature
            try:
                if not isinstance(self.embedder, EmbeddingFingerprintProvider):
                    raise SemanticIndexOperationError(
                        "Embedding backend cannot resolve a compatibility fingerprint"
                    )
                embedding_fingerprint = self.embedder.resolve_embedding_fingerprint()
                try:
                    resolved_signature = self._build_index_signature(
                        embedding_fingerprint
                    )
                except (TypeError, ValueError) as exc:
                    raise SemanticIndexOperationError(
                        "Embedding fingerprint resolution failed"
                    ) from exc
            except SemanticIndexOperationError as exc:
                self._fingerprint_resolution_failed = True
                self._set_search_available(False)
                if persist_failure and self.repository.storage_initialized():
                    try:
                        self._persist_state(IndexState.ERROR)
                    except Exception:
                        pass
                log_event(
                    logger,
                    logging.ERROR,
                    "semantic_embedding_fingerprint_failed",
                    "Semantic embedding fingerprint resolution failed",
                    index_state=IndexState.ERROR,
                    error_type=type(exc).__name__,
                )
                raise

            self._resolved_index_signature = resolved_signature
            self._fingerprint_resolution_failed = False
            return resolved_signature

    def _invalidate_dynamic_compatibility_cache(self) -> None:
        with self._fingerprint_lock:
            if isinstance(self.embedder, EmbeddingCompatibilityCache):
                self.embedder.invalidate_embedding_compatibility_cache()
            self._resolved_index_signature = self._explicit_index_signature

    def _validate_backend_before_destructive_prepare(
        self,
        *,
        persist_failure: bool,
    ) -> None:
        if self._resolved_index_signature is None:
            raise SemanticIndexOperationError(
                "Embedding fingerprint has not been resolved"
            )

        storage = self.repository.read_status()
        needs_validation = (
            storage.storage_initialized
            and not storage.storage_error
            and storage.index_signature != self._resolved_index_signature
            and bool(storage.indexed_notes or storage.semantic_chunks)
        )
        if not needs_validation or not isinstance(
            self.embedder, EmbeddingBackendValidator
        ):
            return

        try:
            self.embedder.validate_embedding_backend()
        except Exception as exc:
            self._invalidate_dynamic_compatibility_cache()
            self._fingerprint_resolution_failed = True
            self._set_search_available(False)
            if persist_failure and self.repository.storage_initialized():
                try:
                    self._persist_state(IndexState.ERROR)
                except Exception:
                    pass
            log_event(
                logger,
                logging.ERROR,
                "semantic_embedding_backend_validation_failed",
                "Semantic embedding backend validation failed",
                index_state=IndexState.ERROR,
                error_type=type(exc).__name__,
            )
            if isinstance(exc, SemanticIndexOperationError):
                raise
            raise SemanticIndexOperationError(
                "Embedding backend validation failed"
            ) from exc

        self._fingerprint_resolution_failed = False

    def _persist_state(self, state: IndexState) -> IndexState:
        self.repository.set_metadata(INDEX_STATE_METADATA_KEY, state.value)
        return state

    def _read_persisted_state(self) -> IndexState:
        value = self.repository.get_metadata(INDEX_STATE_METADATA_KEY)
        try:
            return IndexState(value)
        except (TypeError, ValueError):
            return self._persist_state(IndexState.ERROR if value else IndexState.UNINITIALIZED)

    def _initialize_state(self, *, create_storage: bool) -> IndexState:
        if self._resolved_index_signature is None:
            try:
                self._resolve_index_signature(persist_failure=create_storage)
            except SemanticIndexOperationError:
                if create_storage:
                    raise
                return IndexState.ERROR

        if self._fingerprint_resolution_failed and not create_storage:
            return IndexState.ERROR
        if self._state_initialized:
            return self._read_persisted_state()

        with self._state_init_lock:
            if self._state_initialized:
                return self._read_persisted_state()

            if not create_storage:
                storage = self.repository.read_status()
                if not storage.storage_initialized:
                    return IndexState.UNINITIALIZED
                if storage.storage_error:
                    return IndexState.ERROR
                if storage.index_signature != self._resolved_index_signature:
                    return IndexState.UNINITIALIZED
                if storage.index_state is None:
                    return (
                        IndexState.READY
                        if storage.semantic_chunks
                        else IndexState.UNINITIALIZED
                    )
                try:
                    state = IndexState(storage.index_state)
                except ValueError:
                    return IndexState.ERROR
                return IndexState.ERROR if state is IndexState.INDEXING else state

            storage = self.repository.prepare_index(self.index_signature)
            if storage.signature_changed:
                state = IndexState.UNINITIALIZED
            elif storage.index_state is None:
                state = IndexState.READY if storage.has_chunks else IndexState.UNINITIALIZED
            else:
                try:
                    state = IndexState(storage.index_state)
                except ValueError:
                    state = IndexState.ERROR
                if state is IndexState.INDEXING:
                    state = IndexState.ERROR

            self._persist_state(state)
            self._state_initialized = True
            return state

    @property
    def state(self) -> IndexState:
        return self._initialize_state(create_storage=False)

    def is_storage_initialized(self) -> bool:
        return self.repository.storage_initialized()

    def is_ready(self) -> bool:
        return self.state is IndexState.READY

    def is_search_available(self) -> bool:
        with self._availability_lock:
            available = self._search_available
        if available:
            return True
        if self.state is IndexState.READY:
            with self._availability_lock:
                self._search_available = True
            return True
        return False

    def _stored_index_available_from_storage(
        self,
        *,
        storage_initialized: bool,
        storage_error: bool,
        index_signature: str | None,
        index_state: str | None,
        has_chunks: bool,
    ) -> bool:
        compatible_storage = (
            storage_initialized
            and not storage_error
            and self._resolved_index_signature is not None
            and index_signature == self._resolved_index_signature
        )
        if not compatible_storage:
            return False

        return index_state == IndexState.READY.value or (
            index_state is None and has_chunks
        )

    def _search_available_from_storage(
        self,
        *,
        storage_initialized: bool,
        storage_error: bool,
        index_signature: str | None,
        index_state: str | None,
        has_chunks: bool,
    ) -> bool:
        stored_index_available = self._stored_index_available_from_storage(
            storage_initialized=storage_initialized,
            storage_error=storage_error,
            index_signature=index_signature,
            index_state=index_state,
            has_chunks=has_chunks,
        )
        if not (
            storage_initialized
            and not storage_error
            and self._resolved_index_signature is not None
            and index_signature == self._resolved_index_signature
        ):
            return False
        with self._availability_lock:
            search_available = self._search_available
        return search_available or stored_index_available

    def _health_status_from_storage(self, storage: SemanticIndexStatus) -> SemanticHealthStatus:
        compatible_storage = (
            storage.storage_initialized
            and self._resolved_index_signature is not None
            and storage.index_signature == self._resolved_index_signature
        )
        if storage.storage_error:
            state = IndexState.ERROR
        elif self._fingerprint_resolution_failed:
            state = IndexState.ERROR
        elif not compatible_storage:
            state = IndexState.UNINITIALIZED
        elif storage.index_state is None:
            state = IndexState.READY if storage.semantic_chunks else IndexState.UNINITIALIZED
        else:
            try:
                state = IndexState(storage.index_state)
            except ValueError:
                state = IndexState.ERROR
            if state is IndexState.INDEXING and not self._state_initialized:
                state = IndexState.ERROR

        return SemanticHealthStatus(
            state=state,
            search_available=self._search_available_from_storage(
                storage_initialized=storage.storage_initialized,
                storage_error=storage.storage_error,
                index_signature=storage.index_signature,
                index_state=storage.index_state,
                has_chunks=bool(storage.semantic_chunks),
            ),
            indexed_notes=storage.indexed_notes if compatible_storage else 0,
            semantic_chunks=storage.semantic_chunks if compatible_storage else 0,
            last_successful_sync=storage.last_successful_sync if compatible_storage else None,
        )

    def health_status(self) -> SemanticHealthStatus:
        """Return a read-only lifecycle and storage snapshot for operator health reporting."""
        return self._health_status_from_storage(self.repository.read_status())

    def _inspection_from_storage(
        self,
        storage: SemanticIndexStatus,
    ) -> SemanticIndexInspection:
        compatible_storage = (
            storage.storage_initialized
            and not storage.storage_error
            and self._stored_signature_matches_configuration(storage.index_signature)
        )
        if storage.storage_error:
            state = IndexState.ERROR
        elif not compatible_storage:
            state = IndexState.UNINITIALIZED
        elif storage.index_state is None:
            state = IndexState.READY if storage.semantic_chunks else IndexState.UNINITIALIZED
        else:
            try:
                state = IndexState(storage.index_state)
            except ValueError:
                state = IndexState.ERROR

        return SemanticIndexInspection(
            storage_exists=storage.storage_exists,
            storage_initialized=storage.storage_initialized,
            storage_error=storage.storage_error,
            index_signature=storage.index_signature,
            signature_compatible=compatible_storage,
            persisted_state=storage.index_state,
            state=state,
            search_available=compatible_storage
            and (
                storage.index_state == IndexState.READY.value
                or (storage.index_state is None and bool(storage.semantic_chunks))
            ),
            indexed_notes=storage.indexed_notes,
            semantic_chunks=storage.semantic_chunks,
            last_successful_sync=storage.last_successful_sync,
        )

    def inspect_index(self) -> SemanticIndexInspection:
        """Return a persisted index snapshot suitable for in-process callers."""
        return self._inspection_from_storage(self.repository.read_status())

    def inspect_persisted_index(self) -> SemanticIndexInspection:
        """Return a filesystem-immutable snapshot for stopped/offline administration."""
        return self._inspection_from_storage(self.repository.read_immutable_status())

    def probe_search_availability(self) -> bool:
        """Return search availability from a minimal, read-only storage snapshot."""
        storage = self.repository.read_availability_status()
        return self._search_available_from_storage(
            storage_initialized=storage.storage_initialized,
            storage_error=storage.storage_error,
            index_signature=storage.index_signature,
            index_state=storage.index_state,
            has_chunks=storage.has_chunks,
        )

    def use_persisted_index_for_read_only_search(self) -> bool:
        """Enable this instance to search a compatible persisted index without lifecycle writes."""
        try:
            self._resolve_index_signature(persist_failure=False)
        except Exception:
            return False
        try:
            storage = self.repository.read_immutable_status()
        except ImmutableIndexInspectionUnavailableError:
            self._set_search_available(False)
            return False
        available = self._stored_index_available_from_storage(
            storage_initialized=storage.storage_initialized,
            storage_error=storage.storage_error,
            index_signature=storage.index_signature,
            index_state=storage.index_state,
            has_chunks=bool(storage.semantic_chunks),
        )
        self._set_search_available(available, persisted_read_only=available)
        return available

    def _set_search_available(
        self,
        available: bool,
        *,
        persisted_read_only: bool = False,
    ) -> None:
        with self._availability_lock:
            self._search_available = available
            self._persisted_read_only_search = persisted_read_only

    def _embed(self, texts: Sequence[str]) -> list[np.ndarray]:
        """Serialize only calls into one embedder instance; the surrounding pipelines stay concurrent."""
        with self._embed_lock:
            return self.embedder.embed(texts)

    @staticmethod
    def _parse_atx_heading_line(line: str) -> tuple[int, str] | None:
        """Return the ATX level and canonical label used by chunking and embedding deduplication."""
        line_without_ending = line.rstrip("\r\n")
        match = re.match(
            r"^[ \t]{0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$",
            line_without_ending,
        )
        if not match:
            return None
        label = re.sub(r"[ \t]+#+[ \t]*$", "", match.group(2) or "").strip()
        return len(match.group(1)), label

    @staticmethod
    def _build_embedding_text(title: str, heading: str | None, content: str) -> str:
        """Build stable embedding input without changing the persisted chunk content."""
        parts = [title]
        if heading:
            content_start = 0
            while content_start < len(content) and content[content_start] in "\r\n":
                content_start += 1
            line_end_candidates = (
                index
                for index in (
                    content.find("\r", content_start),
                    content.find("\n", content_start),
                )
                if index >= 0
            )
            line_end = min(line_end_candidates, default=len(content))
            first_line = content[content_start:line_end]
            parsed_heading = SemanticSearchService._parse_atx_heading_line(first_line)
            heading_is_at_start = first_line == heading or (
                parsed_heading is not None and parsed_heading[1] == heading
            )
            if not heading_is_at_start:
                parts.append(heading)
        parts.append(content)
        return "\n".join(parts)

    @staticmethod
    def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise SynchronizationCancelledError("Semantic index synchronization was cancelled")

    def reconfigure(self, *, vault_root: Path | None = None, max_note_bytes: int | None = None) -> None:
        with self._configuration_lock:
            if vault_root is not None:
                self.vault_root = vault_root
            if max_note_bytes is not None:
                self.max_note_bytes = max_note_bytes

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _strip_frontmatter(text: str) -> str:
        if not text.startswith("---\n"):
            return text
        end = text.find("\n---\n", 4)
        return text if end == -1 else text[end + 5 :]

    @staticmethod
    def _normalize(vector: np.ndarray) -> np.ndarray:
        vector = np.asarray(vector, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if norm == 0:
            raise SemanticIndexOperationError("Embedding model returned a zero vector")
        return vector / norm

    @staticmethod
    def _fold_text(text: str) -> str:
        """Case-fold and remove diacritics for lightweight lexical matching."""
        decomposed = unicodedata.normalize("NFKD", text.casefold())
        return "".join(character for character in decomposed if not unicodedata.combining(character))

    @classmethod
    def _terms(cls, text: str) -> set[str]:
        folded = cls._fold_text(text)
        return {
            token
            for token in re.findall(r"[\w-]+", folded, flags=re.UNICODE)
            if len(token) > 2 and token not in cls._STOPWORDS
        }

    @classmethod
    def _lexical_score_components(
        cls,
        query_text: str,
        *,
        path: str,
        heading: str | None,
        content: str,
    ) -> _LexicalScoreComponents:
        query_terms = cls._terms(query_text)
        if not query_terms:
            return _LexicalScoreComponents(0.0, 0.0, 0.0, 0.0, 0.0)

        def coverage(value: str) -> float:
            terms = cls._terms(value)
            return len(query_terms & terms) / len(query_terms) if terms else 0.0

        title = Path(path).stem
        title_cov = coverage(title)
        path_cov = coverage(path)
        heading_cov = coverage(heading or "")
        content_cov = coverage(content)

        folded_query = cls._fold_text(query_text).strip()
        exact_bonus = 0.0
        if len(folded_query) >= 5:
            folded_title = cls._fold_text(title)
            folded_content = cls._fold_text(content)
            if folded_query in folded_title:
                exact_bonus = 0.10
            elif folded_query in folded_content:
                exact_bonus = 0.05

        return _LexicalScoreComponents(
            title=0.40 * title_cov,
            path=0.25 * path_cov,
            heading=0.10 * heading_cov,
            content=0.20 * content_cov,
            exact_match=exact_bonus,
        )

    @classmethod
    def _lexical_score(
        cls,
        query_text: str,
        *,
        path: str,
        heading: str | None,
        content: str,
    ) -> float:
        return cls._lexical_score_components(
            query_text,
            path=path,
            heading=heading,
            content=content,
        ).score

    @staticmethod
    def _hybrid_score(semantic_score: float, lexical_score: float) -> float:
        """Combine bounded signals without saturating distinct candidates at 1.0."""
        normalized_score = (
            SEMANTIC_RANK_WEIGHT * semantic_score
            + LEXICAL_RANK_WEIGHT * lexical_score
        ) / HYBRID_RANK_WEIGHT_TOTAL
        return max(-1.0, min(1.0, normalized_score))

    @staticmethod
    def _readable_label(label: str, limit: int) -> str:
        if len(label) <= limit:
            return label
        if limit <= 1:
            return label[:limit]
        if limit == 2:
            return f"{label[0]}…"
        prefix_length = (limit - 1) // 2
        suffix_length = limit - 1 - prefix_length
        return f"{label[:prefix_length]}…{label[-suffix_length:]}"

    @classmethod
    def _digest_marker(cls, value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:HEADING_DIGEST_LENGTH]
        return f" [{digest}]"

    @staticmethod
    def _attach_digest(readable: str, marker: str) -> str:
        if " > " in readable:
            ancestors, leaf = readable.rsplit(" > ", 1)
            return f"{ancestors}{marker} > {leaf}"
        if "…" in readable:
            prefix, suffix = readable.rsplit("…", 1)
            return f"{prefix}…{marker}{suffix}"
        return f"{marker} {readable}".strip()

    @classmethod
    def _bounded_label(cls, label: str, limit: int) -> str:
        if len(label) <= limit:
            return label
        marker = cls._digest_marker(label)
        readable_limit = max(0, limit - len(marker))
        readable = cls._readable_label(label, readable_limit)
        return cls._attach_digest(readable, marker)[-limit:]

    @classmethod
    def _readable_hierarchy(cls, hierarchy: tuple[str, ...], limit: int) -> str | None:
        if not hierarchy or limit <= 0:
            return None

        result = cls._readable_label(hierarchy[-1], limit)
        for ancestor in reversed(hierarchy[:-1]):
            available = limit - len(result) - len(" > ")
            if available <= 0:
                break
            readable_ancestor = cls._readable_label(ancestor, available)
            result = f"{readable_ancestor} > {result}"
            if len(ancestor) > available:
                break
        return result

    @classmethod
    def _format_hierarchy(cls, hierarchy: tuple[str, ...], limit: int = 200) -> str | None:
        """Bound hierarchy metadata while always retaining the leaf heading."""
        if not hierarchy or limit <= 0:
            return None

        full_hierarchy = " > ".join(hierarchy)
        if len(full_hierarchy) <= limit:
            return full_hierarchy

        marker = cls._digest_marker(full_hierarchy)
        readable_limit = max(0, limit - len(marker))
        readable = cls._readable_hierarchy(hierarchy, readable_limit) or ""
        return cls._attach_digest(readable, marker)[-limit:]

    @classmethod
    def _format_group_heading(
        cls,
        hierarchies: tuple[tuple[str, ...], ...],
        limit: int = 200,
    ) -> str | None:
        if not hierarchies or limit <= 0:
            return None
        if len(hierarchies) == 1:
            return cls._format_hierarchy(hierarchies[0], limit)

        first_hierarchy = hierarchies[0] or ("Preamble",)
        last_hierarchy = hierarchies[-1] or ("Preamble",)
        separator = " … "
        full_group = (
            f"{' > '.join(first_hierarchy)}{separator}{' > '.join(last_hierarchy)}"
        )
        if len(full_group) <= limit:
            return full_group

        marker = cls._digest_marker(full_group)
        available = limit - len(separator) - len(marker)
        if available < 2:
            return cls._bounded_label(full_group, limit)

        first_budget = available // 2
        last_budget = available - first_budget
        first = cls._readable_hierarchy(first_hierarchy, first_budget) or "Preamble"
        last = cls._readable_hierarchy(last_hierarchy, last_budget) or "Preamble"
        return cls._attach_digest(f"{first}{separator}{last}", marker)

    @staticmethod
    def _choose_split_boundary(text: str, start: int, limit: int) -> int:
        """Choose a source index without normalizing either resulting slice."""
        hard_end = min(len(text), start + limit)
        if hard_end == len(text):
            return hard_end

        minimum_tail = min(80, max(20, limit // 4))
        target = hard_end
        if len(text) - hard_end < minimum_tail:
            target = max(start + limit // 2, start + (len(text) - start) // 2)

        lower_bound = max(start + 1, start + (target - start) // 2)
        newline = text.rfind("\n", lower_bound, target)
        carriage_return = text.rfind("\r", lower_bound, target)
        line_break = max(newline, carriage_return)
        if line_break >= lower_bound:
            if text[line_break : line_break + 2] == "\r\n":
                return line_break + 2
            return line_break + 1

        whitespace = max(
            text.rfind(" ", lower_bound, target),
            text.rfind("\t", lower_bound, target),
        )
        if whitespace >= lower_bound:
            return whitespace + 1
        return target

    def _split_oversized_block(
        self,
        block: str,
        *,
        first_limit: int | None = None,
        use_overlap: bool,
    ) -> list[str]:
        """Split one source block using monotonic indexes and exact source slices."""
        chunks: list[str] = []
        start = 0
        limit = first_limit or self.chunk_chars
        while len(block) - start > limit:
            boundary = self._choose_split_boundary(block, start, limit)
            if boundary <= start:
                boundary = min(len(block), start + limit)
            chunks.append(block[start:boundary])

            next_start = boundary
            if use_overlap and self.chunk_overlap:
                next_start = max(start + 1, boundary - self.chunk_overlap)
            start = next_start
            limit = self.chunk_chars
        if start < len(block):
            chunks.append(block[start:])
        return chunks

    def _markdown_blocks(self, text: str) -> list[str]:
        blocks: list[str] = []
        current: list[str] = []
        fence_character: str | None = None
        fence_length = 0
        for line in text.splitlines(keepends=True):
            line_without_ending = line.rstrip("\r\n")
            stripped = line_without_ending.strip()
            fence_match = re.match(r"^(`{3,}|~{3,})(.*)$", stripped)
            if fence_match and fence_character is None:
                marker = fence_match.group(1)
                fence_character = marker[0]
                fence_length = len(marker)
                current.append(line)
                continue
            if (
                fence_match
                and fence_match.group(1)[0] == fence_character
                and len(fence_match.group(1)) >= fence_length
                and not fence_match.group(2).strip()
            ):
                fence_character = None
                fence_length = 0
                current.append(line)
                continue
            if fence_character is None and not stripped:
                current.append(line)
                blocks.append("".join(current))
                current = []
                continue
            current.append(line)
        if current:
            blocks.append("".join(current))
        return blocks

    @classmethod
    def _heading_sections(cls, text: str) -> list[_MarkdownSection]:
        """Split Markdown on ATX headings outside fenced code blocks."""
        sections: list[_MarkdownSection] = []
        current_lines: list[str] = []
        current_hierarchy: tuple[str, ...] = ()
        current_level: int | None = None
        hierarchy: dict[int, str] = {}
        fence_character: str | None = None
        fence_length = 0

        for line in text.splitlines(keepends=True):
            line_without_ending = line.rstrip("\r\n")
            fence_match = re.match(
                r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$",
                line_without_ending,
            )
            if fence_match:
                marker = fence_match.group(1)
                if fence_character is None:
                    fence_character = marker[0]
                    fence_length = len(marker)
                elif (
                    marker[0] == fence_character
                    and len(marker) >= fence_length
                    and not fence_match.group(2).strip()
                ):
                    fence_character = None
                    fence_length = 0
                current_lines.append(line)
                continue

            parsed_heading = (
                cls._parse_atx_heading_line(line_without_ending)
                if fence_character is None
                else None
            )
            if parsed_heading is None:
                current_lines.append(line)
                continue

            level, label = parsed_heading
            content = "".join(current_lines)
            heading_only_parent = bool(
                content
                and current_level is not None
                and level > current_level
                and all(
                    not candidate.strip()
                    or cls._parse_atx_heading_line(candidate) is not None
                    for candidate in current_lines
                )
            )
            if content and not heading_only_parent:
                sections.append(_MarkdownSection(current_hierarchy, content))

            hierarchy = {key: value for key, value in hierarchy.items() if key < level}
            if label:
                hierarchy[level] = label
            current_hierarchy = tuple(hierarchy[key] for key in sorted(hierarchy))
            current_lines = [*current_lines, line] if heading_only_parent else [line]
            current_level = level

        content = "".join(current_lines)
        if content:
            sections.append(_MarkdownSection(current_hierarchy, content))
        return sections

    def _coalesce_sections(
        self,
        sections: Sequence[_MarkdownSection],
    ) -> list[_MarkdownSectionGroup]:
        target = max(80, self.chunk_chars // 3)
        groups: list[_MarkdownSectionGroup] = []
        current: list[_MarkdownSection] = []
        current_length = 0

        def flush() -> None:
            nonlocal current, current_length
            if current:
                groups.append(
                    _MarkdownSectionGroup(
                        hierarchies=tuple(section.hierarchy for section in current),
                        content="".join(section.content for section in current),
                    )
                )
                current = []
                current_length = 0

        for section in sections:
            section_length = len(section.content)
            if section_length > self.chunk_chars:
                flush()
                groups.append(_MarkdownSectionGroup((section.hierarchy,), section.content))
                continue

            combined_length = current_length + section_length
            can_combine = (
                current
                and combined_length <= self.chunk_chars
                and (current_length < target or section_length < target)
            )
            if current and not can_combine:
                flush()
            current.append(section)
            current_length += section_length
        flush()
        return groups

    @staticmethod
    def _block_uses_overlap(block: str) -> bool:
        """Use overlap only for an oversized prose paragraph, not lists or code."""
        meaningful = block.rstrip("\r\n")
        if "\n" in meaningful or "\r" in meaningful:
            return False
        stripped = meaningful.lstrip()
        return not re.match(r"(?:[-+*]|\d+[.)])\s+", stripped)

    def _chunk_section(self, content: str) -> list[str]:
        blocks = self._markdown_blocks(content)
        if not blocks:
            return []

        chunks: list[str] = []
        current = ""
        for block in blocks:
            if len(block) <= self.chunk_chars:
                if len(current) + len(block) <= self.chunk_chars:
                    current += block
                else:
                    if current:
                        chunks.append(current)
                    current = block
                continue

            available = self.chunk_chars - len(current)
            first_limit = None
            if current and available > self.chunk_overlap + max(20, self.chunk_chars // 5):
                first_limit = available
            elif current:
                chunks.append(current)
                current = ""

            pieces = self._split_oversized_block(
                block,
                first_limit=first_limit,
                use_overlap=self._block_uses_overlap(block),
            )
            if current and pieces:
                chunks.append(current + pieces.pop(0))
                current = ""
            if pieces:
                chunks.extend(pieces[:-1])
                current = pieces[-1]

        if current:
            chunks.append(current)
        return chunks

    def _chunk_markdown(self, title: str, text: str) -> list[tuple[str | None, str]]:
        body = self._strip_frontmatter(text)
        if not body.strip():
            title_chunks = self._chunk_section(title)
            return [(None, chunk) for chunk in title_chunks] or [(None, title)]

        chunks: list[tuple[str | None, str]] = []
        sections = self._coalesce_sections(self._heading_sections(body))
        for section in sections:
            heading = self._format_group_heading(section.hierarchies)
            chunks.extend((heading, chunk) for chunk in self._chunk_section(section.content))
        return chunks or [(None, title)]

    @staticmethod
    def _eligible_files(vault_root: Path, max_note_bytes: int) -> list[Path]:
        return eligible_markdown_files(vault_root, max_note_bytes)

    @staticmethod
    def _target_files(
        vault_root: Path,
        max_note_bytes: int,
        relative_paths: Sequence[str],
    ) -> tuple[list[Path], list[str]]:
        resolved_root = vault_root.resolve()
        files: list[Path] = []
        missing_paths: list[str] = []
        for raw_path in sorted(set(relative_paths)):
            normalized = raw_path.strip().replace("\\", "/")
            posix_path = PurePosixPath(normalized)
            if (
                not normalized
                or posix_path.is_absolute()
                or PureWindowsPath(normalized).is_absolute()
                or ".." in posix_path.parts
                or posix_path.suffix.lower() != ".md"
            ):
                raise TargetedSynchronizationError(
                    f"Invalid targeted note path: {raw_path!r}"
                )
            normalized = posix_path.as_posix()
            relative_path = Path(normalized)
            if any(part in SEMANTIC_EXCLUDED_DIRECTORIES for part in relative_path.parts):
                raise TargetedSynchronizationError(
                    f"Targeted note path is excluded: {normalized}"
                )
            candidate = resolved_root / relative_path
            try:
                path = candidate.resolve(strict=True)
                path.relative_to(resolved_root)
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    missing_paths.append(normalized)
                    continue
                if not path.is_file():
                    raise TargetedSynchronizationError(
                        f"Targeted note is unavailable: {normalized}"
                    )
                if stat.st_size > max_note_bytes:
                    raise TargetedSynchronizationError(
                        f"Targeted note is too large: {normalized}"
                    )
                files.append(path)
            except FileNotFoundError:
                try:
                    unresolved_path = candidate.resolve(strict=False)
                    unresolved_path.relative_to(resolved_root)
                    if candidate.is_symlink():
                        raise TargetedSynchronizationError(
                            f"Targeted note is inaccessible: {normalized}"
                        )
                except TargetedSynchronizationError:
                    raise
                except (OSError, ValueError) as exc:
                    raise TargetedSynchronizationError(
                        f"Targeted note is inaccessible: {normalized}"
                    ) from exc
                missing_paths.append(normalized)
            except TargetedSynchronizationError:
                raise
            except (OSError, ValueError) as exc:
                raise TargetedSynchronizationError(
                    f"Targeted note is inaccessible: {normalized}"
                ) from exc
        return files, missing_paths

    def _index_files(
        self,
        *,
        files: Sequence[Path],
        vault_root: Path,
        known: dict[str, StoredNote],
        cancel_event: threading.Event | None,
        strict_reads: bool = False,
    ) -> tuple[int, int]:
        indexed = unchanged = 0
        for file_batch in batched(files, self.index_batch_size):
            self._raise_if_cancelled(cancel_event)
            with self.repository.transaction() as session:
                for path in file_batch:
                    relative_path = str(path.relative_to(vault_root)).replace("\\", "/")
                    try:
                        stat = path.stat()
                    except OSError as exc:
                        if strict_reads:
                            raise TargetedSynchronizationError(
                                f"Targeted note could not be inspected: {relative_path}"
                            ) from exc
                        raise
                    previous = known.get(relative_path)
                    if (
                        previous
                        and previous.mtime_ns == stat.st_mtime_ns
                        and previous.size == stat.st_size
                    ):
                        unchanged += 1
                        continue

                    try:
                        digest = self._sha256(path)
                    except OSError as exc:
                        if strict_reads:
                            raise TargetedSynchronizationError(
                                f"Targeted note could not be read: {relative_path}"
                            ) from exc
                        raise
                    if previous and previous.sha256 == digest:
                        session.update_note_metadata(
                            path=relative_path,
                            mtime_ns=stat.st_mtime_ns,
                            size=stat.st_size,
                        )
                        unchanged += 1
                        continue

                    try:
                        with path.open("r", encoding="utf-8", newline="") as note_file:
                            note_text = note_file.read()
                    except (UnicodeDecodeError, OSError) as exc:
                        if strict_reads:
                            raise TargetedSynchronizationError(
                                f"Targeted note could not be read: {relative_path}"
                            ) from exc
                        continue

                    chunks = self._chunk_markdown(path.stem, note_text)
                    embedding_texts = [
                        self._build_embedding_text(path.stem, heading, content)
                        for heading, content in chunks
                    ]
                    vectors = self._embed(embedding_texts)
                    if len(vectors) != len(chunks):
                        raise SemanticIndexOperationError(
                            "Embedding model returned an unexpected number of vectors"
                        )

                    stored_chunks: list[StoredChunk] = []
                    for index, ((heading, content), vector) in enumerate(
                        zip(chunks, vectors, strict=True)
                    ):
                        normalized = self._normalize(vector)
                        stored_chunks.append(
                            StoredChunk(
                                path=relative_path,
                                chunk_index=index,
                                heading=heading,
                                content=content,
                                embedding=normalized.astype(np.float32).tobytes(),
                                dimensions=int(normalized.size),
                            )
                        )
                    session.replace_note(
                        StoredNote(
                            path=relative_path,
                            mtime_ns=stat.st_mtime_ns,
                            size=stat.st_size,
                            sha256=digest,
                            indexed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        ),
                        stored_chunks,
                    )
                    indexed += 1
        return indexed, unchanged

    def _sync_all_locked(
        self,
        cancel_event: threading.Event | None,
    ) -> dict[str, int]:
        with self._configuration_lock:
            vault_root = self.vault_root
            max_note_bytes = self.max_note_bytes
        files = self._eligible_files(vault_root, max_note_bytes)
        seen = {str(path.relative_to(vault_root)).replace("\\", "/") for path in files}

        with self.repository.transaction() as session:
            known = session.load_notes()

        removed = 0
        stale_paths = set(known) - seen
        for stale_batch in batched(stale_paths, self.index_batch_size):
            self._raise_if_cancelled(cancel_event)
            with self.repository.transaction() as session:
                for stale_path in stale_batch:
                    session.delete_note(stale_path)
            removed += len(stale_batch)

        indexed, unchanged = self._index_files(
            files=files,
            vault_root=vault_root,
            known=known,
            cancel_event=cancel_event,
        )
        return {"indexed": indexed, "unchanged": unchanged, "removed": removed}

    def _sync_targets_locked(
        self,
        relative_paths: Sequence[str],
        cancel_event: threading.Event | None,
    ) -> dict[str, int]:
        with self._configuration_lock:
            vault_root = self.vault_root
            max_note_bytes = self.max_note_bytes
        files, missing_paths = self._target_files(
            vault_root,
            max_note_bytes,
            relative_paths,
        )
        with self.repository.transaction() as session:
            known = session.load_notes()
        removed = 0
        for stale_batch in batched(
            (path for path in missing_paths if path in known),
            self.index_batch_size,
        ):
            self._raise_if_cancelled(cancel_event)
            with self.repository.transaction() as session:
                for stale_path in stale_batch:
                    session.delete_note(stale_path)
            removed += len(stale_batch)
        indexed, unchanged = self._index_files(
            files=files,
            vault_root=vault_root,
            known=known,
            cancel_event=cancel_event,
            strict_reads=True,
        )
        return {"indexed": indexed, "unchanged": unchanged, "removed": removed}

    def _run_synchronization_locked(
        self,
        operation: Callable[[], dict[str, int]],
        *,
        operation_name: str,
        record_full_sync: bool = False,
    ) -> dict[str, int]:
        initial_state = self._initialize_state(create_storage=True)
        with self._availability_lock:
            search_available = self._search_available
        self._set_search_available(search_available or initial_state is IndexState.READY)
        self._persist_state(IndexState.INDEXING)
        started_at = time.perf_counter()
        event_prefix = "semantic_sync" if operation_name == "full" else "targeted_reindex"
        log_event(
            logger,
            logging.INFO,
            f"{event_prefix}_started",
            "Semantic synchronization started",
            operation=operation_name,
            index_state=IndexState.INDEXING,
        )
        try:
            result = operation()
            if record_full_sync:
                self.repository.finalize_full_sync_success(
                    last_successful_sync=datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                    ready_state=IndexState.READY.value,
                )
            else:
                self._persist_state(IndexState.READY)
        except SynchronizationCancelledError as exc:
            try:
                self._persist_state(IndexState.ERROR)
            except Exception:
                pass
            log_event(
                logger,
                logging.INFO,
                f"{event_prefix}_cancelled",
                "Semantic synchronization was cancelled",
                operation=operation_name,
                duration_ms=round((time.perf_counter() - started_at) * 1000, 3),
                index_state=IndexState.ERROR,
                error_type=type(exc).__name__,
            )
            raise
        except Exception as exc:
            try:
                self._persist_state(IndexState.ERROR)
            except Exception:
                pass
            log_event(
                logger,
                logging.ERROR,
                f"{event_prefix}_failed",
                "Semantic synchronization failed",
                exc_info=(type(exc), exc, exc.__traceback__),
                operation=operation_name,
                duration_ms=round((time.perf_counter() - started_at) * 1000, 3),
                index_state=IndexState.ERROR,
                error_type=type(exc).__name__,
            )
            raise

        self._set_search_available(True)
        log_event(
            logger,
            logging.INFO,
            f"{event_prefix}_completed",
            "Semantic synchronization completed",
            operation=operation_name,
            indexed_notes=result["indexed"],
            unchanged_notes=result["unchanged"],
            removed_notes=result["removed"],
            duration_ms=round((time.perf_counter() - started_at) * 1000, 3),
            index_state=IndexState.READY,
        )
        return result

    def sync(self, cancel_event: threading.Event | None = None) -> dict[str, int]:
        """Index only new/changed notes and remove deleted notes."""
        with self._sync_lock:
            self._resolve_index_signature(persist_failure=True)
            self._validate_backend_before_destructive_prepare(persist_failure=True)
            return self._run_synchronization_locked(
                lambda: self._sync_all_locked(cancel_event),
                operation_name="full",
                record_full_sync=True,
            )

    def rebuild(self, cancel_event: threading.Event | None = None) -> dict[str, int]:
        """Discard derived semantic state and rebuild it from the Markdown vault."""
        with self._sync_lock:
            self._resolve_index_signature(persist_failure=True)
            self._validate_backend_before_destructive_prepare(persist_failure=True)
            self._set_search_available(False)
            self.repository.reset_index(self.index_signature, IndexState.UNINITIALIZED.value)
            with self._state_init_lock:
                self._state_initialized = True
            return self._run_synchronization_locked(
                lambda: self._sync_all_locked(cancel_event),
                operation_name="full",
                record_full_sync=True,
            )

    def sync_paths(
        self,
        relative_paths: Sequence[str],
        cancel_event: threading.Event | None = None,
    ) -> dict[str, int]:
        """Refresh only the supplied vault-relative Markdown paths."""
        paths = tuple(sorted(set(relative_paths)))
        if not paths:
            return {"indexed": 0, "unchanged": 0, "removed": 0}

        with self._sync_lock:
            self._resolve_index_signature(persist_failure=True)
            self._validate_backend_before_destructive_prepare(persist_failure=True)
            initial_state = self._initialize_state(create_storage=True)
            with self._availability_lock:
                search_available = self._search_available
            if not search_available and initial_state is not IndexState.READY:
                return self._run_synchronization_locked(
                    lambda: self._sync_all_locked(cancel_event),
                    operation_name="full",
                    record_full_sync=True,
                )
            return self._run_synchronization_locked(
                lambda: self._sync_targets_locked(paths, cancel_event),
                operation_name="targeted",
            )

    def search(
        self,
        text: str,
        *,
        folder: str = "",
        limit: int = 5,
        min_score: float = 0.28,
    ) -> list[SemanticResult]:
        if not self.is_search_available():
            if self.state is IndexState.ERROR:
                raise SemanticSearchUnavailableError("Semantic index is unavailable")
            return []
        embedded_query = self._embed([text])
        if not embedded_query:
            return []
        query = self._normalize(embedded_query[0])
        folder = folder.strip().replace("\\", "/").strip("/")
        prefix = f"{folder}/" if folder else ""
        best: dict[str, _RankedChunk] = {}

        with self._availability_lock:
            persisted_read_only_search = self._persisted_read_only_search
        try:
            chunks = (
                self.repository.load_chunks_read_only()
                if persisted_read_only_search
                else self.repository.load_chunks()
            )
        except ImmutableIndexInspectionUnavailableError as exc:
            self._set_search_available(False)
            raise SemanticSearchUnavailableError(
                "Persisted semantic index is unavailable for immutable reads"
            ) from exc
        for chunk in chunks:
            if prefix and not chunk.path.startswith(prefix):
                continue
            vector = np.frombuffer(
                chunk.embedding,
                dtype=np.float32,
                count=chunk.dimensions,
            )
            if vector.size != query.size:
                continue
            semantic_score = float(np.dot(query, vector))
            if semantic_score < min_score:
                continue
            lexical_score = self._lexical_score(
                text,
                path=chunk.path,
                heading=chunk.heading,
                content=chunk.content,
            )
            candidate = _RankedChunk(
                score=self._hybrid_score(semantic_score, lexical_score),
                semantic_score=semantic_score,
                lexical_score=lexical_score,
                chunk=chunk,
            )
            current = best.get(chunk.path)
            if current is None or candidate.note_selection_key > current.note_selection_key:
                best[chunk.path] = candidate

        results: list[SemanticResult] = []
        ordered = sorted(
            best.items(),
            key=lambda item: (
                -item[1].score,
                -item[1].semantic_score,
                -item[1].lexical_score,
                item[0].casefold(),
                item[0],
            ),
        )
        relative_floor = ordered[0][1].score * RELATIVE_RESULT_FLOOR if ordered else 0.0
        for path, ranked in ordered:
            if ranked.score < relative_floor:
                continue
            results.append(
                SemanticResult(
                    path=path,
                    title=Path(path).stem,
                    score=round(ranked.score, 4),
                    semantic_score=round(ranked.semantic_score, 4),
                    lexical_score=round(ranked.lexical_score, 4),
                    snippet=" ".join(ranked.chunk.content.split())[:500],
                    heading=ranked.chunk.heading,
                )
            )
            if len(results) >= limit:
                break
        return results

    def is_initialized(self) -> bool:
        """Compatibility alias for callers that previously checked index readiness."""
        return self.is_ready()


def semantic_search_service_from_settings(
    settings: Settings,
    *,
    embedder: Embedder | None = None,
) -> SemanticSearchService:
    data_path = settings.semantic_data_path
    return SemanticSearchService(
        vault_root=settings.vault_path,
        repository=SemanticRepository(data_path / "semantic-index.sqlite3"),
        cache_dir=data_path / "models",
        model_name=settings.semantic_model,
        max_note_bytes=settings.max_note_bytes,
        chunk_chars=settings.semantic_chunk_chars,
        chunk_overlap=settings.semantic_chunk_overlap,
        embed_batch_size=settings.semantic_embed_batch_size,
        onnx_cpu_mem_arena=settings.semantic_onnx_cpu_mem_arena,
        index_batch_size=settings.semantic_index_batch_size,
        embedder=embedder,
    )
