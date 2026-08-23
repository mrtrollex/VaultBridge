from __future__ import annotations

import hashlib
import re
import threading
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from itertools import batched
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol

import numpy as np

from app.core.config import DEFAULT_SEMANTIC_MODEL, Settings
from app.repositories.semantic import SemanticRepository, StoredChunk, StoredNote
from app.services.vault import SEMANTIC_EXCLUDED_DIRECTORIES, eligible_markdown_files

DEFAULT_MODEL = DEFAULT_SEMANTIC_MODEL
INDEX_STATE_METADATA_KEY = "index_state"
LAST_SUCCESSFUL_SYNC_METADATA_KEY = "last_successful_sync"
INDEX_FORMAT_VERSION = "v3-heading-context"
HEADING_DIGEST_LENGTH = 12


class IndexState(str, Enum):
    UNINITIALIZED = "uninitialized"
    INDEXING = "indexing"
    READY = "ready"
    ERROR = "error"


class SemanticSearchUnavailableError(RuntimeError):
    """No compatible completed semantic index is currently searchable."""


class SynchronizationCancelledError(RuntimeError):
    """Semantic synchronization stopped cooperatively at a safe boundary."""


class TargetedSynchronizationError(RuntimeError):
    """A requested note could not be authoritatively refreshed."""


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[np.ndarray]: ...


class FastEmbedder:
    """Load the ONNX embedding model only when semantic search is first used."""

    def __init__(self, model_name: str, cache_dir: Path) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None
        self._lock = threading.Lock()

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                try:
                    from fastembed import TextEmbedding
                except ImportError as exc:  # pragma: no cover
                    raise RuntimeError("fastembed is not installed") from exc
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = TextEmbedding(
                    model_name=self.model_name,
                    cache_dir=str(self.cache_dir),
                    providers=["CPUExecutionProvider"],
                )
        return self._model

    def embed(self, texts: Sequence[str]) -> list[np.ndarray]:
        if not texts:
            return []
        model = self._get_model()
        return [np.asarray(vector, dtype=np.float32) for vector in model.embed(list(texts))]


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
class SemanticHealthStatus:
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
        index_batch_size: int = 25,
        embedder: Embedder | None = None,
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
        self.embedder = embedder or FastEmbedder(model_name, self.cache_dir)
        self._sync_lock = threading.Lock()
        self._configuration_lock = threading.Lock()
        self._availability_lock = threading.Lock()
        self._embed_lock = threading.Lock()
        self._search_available = False
        self._state_init_lock = threading.Lock()
        self._state_initialized = False

    @property
    def db_path(self) -> Path:
        return self.repository.db_path

    @property
    def index_signature(self) -> str:
        return f"{INDEX_FORMAT_VERSION}|{self.model_name}|{self.chunk_chars}|{self.chunk_overlap}"

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
        if self._state_initialized:
            return self._read_persisted_state()

        with self._state_init_lock:
            if self._state_initialized:
                return self._read_persisted_state()
            if not create_storage and not self.repository.storage_initialized():
                return IndexState.UNINITIALIZED

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

    def health_status(self) -> SemanticHealthStatus:
        """Return a read-only lifecycle and storage snapshot for operator health reporting."""
        storage = self.repository.read_status()
        compatible_storage = (
            storage.storage_initialized and storage.index_signature == self.index_signature
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
            if state is IndexState.INDEXING and not self._state_initialized:
                state = IndexState.ERROR

        with self._availability_lock:
            search_available = self._search_available
        return SemanticHealthStatus(
            state=state,
            search_available=compatible_storage and (search_available or state is IndexState.READY),
            indexed_notes=storage.indexed_notes if compatible_storage else 0,
            semantic_chunks=storage.semantic_chunks if compatible_storage else 0,
            last_successful_sync=storage.last_successful_sync if compatible_storage else None,
        )

    def _set_search_available(self, available: bool) -> None:
        with self._availability_lock:
            self._search_available = available

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
            raise RuntimeError("Embedding model returned a zero vector")
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
    def _lexical_score(
        cls,
        query_text: str,
        *,
        path: str,
        heading: str | None,
        content: str,
    ) -> float:
        query_terms = cls._terms(query_text)
        if not query_terms:
            return 0.0

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

        score = (
            0.40 * title_cov
            + 0.25 * path_cov
            + 0.10 * heading_cov
            + 0.20 * content_cov
            + exact_bonus
        )
        return min(1.0, score)

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
    ) -> list[Path]:
        resolved_root = vault_root.resolve()
        files: list[Path] = []
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
            try:
                path = (resolved_root / relative_path).resolve()
                resolved_relative = path.relative_to(resolved_root)
                if any(
                    part in SEMANTIC_EXCLUDED_DIRECTORIES for part in resolved_relative.parts
                ):
                    raise TargetedSynchronizationError(
                        f"Targeted note path is excluded: {normalized}"
                    )
                if not path.is_file():
                    raise TargetedSynchronizationError(
                        f"Targeted note is unavailable: {normalized}"
                    )
                if path.stat().st_size > max_note_bytes:
                    raise TargetedSynchronizationError(
                        f"Targeted note is too large: {normalized}"
                    )
                files.append(path)
            except TargetedSynchronizationError:
                raise
            except (OSError, ValueError) as exc:
                raise TargetedSynchronizationError(
                    f"Targeted note is inaccessible: {normalized}"
                ) from exc
        return files

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
                        raise RuntimeError("Embedding model returned an unexpected number of vectors")

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
        files = self._target_files(vault_root, max_note_bytes, relative_paths)
        with self.repository.transaction() as session:
            known = session.load_notes()
        indexed, unchanged = self._index_files(
            files=files,
            vault_root=vault_root,
            known=known,
            cancel_event=cancel_event,
            strict_reads=True,
        )
        return {"indexed": indexed, "unchanged": unchanged, "removed": 0}

    def _run_synchronization_locked(
        self,
        operation: Callable[[], dict[str, int]],
        *,
        record_full_sync: bool = False,
    ) -> dict[str, int]:
        initial_state = self._initialize_state(create_storage=True)
        with self._availability_lock:
            search_available = self._search_available
        self._set_search_available(search_available or initial_state is IndexState.READY)
        self._persist_state(IndexState.INDEXING)
        try:
            result = operation()
            if record_full_sync:
                self.repository.set_metadata(
                    LAST_SUCCESSFUL_SYNC_METADATA_KEY,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                )
        except Exception:
            self._persist_state(IndexState.ERROR)
            raise

        self._persist_state(IndexState.READY)
        self._set_search_available(True)
        return result

    def sync(self, cancel_event: threading.Event | None = None) -> dict[str, int]:
        """Index only new/changed notes and remove deleted notes."""
        with self._sync_lock:
            return self._run_synchronization_locked(
                lambda: self._sync_all_locked(cancel_event),
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
            initial_state = self._initialize_state(create_storage=True)
            with self._availability_lock:
                search_available = self._search_available
            if not search_available and initial_state is not IndexState.READY:
                return self._run_synchronization_locked(
                    lambda: self._sync_all_locked(cancel_event),
                    record_full_sync=True,
                )
            return self._run_synchronization_locked(
                lambda: self._sync_targets_locked(paths, cancel_event)
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
        best: dict[str, tuple[float, float, float, StoredChunk]] = {}

        for chunk in self.repository.load_chunks():
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
            rank_score = min(1.0, semantic_score + 0.70 * lexical_score)
            if chunk.path not in best or rank_score > best[chunk.path][0]:
                best[chunk.path] = (rank_score, semantic_score, lexical_score, chunk)

        results: list[SemanticResult] = []
        ordered = sorted(best.items(), key=lambda item: item[1][0], reverse=True)
        relative_floor = ordered[0][1][0] * 0.78 if ordered else 0.0
        for path, (rank_score, semantic_score, lexical_score, chunk) in ordered:
            if rank_score < relative_floor:
                continue
            results.append(
                SemanticResult(
                    path=path,
                    title=Path(path).stem,
                    score=round(rank_score, 4),
                    semantic_score=round(semantic_score, 4),
                    lexical_score=round(lexical_score, 4),
                    snippet=" ".join(chunk.content.split())[:500],
                    heading=chunk.heading,
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
        index_batch_size=settings.semantic_index_batch_size,
        embedder=embedder,
    )
