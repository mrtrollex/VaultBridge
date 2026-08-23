from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np

from app.core.config import DEFAULT_SEMANTIC_MODEL, Settings

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
        return [np.asarray(v, dtype=np.float32) for v in model.embed(list(texts))]


@dataclass(frozen=True)
class SemanticResult:
    path: str
    title: str
    score: float
    semantic_score: float
    lexical_score: float
    snippet: str
    heading: str | None


class SemanticIndex:
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
        db_path: Path,
        model_name: str = DEFAULT_MODEL,
        cache_dir: Path | None = None,
        max_note_bytes: int = 1_000_000,
        chunk_chars: int = 600,
        chunk_overlap: int = 100,
        embedder: Embedder | None = None,
    ) -> None:
        self.vault_root = vault_root
        self.db_path = db_path
        self.model_name = model_name
        self.cache_dir = cache_dir or db_path.parent / "models"
        self.max_note_bytes = max_note_bytes
        self.chunk_chars = max(250, chunk_chars)
        self.chunk_overlap = max(0, min(chunk_overlap, self.chunk_chars // 2))
        self.embedder = embedder or FastEmbedder(model_name, self.cache_dir)
        self._lock = threading.RLock()

    def reconfigure(self, *, vault_root: Path | None = None, max_note_bytes: int | None = None) -> None:
        with self._lock:
            if vault_root is not None:
                self.vault_root = vault_root
            if max_note_bytes is not None:
                self.max_note_bytes = max_note_bytes

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
                path TEXT PRIMARY KEY,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                indexed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                heading TEXT,
                content TEXT NOT NULL,
                embedding BLOB NOT NULL,
                dimensions INTEGER NOT NULL,
                FOREIGN KEY(path) REFERENCES notes(path) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
            """
        )
        signature = f"v1|{self.model_name}|{self.chunk_chars}|{self.chunk_overlap}"
        current = conn.execute("SELECT value FROM meta WHERE key='index_signature'").fetchone()
        if current and current["value"] != signature:
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM notes")
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('index_signature', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (signature,),
        )
        conn.commit()
        return conn

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
        return "".join(ch for ch in decomposed if not unicodedata.combining(ch))

    @classmethod
    def _terms(cls, text: str) -> set[str]:
        folded = cls._fold_text(text)
        return {
            token
            for token in re.findall(r"[\w-]+", folded, flags=re.UNICODE)
            if len(token) > 2 and token not in cls._STOPWORDS
        }

    @classmethod
    def _lexical_score(cls, query_text: str, *, path: str, heading: str | None, content: str) -> float:
        """Small lexical/title boost layered on top of semantic similarity.

        The semantic model remains authoritative. This score mainly prevents a
        generic semantic hit from outranking a note whose title/path clearly
        contains the user's topic words.
        """
        query_terms = cls._terms(query_text)
        if not query_terms:
            return 0.0

        def coverage(text: str) -> float:
            terms = cls._terms(text)
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
            headings = [line.lstrip("# ").strip() for line in chunk.splitlines() if line.lstrip().startswith("#")]
            return headings[-1][:200] if headings and headings[-1] else None

        return [(nearest_heading(chunk), chunk) for chunk in raw_chunks] or [(None, title)]

    def _eligible_files(self) -> list[Path]:
        if not self.vault_root.exists():
            return []
        excluded = {".obsidian", ".trash", ".git", ".obsidian-chatgpt-data"}
        files: list[Path] = []
        for path in self.vault_root.rglob("*.md"):
            try:
                rel = path.relative_to(self.vault_root)
                if any(part in excluded for part in rel.parts):
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
            seen = {str(p.relative_to(self.vault_root)).replace("\\", "/") for p in files}
            indexed = unchanged = removed = 0

            with self._connect() as conn:
                known = {
                    row["path"]: row
                    for row in conn.execute("SELECT path, mtime_ns, size, sha256 FROM notes").fetchall()
                }

                for rel_path in set(known) - seen:
                    conn.execute("DELETE FROM notes WHERE path=?", (rel_path,))
                    removed += 1

                for path in files:
                    rel_path = str(path.relative_to(self.vault_root)).replace("\\", "/")
                    stat = path.stat()
                    previous = known.get(rel_path)
                    if previous and previous["mtime_ns"] == stat.st_mtime_ns and previous["size"] == stat.st_size:
                        unchanged += 1
                        continue

                    digest = self._sha256(path)
                    if previous and previous["sha256"] == digest:
                        conn.execute(
                            "UPDATE notes SET mtime_ns=?, size=? WHERE path=?",
                            (stat.st_mtime_ns, stat.st_size, rel_path),
                        )
                        unchanged += 1
                        continue

                    try:
                        text = path.read_text(encoding="utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue

                    chunks = self._chunk_markdown(path.stem, text)
                    vectors = self.embedder.embed([f"{path.stem}\n{content}" for _, content in chunks])
                    if len(vectors) != len(chunks):
                        raise RuntimeError("Embedding model returned an unexpected number of vectors")

                    conn.execute("DELETE FROM chunks WHERE path=?", (rel_path,))
                    conn.execute(
                        "INSERT INTO notes(path, mtime_ns, size, sha256, indexed_at) VALUES(?, ?, ?, ?, ?) "
                        "ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns, size=excluded.size, "
                        "sha256=excluded.sha256, indexed_at=excluded.indexed_at",
                        (
                            rel_path,
                            stat.st_mtime_ns,
                            stat.st_size,
                            digest,
                            datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        ),
                    )
                    for i, ((chunk_heading, content), vector) in enumerate(zip(chunks, vectors, strict=True)):
                        normalized = self._normalize(vector)
                        conn.execute(
                            "INSERT INTO chunks(path, chunk_index, heading, content, embedding, dimensions) "
                            "VALUES(?, ?, ?, ?, ?, ?)",
                            (
                                rel_path,
                                i,
                                chunk_heading,
                                content,
                                normalized.astype(np.float32).tobytes(),
                                int(normalized.size),
                            ),
                        )
                    indexed += 1
                conn.commit()

            return {"indexed": indexed, "unchanged": unchanged, "removed": removed}

    def search(self, text: str, *, folder: str = "", limit: int = 5, min_score: float = 0.28) -> list[SemanticResult]:
        with self._lock:
            self.sync()
            embedded_query = self.embedder.embed([text])
            if not embedded_query:
                return []
            query = self._normalize(embedded_query[0])
            folder = folder.strip().replace("\\", "/").strip("/")
            prefix = f"{folder}/" if folder else ""
            best: dict[str, tuple[float, float, float, sqlite3.Row]] = {}

            with self._connect() as conn:
                rows = conn.execute("SELECT path, heading, content, embedding, dimensions FROM chunks").fetchall()

            for row in rows:
                path = row["path"]
                if prefix and not path.startswith(prefix):
                    continue
                vector = np.frombuffer(row["embedding"], dtype=np.float32, count=row["dimensions"])
                if vector.size != query.size:
                    continue
                semantic_score = float(np.dot(query, vector))
                if semantic_score < min_score:
                    continue
                lexical_score = self._lexical_score(
                    text,
                    path=path,
                    heading=row["heading"],
                    content=row["content"],
                )
                # Semantic similarity is still the base signal. Lexical overlap
                # acts as a reranking bonus, especially for title/folder hits.
                rank_score = min(1.0, semantic_score + 0.70 * lexical_score)
                if path not in best or rank_score > best[path][0]:
                    best[path] = (rank_score, semantic_score, lexical_score, row)

            results: list[SemanticResult] = []
            ordered = sorted(best.items(), key=lambda item: item[1][0], reverse=True)
            # Do not force weak tail results merely to fill `limit`. Keep notes
            # reasonably close to the best hit for this query.
            relative_floor = ordered[0][1][0] * 0.78 if ordered else 0.0
            for path, (rank_score, semantic_score, lexical_score, row) in ordered:
                if rank_score < relative_floor:
                    continue
                results.append(
                    SemanticResult(
                        path=path,
                        title=Path(path).stem,
                        score=round(rank_score, 4),
                        semantic_score=round(semantic_score, 4),
                        lexical_score=round(lexical_score, 4),
                        snippet=" ".join(row["content"].split())[:500],
                        heading=row["heading"],
                    )
                )
                if len(results) >= limit:
                    break
            return results

    def is_initialized(self) -> bool:
        try:
            if not self.db_path.exists():
                return False
            with sqlite3.connect(self.db_path, timeout=3) as conn:
                row = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
                return bool(row and row[0] > 0)
        except sqlite3.Error:
            return False


def semantic_index_from_settings(settings: Settings) -> SemanticIndex:
    data_path = settings.semantic_data_path
    return SemanticIndex(
        vault_root=settings.vault_path,
        db_path=data_path / "semantic-index.sqlite3",
        cache_dir=data_path / "models",
        model_name=settings.semantic_model,
        max_note_bytes=settings.max_note_bytes,
        chunk_chars=settings.semantic_chunk_chars,
        chunk_overlap=settings.semantic_chunk_overlap,
    )
