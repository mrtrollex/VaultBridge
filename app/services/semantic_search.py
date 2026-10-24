from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
import sqlite3
import stat
import threading
import time
import unicodedata
from collections.abc import Iterable, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import wraps
from itertools import batched
from math import isfinite
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

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
from app.repositories.semantic_reads import EligibleSemanticSnapshot, SemanticReadUnavailableError
from app.services.knowledge_spaces import SpaceId
from app.services.scoped_budget import CancellationToken, ScopedBudget
from app.services.semantic_sync import SyncContinuation, SyncStep, SyncStepState, SyncWork
from app.services.vault import (
    SEMANTIC_EXCLUDED_DIRECTORIES,
    VaultService,
    VaultServiceError,
    eligible_markdown_files,
)

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

if TYPE_CHECKING:
    from app.services.semantic_resources import SharedEmbeddingResource


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


def _embedding_pipeline(method):
    """Retain a shared generation, independently of the backend-call lease."""
    signature = inspect.signature(method)

    @wraps(method)
    def run(self, *args, **kwargs):
        if self._shared_resource is None:
            return method(self, *args, **kwargs)
        arguments = signature.bind(self, *args, **kwargs).arguments
        cancel = arguments.get("cancel")
        event = arguments.get("cancel_event")
        check = cancel.check if cancel is not None else lambda: self._raise_if_cancelled(event)
        prepared = arguments.get("prepared")
        expected = prepared.resource_generation if prepared is not None else None
        with self._embedding_operation(check, expected=expected):
            return method(self, *args, **kwargs)

    return run


@dataclass(frozen=True, slots=True)
class EmbeddingCompatibilityEvidence:
    embedding_fingerprint: str
    dimensions: int
    backend_contract: str

    def __post_init__(self):
        if (
            not isinstance(self.embedding_fingerprint, str)
            or not re.fullmatch(r"embedding-v1:[0-9a-f]{64}", self.embedding_fingerprint)
            or type(self.dimensions) is not int or self.dimensions <= 0
            or not isinstance(self.backend_contract, str) or not self.backend_contract
        ):
            raise ValueError("invalid embedding compatibility evidence")


@runtime_checkable
class EmbeddingCompatibilityProvider(Protocol):
    def resolve_embedding_compatibility(self) -> EmbeddingCompatibilityEvidence: ...


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
        self._compatibility_evidence: EmbeddingCompatibilityEvidence | None = None
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
            dimensions = getattr(description, "dim", None)
            if type(dimensions) is int and dimensions > 0:
                self._compatibility_evidence = EmbeddingCompatibilityEvidence(
                    self._embedding_fingerprint, dimensions, backend_contract_id,
                )
            return self._embedding_fingerprint

    def resolve_embedding_compatibility(self) -> EmbeddingCompatibilityEvidence:
        """Resolve artifacts and declared vector shape without probe inference."""
        self.resolve_embedding_fingerprint()
        if self._compatibility_evidence is None:
            raise SemanticIndexOperationError("Embedding compatibility evidence is unavailable")
        return self._compatibility_evidence

    def invalidate_embedding_compatibility_cache(self) -> None:
        """Discard artifact identity and partially initialized lazy backend state."""
        with self._lock:
            self._embedding_fingerprint = None
            self._compatibility_evidence = None
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


SemanticQueryBasis = Literal[
    "compatible_ready",
    "compatible_previous_refresh",
    "compatible_previous_error",
]


@dataclass(frozen=True, slots=True)
class ScoringDescriptor:
    model: str
    embedding_fingerprint: str
    index_signature: str
    dimensions: int
    backend_contract: str
    min_score: float
    rank_contract: Literal["vaultbridge-hybrid-v1"] = "vaultbridge-hybrid-v1"
    semantic_weight: float = 1.0
    lexical_weight: float = 0.70
    relative_floor: float = 0.78

    def __post_init__(self):
        EmbeddingCompatibilityEvidence(
            self.embedding_fingerprint, self.dimensions, self.backend_contract,
        )
        if (
            not isinstance(self.model, str) or not self.model
            or not isinstance(self.index_signature, str) or not self.index_signature
            or not isfinite(self.min_score) or not 0 <= self.min_score <= 1
            or self.rank_contract != "vaultbridge-hybrid-v1"
            or self.semantic_weight != SEMANTIC_RANK_WEIGHT
            or self.lexical_weight != LEXICAL_RANK_WEIGHT
            or self.relative_floor != RELATIVE_RESULT_FLOOR
        ):
            raise ValueError("invalid scoring descriptor")


@dataclass(frozen=True, slots=True)
class PreciseSemanticCandidate:
    path: str
    title: str
    score: float
    semantic_score: float
    lexical_score: float
    snippet: str
    heading: str | None


@dataclass(frozen=True, slots=True)
class PreciseSemanticResult:
    candidates: tuple[PreciseSemanticCandidate, ...]
    descriptor: ScoringDescriptor
    index_basis: SemanticQueryBasis


@dataclass(frozen=True, slots=True)
class _PreparedSemanticSearch:
    descriptor: ScoringDescriptor
    index_basis: SemanticQueryBasis
    snapshot: EligibleSemanticSnapshot = field(repr=False)
    owner: object = field(repr=False)
    budget: ScopedBudget = field(repr=False)
    cancel: CancellationToken = field(repr=False)
    space_id: SpaceId
    resource_generation: int | None = None


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
        compatibility_evidence: EmbeddingCompatibilityEvidence | None = None,
        indexing_enabled: bool = True,
        shared_resource: SharedEmbeddingResource | None = None,
    ) -> None:
        # The existing embedder seam also accepts the resource, but must never
        # bypass its generation contract when used that way by trusted callers.
        from app.services.semantic_resources import SharedEmbeddingResource

        if isinstance(embedder, SharedEmbeddingResource) and shared_resource is None:
            shared_resource, embedder = embedder, None
        self.vault_root = vault_root
        self.repository = repository
        self._validate_source_root(vault_root)
        self.model_name = model_name
        self._indexing_enabled = indexing_enabled
        if shared_resource is not None and (
            embedder is not None or embedding_fingerprint is not None
            or compatibility_evidence is not None or model_name != shared_resource.model_name
            or (cache_dir is not None and cache_dir != shared_resource.cache_dir)
        ):
            raise ValueError("Shared embedding resource requires its configured model and evidence")
        self._shared_resource = shared_resource
        self._resource_generation: int | None = None
        self._federation_evidence = compatibility_evidence
        self._federation_evidence_lock = threading.Lock()
        self.cache_dir = (
            shared_resource.cache_dir if shared_resource is not None
            else cache_dir or repository.db_path.parent / "models"
        )
        self.max_note_bytes = max_note_bytes
        self.chunk_chars = max(250, chunk_chars)
        self.chunk_overlap = max(0, min(chunk_overlap, self.chunk_chars // 2))
        if index_batch_size <= 0:
            raise ValueError("index_batch_size must be positive")
        self.index_batch_size = index_batch_size
        if shared_resource is not None:
            self.embedder = shared_resource
        elif embedder is None:
            self.embedder = FastEmbedder(
                model_name,
                self.cache_dir,
                batch_size=embed_batch_size,
                enable_cpu_mem_arena=onnx_cpu_mem_arena,
            )
        else:
            self.embedder = embedder
        self._sync_lock = threading.Lock()
        self._legacy_sync_lock = threading.Lock()
        self._sync_admin_lock = threading.Lock()
        self._sync_operations: dict[threading.Event, threading.Event] = {}
        self._active_sync: SyncWork | None = None
        self._sync_closed = False
        self._full_sync_required = False
        self._sync_configuration_revision = 0
        self._configuration_lock = threading.Lock()
        self._availability_lock = threading.Lock()
        self._embed_lock = threading.Lock()
        self._fingerprint_lock = threading.Lock()
        self._search_available = False
        self._persisted_read_only_search = False
        self._persisted_read_only_basis: SemanticQueryBasis | None = None
        self._state_init_lock = threading.Lock()
        self._state_initialized = False
        self._fingerprint_resolution_failed = False
        self._compatibility_invalidation_pending = False
        self._explicit_index_signature = (
            self._build_index_signature(embedding_fingerprint)
            if embedding_fingerprint is not None
            else None
        )
        self._resolved_index_signature = self._explicit_index_signature

    @property
    def db_path(self) -> Path:
        return self.repository.db_path

    @contextmanager
    def _embedding_operation(self, check=lambda: None, *, expected=None):
        resource = self._shared_resource
        if resource is None:
            yield
            return
        if not self._indexing_enabled:
            raise SemanticSearchUnavailableError("Semantic index is unavailable")
        self._validate_source_root(self.vault_root)
        with resource.operation(check, expected=expected) as generation:
            with self._fingerprint_guard():
                if self._resource_generation != generation:
                    with self._owner_guard(self._federation_evidence_lock):
                        with self._owner_guard(self._availability_lock):
                            self._federation_evidence = None
                            self._resolved_index_signature = None
                            self._fingerprint_resolution_failed = False
                            self._state_initialized = False
                            self._search_available = False
                            self._persisted_read_only_search = False
                            self._persisted_read_only_basis = None
                            self._resource_generation = generation
                            self._compatibility_invalidation_pending = False
                self._check_compatibility_invalidation()
            yield
            self._check_compatibility_invalidation()

    def _check_compatibility_invalidation(self):
        if self._shared_resource is not None and self._compatibility_invalidation_pending:
            raise SemanticSearchUnavailableError("Embedding compatibility invalidation is pending")

    @contextmanager
    def _owner_guard(self, lock, check=None):
        resource = self._shared_resource
        if resource is None and check is None:
            with lock:
                yield
            return
        check = check or resource.check_cancelled
        check()
        while not lock.acquire(timeout=0.05):
            check()
        try:
            check()
            yield
        finally:
            lock.release()

    @contextmanager
    def _fingerprint_guard(self):
        resource = self._shared_resource
        if resource is None:
            # Retain legacy/custom embedder lock behavior.
            with self._fingerprint_lock:
                yield
            return
        resource.check_cancelled()
        while not self._fingerprint_lock.acquire(timeout=0.05):
            resource.check_cancelled()
        try:
            resource.check_cancelled()
            yield
        finally:
            self._fingerprint_lock.release()

    def _validate_source_root(self, root):
        binding = getattr(self.repository, "_expected_binding", None)
        if binding is not None:
            binding.check_source_root(root)

    def _embedding_evidence_current(self):
        resource = self._shared_resource
        return resource is None or resource.is_current(self._resource_generation)

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

    @_embedding_pipeline
    def _resolve_index_signature(self, *, persist_failure: bool) -> str:
        self._validate_source_root(self.vault_root)
        if not self._indexing_enabled:
            raise SemanticSearchUnavailableError("Semantic index is unavailable")
        self.repository.validate_expected_binding()
        if self._resolved_index_signature is not None:
            return self._resolved_index_signature
        with self._fingerprint_guard():
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
        resource = self._shared_resource
        if resource is not None:
            # Fence this generation before any cancellable owner-lock wait. The
            # last pin completes backend invalidation even if local cleanup exits.
            self._compatibility_invalidation_pending = True
            resource.invalidate_embedding_compatibility_cache()
        with self._fingerprint_guard():
            with self._owner_guard(self._federation_evidence_lock):
                self._federation_evidence = None
            if resource is None and isinstance(self.embedder, EmbeddingCompatibilityCache):
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

    @_embedding_pipeline
    def _initialize_state(self, *, create_storage: bool, _sync_check=None) -> IndexState:
        self._validate_source_root(self.vault_root)
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

        with self._owner_guard(self._state_init_lock):
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

            self._check_compatibility_invalidation()
            signature = self.index_signature
            with self._sync_preparation(_sync_check):
                storage = self.repository.prepare_index(signature)
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

    @_embedding_pipeline
    def is_search_available(self) -> bool:
        with self._owner_guard(self._availability_lock):
            available = self._search_available
        if available:
            return True
        if self.state is IndexState.READY:
            with self._owner_guard(self._availability_lock):
                self._search_available = True
            return True
        return False

    @_embedding_pipeline
    def query_basis(self) -> SemanticQueryBasis | None:
        """Return the bounded compatible index basis available to a domain query."""
        with self._owner_guard(self._availability_lock):
            if self._persisted_read_only_search:
                return self._persisted_read_only_basis
        if not self.is_search_available():
            return None
        state = self.state
        if state is IndexState.READY:
            return "compatible_ready"
        if state is IndexState.INDEXING:
            return "compatible_previous_refresh"
        if state is IndexState.ERROR:
            return "compatible_previous_error"
        return None

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
            self._embedding_evidence_current()
            and storage_initialized
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
            self._embedding_evidence_current()
            and storage_initialized
            and not storage_error
            and self._resolved_index_signature is not None
            and index_signature == self._resolved_index_signature
        ):
            return False
        with self._owner_guard(self._availability_lock):
            search_available = self._search_available
        return search_available or stored_index_available

    def _health_status_from_storage(self, storage: SemanticIndexStatus) -> SemanticHealthStatus:
        compatible_storage = (
            self._embedding_evidence_current()
            and storage.storage_initialized
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

    def inspect_hygiene_index(self) -> str:
        """Classify existing storage without lifecycle, cache, or sidecar mutation."""
        try:
            storage = self.repository.read_immutable_status()
        except ImmutableIndexInspectionUnavailableError:
            return "inspection_unavailable"
        if storage.storage_error:
            return storage.error_kind or "storage_unavailable"
        if not storage.storage_exists:
            return "missing"
        if not storage.storage_initialized or storage.index_signature is None:
            return "invalid_metadata"
        if not storage.index_signature.startswith(INDEX_SIGNATURE_PREFIX):
            return "invalid_metadata"
        try:
            parsed_signature = json.loads(storage.index_signature.removeprefix(INDEX_SIGNATURE_PREFIX))
        except (TypeError, ValueError):
            return "invalid_metadata"
        if not isinstance(parsed_signature, dict):
            return "invalid_metadata"
        if not self._stored_signature_matches_configuration(storage.index_signature):
            return "incompatible"
        # Resolving an uncached model fingerprint may mutate a model cache.
        if not self._embedding_evidence_current() or self._resolved_index_signature is None:
            return "inspection_unavailable"
        if storage.index_signature != self._resolved_index_signature:
            return "incompatible"
        if storage.index_state is None:
            return "compatible_ready" if storage.semantic_chunks else "invalid_metadata"
        if storage.index_state == IndexState.READY.value:
            return "compatible_ready"
        if storage.index_state not in {state.value for state in IndexState}:
            return "invalid_metadata"
        if storage.index_state in {IndexState.INDEXING.value, IndexState.ERROR.value}:
            # Persisted state may have been written by another process. This
            # instance has no explicit current lifecycle phase to corroborate it.
            return "inspection_unavailable" if storage.semantic_chunks else "invalid_metadata"
        return "invalid_metadata"

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

    @_embedding_pipeline
    def use_persisted_index_for_read_only_search(self) -> bool:
        """Enable this instance to search a compatible persisted index without lifecycle writes."""
        try:
            self._resolve_index_signature(persist_failure=False)
        except SemanticIndexOperationError:
            self._set_search_available(False, persisted_read_only=True)
            return False
        try:
            storage = self.repository.read_immutable_status()
        except ImmutableIndexInspectionUnavailableError:
            self._set_search_available(False, persisted_read_only=True)
            return False
        available = self._stored_index_available_from_storage(
            storage_initialized=storage.storage_initialized,
            storage_error=storage.storage_error,
            index_signature=storage.index_signature,
            index_state=storage.index_state,
            has_chunks=bool(storage.semantic_chunks),
        )
        self._set_search_available(
            available,
            persisted_read_only=True,
            persisted_read_only_basis="compatible_ready" if available else None,
        )
        return available

    def _set_search_available(
        self,
        available: bool,
        *,
        persisted_read_only: bool = False,
        persisted_read_only_basis: SemanticQueryBasis | None = None,
    ) -> None:
        with self._owner_guard(self._availability_lock):
            self._search_available = available
            self._persisted_read_only_search = persisted_read_only
            self._persisted_read_only_basis = (
                persisted_read_only_basis if persisted_read_only and available else None
            )

    @contextmanager
    def _embed_guard(self):
        resource = self._shared_resource
        if resource is None:
            with self._embed_lock:
                yield
            return
        resource.check_cancelled()
        while not self._embed_lock.acquire(timeout=0.05):
            resource.check_cancelled()
        try:
            resource.check_cancelled()
            yield
        finally:
            self._embed_lock.release()

    def _embed(self, texts: Sequence[str], *, _session=None) -> list[np.ndarray]:
        """Serialize only calls into one embedder instance; the surrounding pipelines stay concurrent."""
        if not self._indexing_enabled:
            raise SemanticSearchUnavailableError("Semantic index is unavailable")
        with self._embed_guard():
            self._validate_source_root(self.vault_root)
            if _session is None:
                self.repository.validate_expected_binding()
            else:
                self.repository._validate_transaction_binding(_session)
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
        with self._owner_guard(self._configuration_lock):
            root = self.vault_root if vault_root is None else vault_root
            size = self.max_note_bytes if max_note_bytes is None else max_note_bytes
            self._validate_source_root(root)
            if root != self.vault_root or size != self.max_note_bytes:
                self._sync_configuration_revision += 1
                self.vault_root, self.max_note_bytes = root, size

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
    def _verify_missing_target(vault_root: Path, relative_path: Path) -> None:
        """Establish ordinary absence without trusting a dangling ancestor.

        Facts are bounded by this single path's depth. Recheck ancestors and the
        first missing component before authorizing deletion; no sibling walk.
        Existing contained directory aliases retain their normal path behavior.
        """
        def identity(info):
            return info.st_dev, info.st_ino, info.st_mode, info.st_ctime_ns

        facts = []
        current = vault_root
        components = (None, *relative_path.parts)
        for index, part in enumerate(components):
            if part is not None:
                current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                if index == 0:
                    raise TargetedSynchronizationError("Synchronization root is unavailable") from None
                for observed, expected in facts:
                    if identity(observed.lstat()) != expected:
                        raise TargetedSynchronizationError("Targeted ancestor changed")
                try:
                    current.lstat()
                except FileNotFoundError:
                    return
                raise TargetedSynchronizationError("Targeted note reappeared during validation")
            facts.append((current, identity(info)))
            if stat.S_ISLNK(info.st_mode) or current.is_junction():
                target = current.resolve(strict=True)
                target.relative_to(vault_root)
                target_info = target.stat()
                facts.append((target, identity(target_info)))
                info = target_info
            if index < len(components) - 1 and not stat.S_ISDIR(info.st_mode):
                raise TargetedSynchronizationError("Targeted ancestor is not a directory")
        # The target now exists, so the earlier FileNotFoundError is stale too.
        raise TargetedSynchronizationError("Targeted absence could not be confirmed")

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
                stat = path.stat()
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
                    SemanticSearchService._verify_missing_target(resolved_root, relative_path)
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
        known: dict[str, StoredNote] | None,
        cancel_event: threading.Event | None,
        strict_reads: bool = False,
        _session=None,
    ) -> tuple[int, int]:
        self._validate_source_root(vault_root)
        vault = VaultService(vault_root=vault_root, max_note_bytes=self.max_note_bytes)
        indexed = unchanged = 0
        for file_batch in batched(files, self.index_batch_size):
            self._raise_if_cancelled(cancel_event)
            with (nullcontext(_session) if _session is not None else self.repository.transaction()) as session:
                for path in file_batch:
                    relative_path = str(path.relative_to(vault_root)).replace("\\", "/")
                    self._raise_if_cancelled(cancel_event)
                    # A discovered spelling is only scan evidence. Recheck the
                    # canonical target, containment, exclusion and size on use.
                    files_now, missing = self._target_files(vault_root, self.max_note_bytes, (relative_path,))
                    if missing or files_now != [path]:
                        raise TargetedSynchronizationError("Synchronization note changed")
                    try:
                        stat = path.stat()
                    except OSError as exc:
                        if strict_reads:
                            raise TargetedSynchronizationError(
                                f"Targeted note could not be inspected: {relative_path}"
                            ) from exc
                        raise
                    previous = known.get(relative_path) if known is not None else session.get_note(relative_path)
                    if (
                        previous
                        and previous.mtime_ns == stat.st_mtime_ns
                        and previous.size == stat.st_size
                    ):
                        unchanged += 1
                        continue

                    try:
                        # Canonical spelling was established at admission and
                        # rechecked above. Use bounded inode facts rather than
                        # the reader's default exact-spelling sibling scans.
                        fact = vault._bounded_path_fact(relative_path)
                        note_text = vault.read_verified_markdown_snapshot(relative_path, fact=fact).content
                        current = path.stat()
                        if (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) != (
                            current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns,
                        ):
                            raise TargetedSynchronizationError("Synchronization note changed during read")
                        digest = hashlib.sha256(note_text.encode("utf-8")).hexdigest()
                    except (UnicodeDecodeError, OSError, VaultServiceError) as exc:
                        if strict_reads:
                            raise TargetedSynchronizationError(
                                f"Targeted note could not be read: {relative_path}"
                            ) from exc
                        continue
                    if previous and previous.sha256 == digest:
                        session.update_note_metadata(
                            path=relative_path,
                            mtime_ns=stat.st_mtime_ns,
                            size=stat.st_size,
                        )
                        unchanged += 1
                        continue

                    chunks = self._chunk_markdown(path.stem, note_text)
                    embedding_texts = [
                        self._build_embedding_text(path.stem, heading, content)
                        for heading, content in chunks
                    ]
                    vectors = self._embed(embedding_texts, _session=session)
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

    def _synchronization_configuration(self):
        with self._owner_guard(self._configuration_lock):
            vault_root = self.vault_root
            self._validate_source_root(vault_root)
            return vault_root, self.max_note_bytes, self._sync_configuration_revision

    def _sync_event(self, work, suffix, *, error=None):
        prefix = "semantic_sync" if work.full else "targeted_reindex"
        log_event(
            logger, logging.ERROR if suffix == "failed" else logging.INFO,
            f"{prefix}_{suffix}", f"Semantic synchronization {suffix}",
            operation="full" if work.full else "targeted",
            index_state=(IndexState.READY if suffix == "completed" else
                         IndexState.INDEXING if suffix == "started" else IndexState.ERROR),
            indexed_notes=work.counts["indexed"], unchanged_notes=work.counts["unchanged"],
            removed_notes=work.counts["removed"],
            duration_ms=round((time.perf_counter() - work.started_at) * 1000, 3),
            error_type=type(error).__name__ if error is not None else None,
        )

    @contextmanager
    def _sync_guard(self, check=None):
        resource = self._shared_resource
        if resource is None and check is None:
            with self._sync_lock:
                yield
            return
        check = check or resource.check_cancelled
        check()
        while not self._sync_lock.acquire(timeout=0.05):
            check()
        try:
            check()
            yield
        finally:
            self._sync_lock.release()

    @contextmanager
    def _sync_admission(self, cancel_event):
        """Track synchronization until all owned resources have unwound.

        Close fences admission and signals these tokens without waiting under
        an owner/resource lock. A yielded continuation has no admitted operation.
        """
        cancelled, done = threading.Event(), threading.Event()
        with self._sync_admin_lock:
            if self._sync_closed:
                raise ValueError("Synchronization owner is closed")
            self._sync_operations[cancelled] = done

        def check():
            self._raise_if_cancelled(cancel_event)
            self._raise_if_cancelled(cancelled)

        try:
            yield check
        finally:
            with self._sync_admin_lock:
                del self._sync_operations[cancelled]
                done.set()

    @contextmanager
    def _sync_preparation(self, check):
        """Linearize a destructive SQLite boundary against owner's close fence.

        Signature/backend work and owner-lock acquisition precede this guard.
        Only the current non-interruptible storage boundary holds the fence.
        """
        if check is None:
            yield
            return
        with self._sync_admin_lock:
            check()
            yield

    def begin_sync(
        self, *, relative_paths: Iterable[str] | None = None, rebuild: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> SyncContinuation:
        """Reserve this owner for an internal resumable sync, without scanning.

        Only one continuation may own progress. Call sync_step until complete,
        or cancel_sync to abandon it. Yielded handles hold no generation pin.
        Exceptions during a step terminalize its handle and retain committed data.
        """
        if not self._indexing_enabled:
            raise SemanticSearchUnavailableError("Semantic index is unavailable")
        with self._sync_admission(cancel_event) as check:
            work = None
            try:
                check()
                with self._embedding_operation(check):
                    work = self._prepare_sync(relative_paths=relative_paths, rebuild=rebuild, check=check)
                return work.handle
            except BaseException as error:
                if work is not None and self._active_sync is work:
                    self._fail_sync(work, error)
                raise

    def _prepare_sync(self, *, relative_paths, rebuild, check):
        self._validate_source_root(self.vault_root)
        with self._sync_guard():
            check()
            if self._sync_closed or self._active_sync is not None:
                raise ValueError("Synchronization owner is closed or already reserved")
            configuration = self._synchronization_configuration()
            # Root loss must be detected before signature preparation/reset.
            from app.services.semantic_sync import directory_identity

            root = configuration[0].resolve(strict=True)
            directory_identity(root)
            self._resolve_index_signature(persist_failure=True)
            self._validate_backend_before_destructive_prepare(persist_failure=True)
            check()
            with self._owner_guard(self._configuration_lock):
                if configuration != (self.vault_root, self.max_note_bytes, self._sync_configuration_revision):
                    raise ValueError("Synchronization configuration changed during preparation")
                if rebuild:
                    signature = self.index_signature
                    with self._owner_guard(self._state_init_lock):
                        with self._owner_guard(self._availability_lock):
                            with self._sync_preparation(check):
                                self._check_compatibility_invalidation()
                                self._search_available = False
                                self._persisted_read_only_search = False
                                self._persisted_read_only_basis = None
                                self._full_sync_required = True
                                self.repository.reset_index(signature, IndexState.UNINITIALIZED.value)
                                self._state_initialized = True
                initial_state = self._initialize_state(create_storage=True, _sync_check=check)
                with self._owner_guard(self._availability_lock):
                    available = self._search_available
                self._set_search_available(available or initial_state is IndexState.READY)
                # On restart ERROR/INDEXING cannot prove full reconciliation. A live
                # targeted retry may use its preserved searchable previous index.
                full = (relative_paths is None or rebuild or self._full_sync_required
                        or (not available and initial_state is not IndexState.READY))
                check()
                work = SyncWork(self, paths=relative_paths, full=full)
                try:
                    with self._sync_admin_lock:
                        check()
                        self._persist_state(IndexState.INDEXING)
                        self._active_sync = work
                        if full:
                            self._full_sync_required = True
                except BaseException:
                    work.close()
                    raise
                self._sync_event(work, "started")
                return work

    def _sync_work(self, handle):
        work = self._active_sync
        if (self._sync_closed or work is None or handle is not work.handle
                or handle.state is not SyncStepState.INCOMPLETE):
            raise ValueError("Invalid or inactive synchronization continuation")
        return work

    def _end_sync(self, work, state, *, error=None):
        work.handle._state = state
        self._active_sync = None
        try:
            work.close()
        finally:
            self._sync_event(work, "completed" if state is SyncStepState.COMPLETE else
                             "cancelled" if state is SyncStepState.CANCELLED else "failed",
                             error=error)

    def _fail_sync(self, work, error):
        # Never prepare/repair or write a rebound index while reporting failure.
        try:
            work.validate_storage(self)
            self._persist_state(IndexState.ERROR)
        except Exception:
            pass
        self._end_sync(work, SyncStepState.CANCELLED if isinstance(
            error, SynchronizationCancelledError) else SyncStepState.FAILED, error=error)

    def sync_step(
        self, handle: SyncContinuation, cancel_event: threading.Event | None = None,
        *, discovery_budget: int = 256,
    ) -> SyncStep:
        """Run one finite phase turn; return cumulative *durable* counts.

        Discovery consumes at most discovery_budget entries/directory operations.
        Processing and metadata reconciliation each touch at most index_batch_size
        notes. Only COMPLETE denotes successful synchronization. Cancellation and
        failure raise their existing exception and terminalize handle.state.
        """
        if type(discovery_budget) is not int or not 1 <= discovery_budget <= 4096:
            raise ValueError("discovery_budget must be an integer in 1..4096")
        # Identity rejection precedes generation admission and any storage work.
        work = self._sync_work(handle)
        with self._sync_admission(cancel_event) as admitted_check:
            if not work.turn_lock.acquire(blocking=False):
                raise ValueError("Synchronization continuation is already executing")
            def check():
                admitted_check()
                self._raise_if_cancelled(work.cancel_event)
            try:
                check()
                self._sync_work(handle)
                with self._embedding_operation(check, expected=work.generation):
                    with self._sync_guard(check):
                        self._sync_work(handle)
                        check()
                        with self._owner_guard(self._configuration_lock, check):
                            work.validate(self)
                            with work.spool() as connection:
                                if work.phase == "discover":
                                    work.discover(self, connection, discovery_budget, check)
                                elif work.phase == "targets":
                                    work.admit_targets(self, connection, discovery_budget, check)
                                elif work.phase == "process":
                                    work.process(self, connection, check, cancel_event)
                                elif work.phase == "stale":
                                    work.reconcile(self, connection, check)
                            check()
                            if work.phase == "finish":
                                # Dispose the scan before publishing success. A
                                # cleanup failure cannot produce a successful sync.
                                work.close()
                                # Acquire cancellable owner locks *before* the final
                                # non-interruptible commit. Late cancellation cannot
                                # reclassify that completed commit as cancellation.
                                with self._owner_guard(self._availability_lock, check):
                                    check()
                                    self._check_compatibility_invalidation()
                                    if work.full:
                                        self.repository.finalize_full_sync_success(
                                            last_successful_sync=datetime.now(timezone.utc).isoformat(
                                                timespec="seconds"), ready_state=IndexState.READY.value,
                                        )
                                        self._full_sync_required = False
                                    else:
                                        self._persist_state(IndexState.READY)
                                    self._search_available = True
                                    self._persisted_read_only_search = False
                                    self._persisted_read_only_basis = None
                                    self._end_sync(work, SyncStepState.COMPLETE)
                            return work.result()
            except BaseException as error:
                # The turn reservation excludes competing steps and cancellation
                # cleanup. Do not wait again on the very owner lock just cancelled.
                if self._active_sync is work:
                    self._fail_sync(work, error)
                raise
            finally:
                work.turn_lock.release()

    def cancel_sync(self, handle: SyncContinuation) -> None:
        """Abandon remaining work, waiting only for the active safe boundary."""
        work = self._sync_work(handle)
        work.cancel_event.set()
        with work.turn_lock:
            if self._active_sync is work:
                self._fail_sync(work, SynchronizationCancelledError("Synchronization cancelled"))

    def close_synchronization(self) -> None:
        """Fence admission, drain preparation/active turns, discard remaining work."""
        with self._sync_admin_lock:
            self._sync_closed = True
            operations = tuple(self._sync_operations.items())
            for cancelled, _ in operations:
                cancelled.set()
            work = self._active_sync
            if work is not None:
                work.cancel_event.set()
        # Completion signals follow resource-context exit, including pin/lease
        # cleanup. No owner/admin lock is held while a backend call drains.
        for _, done in operations:
            done.wait()
        work = self._active_sync
        if work is not None:
            with work.turn_lock:
                if self._active_sync is work:
                    self._fail_sync(work, SynchronizationCancelledError("Owner closed"))

    def _drive_sync(self, *, relative_paths=None, rebuild=False, cancel_event=None):
        if not self._indexing_enabled:
            raise SemanticSearchUnavailableError("Semantic index is unavailable")
        # Whole-operation legacy serialization is outside generation/turn locks.
        # A queued shared caller owns no pin or backend lease while it waits.
        with self._sync_admission(cancel_event) as check:
            try:
                check()
                with self._owner_guard(self._legacy_sync_lock, check if self._shared_resource is not None else None):
                    check()
                    handle = self.begin_sync(relative_paths=relative_paths, rebuild=rebuild, cancel_event=cancel_event)
                    while True:
                        result = self.sync_step(handle, cancel_event)
                        if result.state is SyncStepState.COMPLETE:
                            return result.counts()
            except ValueError:
                # Close may fence a nested turn between the yielded turns.
                check()
                raise

    def sync(self, cancel_event: threading.Event | None = None) -> dict[str, int]:
        """Drive bounded full synchronization to genuine completion."""
        return self._drive_sync(cancel_event=cancel_event)

    def rebuild(self, cancel_event: threading.Event | None = None) -> dict[str, int]:
        """Discard derived state and drive the production full-sync path."""
        return self._drive_sync(rebuild=True, cancel_event=cancel_event)

    def sync_paths(
        self, relative_paths: Sequence[str], cancel_event: threading.Event | None = None,
    ) -> dict[str, int]:
        """Drive bounded targeted work (or required full recovery) to completion."""
        if self._indexing_enabled:
            self._validate_source_root(self.vault_root)
        if not relative_paths:
            return {"indexed": 0, "unchanged": 0, "removed": 0}
        return self._drive_sync(relative_paths=relative_paths, cancel_event=cancel_event)

    def _rank_chunks(
        self, text: str, query: np.ndarray, chunks, *, min_score: float,
        strict: bool = False, cancel: CancellationToken | None = None,
    ) -> list[tuple[str, _RankedChunk]]:
        best: dict[str, _RankedChunk] = {}
        for chunk in chunks:
            if cancel is not None:
                cancel.check()
            if strict and (
                chunk.dimensions != query.size
                or len(chunk.embedding) != chunk.dimensions * np.dtype(np.float32).itemsize
            ):
                raise SemanticSearchUnavailableError("Invalid indexed vector")
            vector = np.frombuffer(
                chunk.embedding,
                dtype=np.float32,
                count=chunk.dimensions,
            )
            if strict and (not np.all(np.isfinite(vector)) or not np.any(vector)):
                raise SemanticSearchUnavailableError("Invalid indexed vector")
            if vector.size != query.size:
                continue
            semantic_score = float(np.dot(query, vector))
            if strict and not isfinite(semantic_score):
                raise SemanticSearchUnavailableError("Invalid semantic score")
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
            if strict and not all(isfinite(value) for value in (
                candidate.score, candidate.semantic_score, candidate.lexical_score,
            )):
                raise SemanticSearchUnavailableError("Invalid ranking score")
            current = best.get(chunk.path)
            if current is None or candidate.note_selection_key > current.note_selection_key:
                best[chunk.path] = candidate

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
        if cancel is not None:
            cancel.check()
        return [(path, ranked) for path, ranked in ordered if not ranked.score < relative_floor]

    @_embedding_pipeline
    def prepare_federation_candidates(
        self, eligible_paths: tuple[str, ...] | frozenset[str], *,
        space_id: SpaceId, budget: ScopedBudget, cancel: CancellationToken,
        min_score: float = 0.28,
    ) -> _PreparedSemanticSearch:
        """Prepare one complete bounded contribution, without query inference/writes.

        D3 callers must compare every available descriptor before scoring any owner.
        The prepared value holds the read snapshot, so scoring never reopens SQLite.
        """
        cancel.check()
        self._validate_source_root(self.vault_root)
        if not isfinite(min_score) or not 0 <= min_score <= 1:
            raise ValueError("invalid semantic threshold")
        if space_id not in budget.allocations:
            raise ValueError("space is outside budget selection")
        if not self._indexing_enabled or self._fingerprint_resolution_failed:
            raise SemanticSearchUnavailableError("Semantic index is unavailable")
        try:
            snapshot = self.repository.read_eligible_snapshot(
                eligible_paths, budget=budget, space_id=space_id, cancel=cancel,
            )
        except SemanticReadUnavailableError:
            raise SemanticSearchUnavailableError("Semantic index is unavailable") from None
        if not self._stored_signature_matches_configuration(snapshot.index_signature):
            raise SemanticSearchUnavailableError("Semantic index is unavailable")
        # Poll lock acquisition so queued compatibility work is cancellable too.
        while not self._federation_evidence_lock.acquire(timeout=0.05):
            cancel.check()
        try:
            cancel.check()
            evidence = self._federation_evidence
            if evidence is None:
                if not isinstance(self.embedder, EmbeddingCompatibilityProvider):
                    raise SemanticSearchUnavailableError("Embedding compatibility is unavailable")
                budget.charge(space_id, "probes")
                try:
                    evidence = self.embedder.resolve_embedding_compatibility()
                except Exception as exc:
                    cancel.check()
                    raise SemanticSearchUnavailableError("Embedding compatibility is unavailable") from exc
                cancel.check()
                if not isinstance(evidence, EmbeddingCompatibilityEvidence):
                    raise SemanticSearchUnavailableError("Embedding compatibility is unavailable")
                self._check_compatibility_invalidation()
                self._federation_evidence = evidence
        finally:
            self._federation_evidence_lock.release()
        signature = self._build_index_signature(evidence.embedding_fingerprint)
        if (
            snapshot.index_signature != signature
            or (self._resolved_index_signature is not None and self._resolved_index_signature != signature)
        ):
            raise SemanticSearchUnavailableError("Semantic index is unavailable")
        state = snapshot.index_state
        if state == IndexState.READY.value or (state is None and snapshot.has_chunks):
            basis: SemanticQueryBasis = "compatible_ready"
        else:
            with self._owner_guard(self._availability_lock):
                completed = self._search_available
            if not completed or state not in {IndexState.INDEXING.value, IndexState.ERROR.value}:
                raise SemanticSearchUnavailableError("Semantic index is unavailable")
            basis = (
                "compatible_previous_refresh" if state == IndexState.INDEXING.value
                else "compatible_previous_error"
            )
        cancel.check()
        return _PreparedSemanticSearch(
            ScoringDescriptor(
                self.model_name, evidence.embedding_fingerprint, signature,
                evidence.dimensions, evidence.backend_contract, min_score,
            ), basis, snapshot, self, budget, cancel, space_id, self._resource_generation,
        )

    @_embedding_pipeline
    def search_federation_candidates(
        self, text: str, eligible_paths: tuple[str, ...] | frozenset[str] = (), *,
        space_id: SpaceId, limit: int = 20, min_score: float = 0.28,
        budget: ScopedBudget, cancel: CancellationToken,
        prepared: _PreparedSemanticSearch | None = None,
        allow_blank_text: bool = False,
    ) -> PreciseSemanticResult:
        """Score only after compatibility preparation; never return a partial prefix.

        Scoped Knowledge Query permits whitespace as text under ADR 0006;
        retrieval callers retain nonblank validation unless explicitly opted in.
        """
        cancel.check()
        self._validate_source_root(self.vault_root)
        if (
            not isinstance(text, str) or not text or (not allow_blank_text and not text.strip())
            or len(text.encode("utf-8")) > 4096
            or type(limit) is not int or not 1 <= limit <= 100
            or not isfinite(min_score) or not 0 <= min_score <= 1
        ):
            raise ValueError("invalid semantic search arguments")
        if prepared is None:
            prepared = self.prepare_federation_candidates(
                eligible_paths, space_id=space_id, budget=budget, cancel=cancel,
                min_score=min_score,
            )
        elif (
            prepared.owner is not self or prepared.space_id != space_id
            or prepared.budget is not budget or prepared.cancel is not cancel
            or prepared.descriptor.min_score != min_score or eligible_paths
        ):
            raise ValueError("prepared search association mismatch")
        if not self._indexing_enabled or self._fingerprint_resolution_failed:
            raise SemanticSearchUnavailableError("Semantic index is unavailable")
        while not self._embed_lock.acquire(timeout=0.05):
            cancel.check()
        try:
            cancel.check()
            try:
                vectors = self.embedder.embed([text])
            except Exception as exc:
                cancel.check()
                raise SemanticSearchUnavailableError("Query embedding is unavailable") from exc
        finally:
            self._embed_lock.release()
        cancel.check()
        try:
            if len(vectors) != 1:
                raise ValueError("invalid query vector count")
            raw = np.asarray(vectors[0], dtype=np.float32).reshape(-1)
            if raw.size != prepared.descriptor.dimensions or not np.all(np.isfinite(raw)):
                raise ValueError("invalid query vector")
            query = self._normalize(raw)
            if not np.all(np.isfinite(query)) or not np.any(query):
                raise ValueError("invalid normalized query vector")
        except (ValueError, TypeError, SemanticIndexOperationError) as exc:
            raise SemanticSearchUnavailableError("Query embedding is unavailable") from exc
        self._check_compatibility_invalidation()
        ordered = self._rank_chunks(
            text, query, prepared.snapshot.chunks, min_score=min_score,
            strict=True, cancel=cancel,
        )
        candidates = []
        for path, ranked in ordered[:min(500, max(limit * 5, limit))]:
            cancel.check()
            candidates.append(PreciseSemanticCandidate(
                path, Path(path).stem, ranked.score, ranked.semantic_score,
                ranked.lexical_score, " ".join(ranked.chunk.content.split())[:500],
                ranked.chunk.heading[:200] if ranked.chunk.heading is not None else None,
            ))
        cancel.check()
        return PreciseSemanticResult(tuple(candidates), prepared.descriptor, prepared.index_basis)

    @_embedding_pipeline
    def search(
        self,
        text: str,
        *,
        folder: str = "",
        limit: int = 5,
        min_score: float = 0.28,
        eligible_paths: frozenset[str] | None = None,
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
        with self._owner_guard(self._availability_lock):
            persisted_read_only_search = self._persisted_read_only_search
        try:
            chunks = (
                self.repository.load_chunks_read_only()
                if persisted_read_only_search
                else self.repository.load_chunks()
            )
        except sqlite3.ProgrammingError:
            raise
        except ImmutableIndexInspectionUnavailableError as exc:
            self._set_search_available(False)
            raise SemanticSearchUnavailableError(
                "Persisted semantic index is unavailable for immutable reads"
            ) from exc
        except (OSError, sqlite3.DatabaseError) as exc:
            if not persisted_read_only_search:
                raise
            self._set_search_available(False)
            raise SemanticSearchUnavailableError(
                "Persisted semantic index is unavailable for immutable reads"
            ) from exc
        self._check_compatibility_invalidation()
        ordered = self._rank_chunks(
            text, query,
            (chunk for chunk in chunks
             if (not prefix or chunk.path.startswith(prefix))
             and (eligible_paths is None or chunk.path in eligible_paths)),
            min_score=min_score,
        )
        results: list[SemanticResult] = []
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
