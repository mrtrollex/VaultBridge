from __future__ import annotations

import hashlib
import re
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np

from app.core.config import DEFAULT_SEMANTIC_MODEL, Settings
from app.repositories.semantic import SemanticRepository, StoredChunk, StoredNote

DEFAULT_MODEL = DEFAULT_SEMANTIC_MODEL


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
        embedder: Embedder | None = None,
    ) -> None:
        self.vault_root = vault_root
        self.repository = repository
        self.model_name = model_name
        self.cache_dir = cache_dir or repository.db_path.parent / "models"
        self.max_note_bytes = max_note_bytes
        self.chunk_chars = max(250, chunk_chars)
        self.chunk_overlap = max(0, min(chunk_overlap, self.chunk_chars // 2))
        self.embedder = embedder or FastEmbedder(model_name, self.cache_dir)
        self._lock = threading.RLock()

    @property
    def db_path(self) -> Path:
        return self.repository.db_path

    @property
    def index_signature(self) -> str:
        return f"v1|{self.model_name}|{self.chunk_chars}|{self.chunk_overlap}"

    def reconfigure(self, *, vault_root: Path | None = None, max_note_bytes: int | None = None) -> None:
        with self._lock:
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
    def _split_long_piece(piece: str, max_chars: int) -> list[str]:
        if len(piece) <= max_chars:
            return [piece]
        parts: list[str] = []
        cursor = 0
        while cursor < len(piece):
            end = min(len(piece), cursor + max_chars)
            if end < len(piece):
                newline = piece.rfind("\n", cursor, end)
                space = piece.rfind(" ", cursor, end)
                split = max(newline, space)
                if split > cursor + max_chars // 2:
                    end = split
            chunk = piece[cursor:end].strip()
            if chunk:
                parts.append(chunk)
            cursor = max(end, cursor + 1)
        return parts

    def _markdown_blocks(self, text: str) -> list[str]:
        blocks: list[str] = []
        current: list[str] = []
        in_fence = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = not in_fence
                current.append(line)
                continue
            if not in_fence and not stripped:
                if current:
                    blocks.append("\n".join(current).strip())
                    current = []
                continue
            current.append(line)
        if current:
            blocks.append("\n".join(current).strip())
        return [block for block in blocks if block]

    def _chunk_markdown(self, title: str, text: str) -> list[tuple[str | None, str]]:
        body = self._strip_frontmatter(text).strip()
        if not body:
            return [(None, title)]

        blocks: list[str] = []
        for block in self._markdown_blocks(body):
            blocks.extend(self._split_long_piece(block, self.chunk_chars))

        raw_chunks: list[str] = []
        current = ""
        for block in blocks:
            candidate = block if not current else f"{current}\n\n{block}"
            if len(candidate) <= self.chunk_chars:
                current = candidate
                continue

            previous = current.strip()
            if previous:
                raw_chunks.append(previous)
            overlap = previous[-self.chunk_overlap :].strip() if previous and self.chunk_overlap else ""
            current = f"{overlap}\n\n{block}" if overlap else block
            if len(current) > self.chunk_chars:
                parts = self._split_long_piece(current, self.chunk_chars)
                raw_chunks.extend(parts[:-1])
                current = parts[-1]

        if current.strip():
            raw_chunks.append(current.strip())

        def nearest_heading(chunk: str) -> str | None:
            headings = [
                line.lstrip("# ").strip()
                for line in chunk.splitlines()
                if line.lstrip().startswith("#")
            ]
            return headings[-1][:200] if headings and headings[-1] else None

        return [(nearest_heading(chunk), chunk) for chunk in raw_chunks] or [(None, title)]

    def _eligible_files(self) -> list[Path]:
        if not self.vault_root.exists():
            return []
        excluded = {".obsidian", ".trash", ".git", ".obsidian-chatgpt-data"}
        files: list[Path] = []
        for path in self.vault_root.rglob("*.md"):
            try:
                relative_path = path.relative_to(self.vault_root)
                if any(part in excluded for part in relative_path.parts):
                    continue
                if path.is_file() and path.stat().st_size <= self.max_note_bytes:
                    files.append(path)
            except (OSError, ValueError):
                continue
        return files

    def sync(self) -> dict[str, int]:
        """Index only new/changed notes and remove deleted notes."""
        with self._lock:
            files = self._eligible_files()
            seen = {str(path.relative_to(self.vault_root)).replace("\\", "/") for path in files}
            indexed = unchanged = removed = 0

            with self.repository.transaction(self.index_signature) as session:
                known, removed = session.load_notes_and_remove_stale(seen)

                for path in files:
                    relative_path = str(path.relative_to(self.vault_root)).replace("\\", "/")
                    stat = path.stat()
                    previous = known.get(relative_path)
                    if (
                        previous
                        and previous.mtime_ns == stat.st_mtime_ns
                        and previous.size == stat.st_size
                    ):
                        unchanged += 1
                        continue

                    digest = self._sha256(path)
                    if previous and previous.sha256 == digest:
                        session.update_note_metadata(
                            path=relative_path,
                            mtime_ns=stat.st_mtime_ns,
                            size=stat.st_size,
                        )
                        unchanged += 1
                        continue

                    try:
                        note_text = path.read_text(encoding="utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue

                    chunks = self._chunk_markdown(path.stem, note_text)
                    vectors = self.embedder.embed(
                        [f"{path.stem}\n{content}" for _, content in chunks]
                    )
                    if len(vectors) != len(chunks):
                        raise RuntimeError(
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

            return {"indexed": indexed, "unchanged": unchanged, "removed": removed}

    def search(
        self,
        text: str,
        *,
        folder: str = "",
        limit: int = 5,
        min_score: float = 0.28,
    ) -> list[SemanticResult]:
        with self._lock:
            self.sync()
            embedded_query = self.embedder.embed([text])
            if not embedded_query:
                return []
            query = self._normalize(embedded_query[0])
            folder = folder.strip().replace("\\", "/").strip("/")
            prefix = f"{folder}/" if folder else ""
            best: dict[str, tuple[float, float, float, StoredChunk]] = {}

            for chunk in self.repository.load_chunks(self.index_signature):
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
        return self.repository.is_initialized()


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
        embedder=embedder,
    )
