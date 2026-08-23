from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

SEMANTIC_EXCLUDED_DIRECTORIES = frozenset(
    {".obsidian", ".trash", ".git", ".obsidian-chatgpt-data"}
)


def eligible_markdown_files(vault_root: Path, max_note_bytes: int) -> list[Path]:
    """Return contained Markdown files accepted by full semantic synchronization."""
    vault_root = Path(vault_root)
    if not vault_root.exists():
        return []

    try:
        resolved_root = vault_root.resolve()
    except OSError:
        return []

    files: dict[Path, None] = {}
    try:
        for discovered_path in vault_root.rglob("*.md"):
            try:
                path = discovered_path.resolve()
                relative_path = path.relative_to(resolved_root)
                if any(part in SEMANTIC_EXCLUDED_DIRECTORIES for part in relative_path.parts):
                    continue
                if path.is_file() and path.stat().st_size <= max_note_bytes:
                    files[path] = None
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return list(files)


class VaultServiceError(Exception):
    """Base class for expected vault-operation failures."""


class VaultValidationError(VaultServiceError):
    """The requested vault path or note name is invalid."""


class NoteNotFoundError(VaultServiceError):
    """The requested note does not exist."""


class NoteTooLargeError(VaultServiceError):
    """A note exceeds the configured size limit."""


class NoteConflictError(VaultServiceError):
    """A note cannot be created because different content already exists."""


@dataclass(frozen=True)
class NoteWriteResult:
    path: str
    status: Literal["created", "unchanged", "appended", "already_applied"]


@dataclass(frozen=True)
class NoteReadResult:
    path: str
    content: str


@dataclass(frozen=True)
class NoteSearchResult:
    path: str
    title: str
    snippet: str


@dataclass(frozen=True)
class NoteListResult:
    path: str
    title: str
    modified: str


class VaultService:
    """Safe Markdown note operations scoped to one Obsidian vault root."""

    def __init__(self, *, vault_root: Path, max_note_bytes: int) -> None:
        if max_note_bytes <= 0:
            raise ValueError("max_note_bytes must be positive")
        self.vault_root = Path(vault_root).expanduser().resolve()
        self.max_note_bytes = max_note_bytes

    def vault_exists(self) -> bool:
        return self.vault_root.exists()

    def count_notes(self) -> int:
        """Count Markdown files eligible for full semantic synchronization."""
        return len(eligible_markdown_files(self.vault_root, self.max_note_bytes))

    def resolve_path(self, raw: str) -> Path:
        normalized = raw.strip().replace("\\", "/")
        path = Path(normalized)
        if path.is_absolute():
            raise VaultValidationError("Path must be vault-relative")
        candidate = (self.vault_root / path).resolve()
        try:
            candidate.relative_to(self.vault_root)
        except ValueError as exc:
            raise VaultValidationError("Path escapes the vault") from exc
        return candidate

    def existing_relative_path(self, raw: str) -> str | None:
        path = self.resolve_path(raw)
        if not path.exists():
            return None
        return self._relative_path(path).replace("\\", "/")

    def create_note(
        self,
        *,
        title: str,
        folder: str,
        content: str,
        tags: list[str],
    ) -> NoteWriteResult:
        folder_raw = (folder or "Inbox").strip().replace("\\", "/")
        folder_path = self.resolve_path(folder_raw)
        folder_path.mkdir(parents=True, exist_ok=True)
        path = self.resolve_path(str(Path(folder_raw) / self._safe_filename(title)))
        self._ensure_markdown(path)

        created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tags_yaml = json.dumps(tags, ensure_ascii=False)
        markdown = (
            "---\n"
            f"created: {created}\n"
            f"tags: {tags_yaml}\n"
            "source: chatgpt\n"
            "---\n\n"
            f"{content.rstrip()}\n"
        )

        if len(markdown.encode("utf-8")) > self.max_note_bytes:
            raise NoteTooLargeError("Generated note is too large")

        if path.exists():
            existing = self._read_text(path)
            expected_tail = f"{content.rstrip()}\n"
            if existing.endswith(expected_tail):
                return NoteWriteResult(path=self._relative_path(path), status="unchanged")
            raise NoteConflictError(
                "A note with this title already exists. Use appendNote or choose another title."
            )

        path.write_text(markdown, encoding="utf-8")
        return NoteWriteResult(path=self._relative_path(path), status="created")

    def append_note(self, *, path: str, content: str, dedupe_key: str | None = None) -> NoteWriteResult:
        note_path = self.resolve_path(path)
        self._ensure_markdown(note_path)
        existing = self._read_text(note_path)

        marker = None
        if dedupe_key:
            safe_key = re.sub(r"[^A-Za-z0-9._:-]", "_", dedupe_key)
            marker = f"<!-- chatgpt-append:{safe_key} -->"
            if marker in existing:
                return NoteWriteResult(path=self._relative_path(note_path), status="already_applied")

        addition = "\n\n" + content.strip() + "\n"
        if marker:
            addition += marker + "\n"

        if len((existing + addition).encode("utf-8")) > self.max_note_bytes:
            raise NoteTooLargeError("Resulting note would be too large")

        with note_path.open("a", encoding="utf-8") as file:
            file.write(addition)

        return NoteWriteResult(path=self._relative_path(note_path), status="appended")

    def read_note(self, path: str) -> NoteReadResult:
        note_path = self.resolve_path(path)
        self._ensure_markdown(note_path)
        return NoteReadResult(path=self._relative_path(note_path), content=self._read_text(note_path))

    def search_notes(self, *, query: str, folder: str = "", limit: int = 10) -> list[NoteSearchResult]:
        root = self.resolve_path(folder) if folder else self.vault_root
        if not root.exists():
            return []

        needle = query.casefold()
        results: list[NoteSearchResult] = []
        for path in root.rglob("*.md"):
            if not path.is_file() or path.stat().st_size > self.max_note_bytes:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            title_match = needle in path.stem.casefold()
            text_folded = text.casefold()
            position = text_folded.find(needle)
            if position < 0 and not title_match:
                continue
            if position >= 0:
                snippet_start = max(0, position - 120)
                snippet_end = min(len(text), position + len(query) + 220)
                snippet = text[snippet_start:snippet_end].replace("\n", " ").strip()
            else:
                snippet = text[:320].replace("\n", " ").strip()
            results.append(
                NoteSearchResult(
                    path=self._relative_path(path),
                    title=path.stem,
                    snippet=snippet[:400],
                )
            )
            if len(results) >= limit:
                break
        return results

    def list_notes(self, *, folder: str = "", limit: int = 50) -> list[NoteListResult]:
        root = self.resolve_path(folder) if folder else self.vault_root
        if not root.exists():
            return []

        notes: list[NoteListResult] = []
        for path in sorted(root.rglob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True):
            notes.append(
                NoteListResult(
                    path=self._relative_path(path),
                    title=path.stem,
                    modified=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                )
            )
            if len(notes) >= limit:
                break
        return notes

    def _relative_path(self, path: Path) -> str:
        return str(path.relative_to(self.vault_root))

    @staticmethod
    def _safe_filename(title: str) -> str:
        value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", title).strip().rstrip(". ")
        value = re.sub(r"\s+", " ", value)
        if not value:
            raise VaultValidationError("Title does not produce a valid filename")
        return f"{value[:180]}.md"

    @staticmethod
    def _ensure_markdown(path: Path) -> None:
        if path.suffix.lower() != ".md":
            raise VaultValidationError("Only .md files are allowed")

    def _read_text(self, path: Path) -> str:
        if not path.exists() or not path.is_file():
            raise NoteNotFoundError("Note not found")
        if path.stat().st_size > self.max_note_bytes:
            raise NoteTooLargeError("Note is too large")
        return path.read_text(encoding="utf-8")
