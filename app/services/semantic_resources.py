"""Internal process-owned embedding resource; no runtime composition or owner state."""

from __future__ import annotations

import gc
import tempfile
import threading
import traceback
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager

import numpy as np

from app.core.config import Settings
from app.services.semantic_search import (
    EmbeddingCompatibilityEvidence,
    FastEmbedder,
    SemanticIndexOperationError,
    SemanticSearchUnavailableError,
)


def _backend_result(call):
    """Do not export backend tracebacks, including chained model-owning frames."""
    try:
        return call(), None
    except BaseException as error:
        failure = (
            type(error) if isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit))
            else SemanticIndexOperationError
        )
        pending, seen = [error], set()
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            pending.extend(exc for exc in (current.__cause__, current.__context__) if exc is not None)
            traceback.clear_frames(current.__traceback__)
            current.__traceback__ = None
        # Only a safe exception class crosses this boundary, never the exception
        # or its arguments. Raising happens after this except block has unwound.
        return None, failure


class SharedEmbeddingResource:
    """One lazy FastEmbedder with a cancellable backend lease and generation pins.

    Pins retain compatibility across a pipeline, not the backend lease. Independent
    owners can scan/read/rank concurrently and lease only actual backend calls.
    Invalidation blocks new pins, drains existing pins, then releases the old model
    before publishing a generation. No callback ever acquires an owner lock.
    The composing process owns close(); individual semantic owners do not close it.
    """

    def __init__(self, settings: Settings) -> None:
        self.model_name = settings.semantic_model
        self.cache_dir = settings.semantic_data_path / "models"
        self._batch_size = settings.semantic_embed_batch_size
        self._cpu_mem_arena = settings.semantic_onnx_cpu_mem_arena
        self._condition = threading.Condition()
        self._local = threading.local()
        self._backend: FastEmbedder | None = None
        self._generation = 0
        self._pins = 0
        self._busy = False
        self._invalidating = False
        self._cleaning = False
        self._cleanup_failed = False
        self._closing = False
        self._closed = False

    @property
    def generation(self) -> int:
        with self._condition:
            return self._generation

    def is_current(self, generation: int | None) -> bool:
        """Model-free health check, including pending invalidation and shutdown."""
        with self._condition:
            return (
                not self._closing and not self._invalidating and not self._cleanup_failed
                and generation == self._generation
            )

    def _check_open(self) -> None:
        if self._closing:
            raise SemanticSearchUnavailableError("Embedding resource is closed")
        if self._cleanup_failed:
            raise SemanticSearchUnavailableError("Embedding resource cleanup failed")

    def check_cancelled(self) -> None:
        """Check the current pipeline token without taking any resource/owner lock."""
        if getattr(self._local, "depth", 0):
            self._local.check()

    @contextmanager
    def operation(
        self, check: Callable[[], None] = lambda: None, *, expected: int | None = None,
    ) -> Iterator[int]:
        """Pin a generation without loading a model; nested pins never reacquire."""
        check()
        if getattr(self._local, "depth", 0):
            if expected is not None and expected != self._local.generation:
                raise SemanticSearchUnavailableError("Embedding generation is stale")
            self._local.depth += 1
            try:
                yield self._local.generation
            finally:
                self._local.depth -= 1
            return
        with self._condition:
            while self._invalidating:
                self._check_open()
                check()
                self._condition.wait(0.05)
            self._check_open()
            check()
            if expected is not None and expected != self._generation:
                raise SemanticSearchUnavailableError("Embedding generation is stale")
            self._pins += 1
            self._local.depth = 1
            self._local.generation = self._generation
            self._local.check = check
        try:
            yield self._local.generation
        finally:
            with self._condition:
                self._local.depth = 0
                self._local.check = None
                self._pins -= 1
                cleanup = self._reserve_cleanup()
                self._condition.notify_all()
            if cleanup:
                self._release_generation()

    def _reserve_cleanup(self) -> bool:
        # Condition held. All backend calls are pinned, so zero pins also means
        # no active backend call. Reservation excludes every conflicting cleanup.
        if self._pins or self._cleaning or not self._invalidating:
            return False
        self._cleaning = True
        return True

    def _release_generation(self) -> None:
        # Reserved under the condition, executed outside it. Waiters can poll
        # cancellation; health and shutdown admission remain cheap during release.
        failure = None
        completed = False
        try:
            if self._backend is not None:
                _, failure = _backend_result(self._backend.invalidate_embedding_compatibility_cache)
            # Released model wrappers can contain cycles. Collect them before a
            # replacement is admitted, after detached backend frames are gone.
            gc.collect()
            completed = failure is None
        finally:
            with self._condition:
                self._cleanup_failed = self._cleanup_failed or not completed
                if completed:
                    self._generation += 1
                self._cleaning = False
                self._invalidating = False
                if self._closing:
                    self._closed = True
                self._condition.notify_all()
        if failure is not None:
            raise failure("Shared embedding resource cleanup failed") from None

    def invalidate_embedding_compatibility_cache(self) -> None:
        """Drain externally; inside a failing pipeline defer release to its exit."""
        with self._condition:
            self._check_open()
            self._invalidating = True
            if getattr(self._local, "depth", 0):
                return
            cleanup = self._reserve_cleanup()
            if not cleanup:
                while self._invalidating:
                    self._condition.wait(0.05)
                if self._cleanup_failed:
                    raise SemanticIndexOperationError("Shared embedding resource cleanup failed")
        if cleanup:
            self._release_generation()

    def _get_backend(self) -> FastEmbedder:
        # Called only while leased. Cache failure never chooses another location.
        if self._backend is None:
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryFile(dir=self.cache_dir):
                    pass
            except OSError:
                raise SemanticIndexOperationError("Shared model cache is unavailable") from None
            self._backend = FastEmbedder(
                self.model_name, self.cache_dir, batch_size=self._batch_size,
                enable_cpu_mem_arena=self._cpu_mem_arena,
            )
        return self._backend

    def _call(self, method: str, *args):
        with self.operation():
            with self._condition:
                while self._busy:
                    self._check_open()
                    self._local.check()
                    self._condition.wait(0.05)
                self._check_open()
                self._local.check()
                self._busy = True
            try:
                # Backend validation may call backend.embed directly. It must not
                # enter this wrapper recursively or reacquire the backend lease.
                result, failure = _backend_result(lambda: getattr(self._get_backend(), method)(*args))
                if failure is not None:
                    with self._condition:
                        self._invalidating = True
            finally:
                with self._condition:
                    self._busy = False
                    self._condition.notify_all()
            if failure is not None:
                raise failure("Shared embedding backend or model cache operation failed") from None
            return result

    def embed(self, texts: Sequence[str]) -> list[np.ndarray]:
        return self._call("embed", texts)

    def resolve_embedding_fingerprint(self) -> str:
        return self._call("resolve_embedding_fingerprint")

    def resolve_embedding_compatibility(self) -> EmbeddingCompatibilityEvidence:
        return self._call("resolve_embedding_compatibility")

    def validate_embedding_backend(self) -> None:
        self._call("validate_embedding_backend")

    def close(self) -> None:
        """Reject new leases immediately and drain pipelines before model release."""
        if getattr(self._local, "depth", 0):
            raise RuntimeError("Cannot close an embedding resource from its operation")
        with self._condition:
            self._closing = True
            self._condition.notify_all()
            if self._closed:
                return
            self._invalidating = True
            while not self._closed:
                if self._reserve_cleanup():
                    break
                self._condition.wait(0.05)
            else:
                return
        self._release_generation()
