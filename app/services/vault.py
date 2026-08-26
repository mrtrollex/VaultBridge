from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

SEMANTIC_EXCLUDED_DIRECTORIES = frozenset(
    {".obsidian", ".trash", ".git", ".obsidian-chatgpt-data"}
)


def _resolve_contained_path(path: Path, resolved_root: Path) -> Path:
    """Resolve one path and require its real target to remain under the vault root."""
    resolved_path = path.resolve()
    resolved_path.relative_to(resolved_root)
    return resolved_path


def contained_markdown_files(
    vault_root: Path,
    discovery_root: Path | None = None,
) -> list[Path]:
    """Return unique Markdown files whose resolved targets remain inside the vault."""
    vault_root = Path(vault_root)
    discovery_root = vault_root if discovery_root is None else Path(discovery_root)
    try:
        resolved_root = vault_root.resolve()
        resolved_discovery_root = _resolve_contained_path(discovery_root, resolved_root)
    except (OSError, ValueError):
        return []
    if not resolved_discovery_root.exists():
        return []

    files: dict[Path, None] = {}
    try:
        for discovered_path in resolved_discovery_root.rglob("*.md"):
            try:
                path = _resolve_contained_path(discovered_path, resolved_root)
                if path.suffix.lower() == ".md" and path.is_file():
                    files[path] = None
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return list(files)


def eligible_markdown_files(vault_root: Path, max_note_bytes: int) -> list[Path]:
    """Return contained Markdown files accepted by full semantic synchronization."""
    vault_root = Path(vault_root)
    try:
        resolved_root = vault_root.resolve()
    except OSError:
        return []

    files: list[Path] = []
    for path in contained_markdown_files(resolved_root):
        try:
            relative_path = path.relative_to(resolved_root)
            if any(part in SEMANTIC_EXCLUDED_DIRECTORIES for part in relative_path.parts):
                continue
            if path.stat().st_size <= max_note_bytes:
                files.append(path)
        except (OSError, ValueError):
            continue
    return files


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

    def vault_available(self) -> bool:
        """Return whether the configured vault is an inspectable directory."""
        try:
            if not self.vault_root.is_dir():
                return False
            with os.scandir(self.vault_root):
                return True
        except OSError:
            return False

    def count_notes(self) -> int:
        """Count Markdown files eligible for full semantic synchronization."""
        return len(eligible_markdown_files(self.vault_root, self.max_note_bytes))

    def resolve_path(self, raw: str) -> Path:
        normalized = raw.strip().replace("\\", "/")
        path = Path(normalized)
        if path.is_absolute():
            raise VaultValidationError("Path must be vault-relative")
        try:
            candidate = _resolve_contained_path(self.vault_root / path, self.vault_root)
        except (OSError, ValueError) as exc:
            raise VaultValidationError("Path escapes the vault") from exc
        return candidate

    def existing_relative_path(self, raw: str) -> str | None:
        path = self.resolve_path(raw)
        if not path.exists():
            return None
        return self._relative_path(path).replace("\\", "/")

    def verify_existing_markdown_path(self, raw: str, *, folder: str = "") -> str | None:
        """Return one canonical live Markdown path contained by the vault and optional folder."""
        try:
            normalized = raw.strip().replace("\\", "/")
            posix_path = PurePosixPath(normalized)
            windows_path = PureWindowsPath(normalized)
            if (
                not normalized
                or posix_path.is_absolute()
                or windows_path.is_absolute()
                or windows_path.drive
                or ".." in posix_path.parts
            ):
                return None
            path = self.resolve_path(raw)
            if folder:
                folder_path = self.resolve_path(folder)
                path.relative_to(folder_path)
            if path.suffix.lower() != ".md" or not path.is_file():
                return None
            return self._relative_path(path).replace("\\", "/")
        except (OSError, RuntimeError, ValueError, VaultValidationError):
            return None

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
        for path in contained_markdown_files(self.vault_root, root):
            try:
                if path.stat().st_size > self.max_note_bytes:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
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

        candidates: list[tuple[Path, float]] = []
        for path in contained_markdown_files(self.vault_root, root):
            try:
                candidates.append((path, path.stat().st_mtime))
            except OSError:
                continue

        notes: list[NoteListResult] = []
        for path, modified in sorted(candidates, key=lambda item: item[1], reverse=True):
            notes.append(
                NoteListResult(
                    path=self._relative_path(path),
                    title=path.stem,
                    modified=datetime.fromtimestamp(modified, tz=timezone.utc).isoformat(
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
