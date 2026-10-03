from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from app.services._vault_writes import (
    UnsafeWritePathError,
    coordinated_write,
    open_root_directory,
    open_windows_staged_file,
    windows_pinned_path,
)


def _coordinated(method):
    @wraps(method)
    def write(self, *args, **kwargs):
        if method.__name__ == "create_note" and not self.vault_root.exists():
            # Preserve legacy creation of a missing configured root, after its
            # original folder containment validation, before locking that inode.
            self.resolve_path(kwargs.get("folder") or "Inbox")
            self.vault_root.mkdir(parents=True, exist_ok=True)
        if method.__name__ == "append_note":
            # Preserve the original validation precedence even when no root exists.
            self._ensure_markdown(self.resolve_path(kwargs["path"]))
        try:
            with coordinated_write(self.vault_root):
                return method(self, *args, **kwargs)
        except (FileNotFoundError, NotADirectoryError):
            if method.__name__ == "append_note":
                raise NoteNotFoundError("Note not found") from None
            raise
    return write


class AtomicCaptureError(Exception):
    """Privacy-safe expected capture filesystem failure."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


class PromotionWriteError(Exception):
    """A stable, privacy-safe exact-path promotion failure."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category

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
    files: dict[Path, None] = {}
    for _, resolved_path in _contained_markdown_file_candidates(vault_root, discovery_root):
        files[resolved_path] = None
    return list(files)


def _contained_markdown_file_candidates(
    vault_root: Path,
    discovery_root: Path | None = None,
) -> list[tuple[Path, Path]]:
    """Return discovered Markdown paths paired with their contained resolved targets."""
    vault_root = Path(vault_root)
    discovery_root = vault_root if discovery_root is None else Path(discovery_root)
    try:
        resolved_root = vault_root.resolve()
        resolved_discovery_root = _resolve_contained_path(discovery_root, resolved_root)
    except (OSError, ValueError):
        return []
    if not resolved_discovery_root.exists():
        return []

    files: list[tuple[Path, Path]] = []
    try:
        for discovered_path in resolved_discovery_root.rglob("*.md"):
            try:
                resolved_path = _resolve_contained_path(discovered_path, resolved_root)
                if resolved_path.suffix.lower() == ".md" and resolved_path.is_file():
                    files.append((discovered_path, resolved_path))
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return files


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

    def relative_sort_key(path: Path) -> tuple[str, str]:
        relative_path = path.relative_to(resolved_root).as_posix()
        return relative_path.casefold(), relative_path

    return sorted(files, key=relative_sort_key)


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


@dataclass(frozen=True)
class LiveMarkdownPathCandidate:
    """One exact discovered note spelling and its verified canonical path."""

    discovered_path: str
    canonical_path: str


@dataclass(frozen=True)
class MarkdownPathCandidateSnapshot:
    """One immutable scan of verified live and unsafe Markdown candidates."""

    live_candidates: tuple[LiveMarkdownPathCandidate, ...]
    unsafe_unqualified_names: frozenset[str]


@dataclass(frozen=True)
class MarkdownPathVerification:
    """Privacy-safe outcome for one VaultService-owned Markdown path check."""

    resolution: Literal["resolved", "missing", "unsafe"]
    resolved_path: str | None = None


@dataclass(frozen=True)
class FolderScopeVerification:
    """Privacy-safe outcome for one exact VaultService-owned folder scope check."""

    resolution: Literal["resolved", "missing", "unsafe"]
    resolved_folder: str | None = None


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

    def verify_existing_markdown_path(
        self,
        raw: str,
        *,
        folder: str = "",
        exact_spelling: bool = False,
    ) -> str | None:
        """Return one canonical live Markdown path contained by the vault and optional folder."""
        return self.verify_existing_markdown_path_result(
            raw,
            folder=folder,
            exact_spelling=exact_spelling,
        ).resolved_path

    def verify_existing_markdown_path_result(
        self,
        raw: str,
        *,
        folder: str = "",
        exact_spelling: bool = False,
    ) -> MarkdownPathVerification:
        """Classify one vault-relative Markdown path without leaking host details."""
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
                return MarkdownPathVerification("unsafe")
            path = self.resolve_path(raw)
            if folder:
                folder_path = self.resolve_path(folder)
                path.relative_to(folder_path)
            if self._has_broken_symlink(posix_path.parts):
                return MarkdownPathVerification("unsafe")
            if path.suffix.lower() != ".md" or not path.is_file():
                return MarkdownPathVerification("missing")
            if exact_spelling and not self._has_exact_path_spelling(posix_path.parts):
                return MarkdownPathVerification("missing")
            return MarkdownPathVerification(
                "resolved",
                self._relative_path(path).replace("\\", "/"),
            )
        except (OSError, RuntimeError, ValueError, VaultValidationError):
            return MarkdownPathVerification("unsafe")

    def verify_source_relative_markdown_path(
        self,
        source_path: str,
        target_path: str,
    ) -> str | None:
        """Verify an exact Markdown target relative to one canonical source note."""
        return self.verify_source_relative_markdown_path_result(
            source_path,
            target_path,
        ).resolved_path

    def verify_source_relative_markdown_path_result(
        self,
        source_path: str,
        target_path: str,
    ) -> MarkdownPathVerification:
        """Classify one source-relative Markdown path without leaking host details."""
        try:
            source_result = self.verify_existing_markdown_path_result(
                source_path,
                exact_spelling=True,
            )
            source = source_result.resolved_path
            if source is None:
                return MarkdownPathVerification(source_result.resolution)

            normalized_target = target_path.replace("\\", "/")
            target = PurePosixPath(normalized_target)
            windows_target = PureWindowsPath(target_path)
            if (
                not normalized_target
                or target.is_absolute()
                or windows_target.is_absolute()
                or windows_target.drive
            ):
                return MarkdownPathVerification("unsafe")

            source_directory = PurePosixPath(source).parent
            written_parts = source_directory.parts + target.parts
            candidate = "/".join(written_parts)
            resolved = self.resolve_path(candidate)
            if self._has_broken_symlink(written_parts):
                return MarkdownPathVerification("unsafe")
            if resolved.suffix.lower() != ".md" or not resolved.is_file():
                return MarkdownPathVerification("missing")
            if not self._has_exact_relative_path_spelling(written_parts):
                return MarkdownPathVerification("missing")
            return MarkdownPathVerification(
                "resolved",
                self._relative_path(resolved).replace("\\", "/"),
            )
        except (OSError, RuntimeError, ValueError, VaultValidationError):
            return MarkdownPathVerification("unsafe")

    def verify_existing_folder_scope_result(self, raw: str) -> FolderScopeVerification:
        """Classify one exact vault-relative directory without leaking host details."""
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
                return FolderScopeVerification("unsafe")
            if normalized.rstrip("/") == ".":
                return FolderScopeVerification("resolved", ".")
            path = self.resolve_path(normalized)
            if self._has_broken_symlink(posix_path.parts):
                return FolderScopeVerification("unsafe")
            if not path.exists() or not path.is_dir():
                return FolderScopeVerification("missing")
            if not self._has_exact_path_spelling(posix_path.parts):
                return FolderScopeVerification("missing")
            return FolderScopeVerification(
                "resolved",
                self._relative_path(path).replace("\\", "/"),
            )
        except (OSError, RuntimeError, ValueError, VaultValidationError):
            return FolderScopeVerification("unsafe")

    def _has_broken_symlink(self, parts: tuple[str, ...]) -> bool:
        current = self.vault_root
        for part in parts:
            if part == ".":
                continue
            if part == "..":
                current = current.parent
                continue
            current /= part
            try:
                if current.is_symlink():
                    current.resolve(strict=True)
            except (OSError, RuntimeError):
                return True
        return False

    def _has_exact_path_spelling(self, parts: tuple[str, ...]) -> bool:
        current = self.vault_root
        for part in parts:
            try:
                with os.scandir(current) as entries:
                    if not any(entry.name == part for entry in entries):
                        return False
            except OSError:
                return False
            current /= part
        return True

    def _has_exact_relative_path_spelling(self, parts: tuple[str, ...]) -> bool:
        current = self.vault_root
        for part in parts:
            if part == ".":
                continue
            if part == "..":
                current = current.parent
                continue
            try:
                with os.scandir(current) as entries:
                    if not any(entry.name == part for entry in entries):
                        return False
                current = (current / part).resolve()
            except OSError:
                return False
        return True

    def live_markdown_paths(self, *, folder: str = "") -> list[str]:
        """Return deterministic canonical paths for live user-note Markdown files."""
        root = self.resolve_path(folder) if folder else self.vault_root
        if not root.is_dir():
            return []

        paths: list[str] = []
        for path in contained_markdown_files(self.vault_root, root):
            try:
                relative_path = path.relative_to(self.vault_root)
            except ValueError:
                continue
            if any(part in SEMANTIC_EXCLUDED_DIRECTORIES for part in relative_path.parts):
                continue
            paths.append(relative_path.as_posix())
        return sorted(paths, key=lambda path: (path.casefold(), path))

    def live_markdown_path_candidates(self) -> list[LiveMarkdownPathCandidate]:
        """Return exact discovered note spellings paired with verified canonical paths."""
        return list(self.markdown_path_candidate_snapshot().live_candidates)

    def markdown_path_candidate_snapshot(self) -> MarkdownPathCandidateSnapshot:
        """Scan once for live candidates and unsafe filenames under the vault."""
        candidates: list[LiveMarkdownPathCandidate] = []
        unsafe_names: set[str] = set()
        try:
            for discovered_path in self.vault_root.rglob("*.md"):
                try:
                    relative_path = discovered_path.relative_to(
                        self.vault_root
                    ).as_posix()
                except ValueError:
                    continue
                relative_parts = PurePosixPath(relative_path).parts
                if any(
                    part in SEMANTIC_EXCLUDED_DIRECTORIES for part in relative_parts
                ):
                    continue
                verification = self.verify_existing_markdown_path_result(
                    relative_path,
                    exact_spelling=True,
                )
                canonical_path = verification.resolved_path
                if canonical_path is not None:
                    if any(
                        part in SEMANTIC_EXCLUDED_DIRECTORIES
                        for part in PurePosixPath(canonical_path).parts
                    ):
                        continue
                    candidates.append(
                        LiveMarkdownPathCandidate(
                            discovered_path=relative_path,
                            canonical_path=canonical_path,
                        )
                    )
                elif verification.resolution == "unsafe":
                    unsafe_names.add(PurePosixPath(relative_path).name)
        except OSError:
            pass
        return MarkdownPathCandidateSnapshot(
            live_candidates=tuple(
                sorted(
                    candidates,
                    key=lambda candidate: (
                        candidate.discovered_path.casefold(),
                        candidate.discovered_path,
                    ),
                )
            ),
            unsafe_unqualified_names=frozenset(unsafe_names),
        )

    @_coordinated
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

    @_coordinated
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

    @contextmanager
    def _capture_directory(self):
        """Pin exact, non-symlink directories; POSIX operations stay descriptor-relative."""
        with ExitStack() as stack:
            path = self.vault_root
            directory_fd = None
            if os.name == "nt":
                # Pin ancestors too: denying deletion of only the leaf is insufficient.
                for ancestor in reversed((path, *path.parents)):
                    stack.enter_context(windows_pinned_path(ancestor))
            else:
                directory_fd = open_root_directory(path)
                stack.callback(os.close, directory_fd)
            pinned = [(path, directory_fd)]
            for name in ("Inbox", "Captures"):
                entries = os.listdir(directory_fd if directory_fd is not None else path)
                if any(entry.casefold() == name.casefold() and entry != name for entry in entries):
                    raise AtomicCaptureError("unsafe_destination")
                if name not in entries:
                    try:
                        if directory_fd is None:
                            (path / name).mkdir()
                        else:
                            os.mkdir(name, dir_fd=directory_fd)
                    except FileExistsError:
                        pass
                path /= name
                if directory_fd is None:
                    stack.enter_context(windows_pinned_path(path))
                    if not path.is_dir():
                        raise AtomicCaptureError("unsafe_destination")
                else:
                    directory_fd = os.open(
                        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                    stack.callback(os.close, directory_fd)
                pinned.append((path, directory_fd))

            def verify():
                for pinned_path, fd in pinned:
                    if fd is not None:
                        info = pinned_path.lstat()
                        opened = os.fstat(fd)
                        if not stat.S_ISDIR(info.st_mode) or (
                            info.st_dev, info.st_ino
                        ) != (opened.st_dev, opened.st_ino):
                            raise AtomicCaptureError("unsafe_destination")
            verify()
            yield path, directory_fd, verify

    def create_capture_if_absent(self, *, capture_id: str, markdown: bytes) -> NoteWriteResult:
        """Commit complete capture bytes with an atomic, non-replacing hard link.

        All VaultService create/append callers share the same local OS lock. A
        destination is never opened for writing. Temporary files have no .md suffix.
        Unsupported hard-link filesystems fail closed instead of falling back.
        """
        if not isinstance(capture_id, str) or not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", capture_id
        ):
            raise AtomicCaptureError("invalid_request")
        if not isinstance(markdown, bytes):
            raise AtomicCaptureError("invalid_request")
        if len(markdown) > self.max_note_bytes:
            raise AtomicCaptureError("size_limit")
        try:
            markdown.decode("utf-8")
        except UnicodeError:
            raise AtomicCaptureError("invalid_request") from None
        name = capture_id + ".md"
        canonical = "Inbox/Captures/" + name
        commit_attempted = False
        try:
            with coordinated_write(self.vault_root), self._capture_directory() as (parent, fd, verify):
                def read_existing():
                    verify()
                    with ExitStack() as stack:
                        if fd is None:
                            stack.enter_context(windows_pinned_path(parent / name))
                            if not (parent / name).is_file():
                                raise AtomicCaptureError("unsafe_destination")
                            read_fd = os.open(parent / name, os.O_RDONLY | os.O_BINARY)
                        else:
                            read_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                        stream = stack.enter_context(os.fdopen(read_fd, "rb"))
                        info = os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode):
                            raise AtomicCaptureError("unsafe_destination")
                        existing = stream.read(self.max_note_bytes + 1)
                    verify()
                    if len(existing) > self.max_note_bytes:
                        raise AtomicCaptureError("size_limit")
                    existing.decode("utf-8")
                    if existing != markdown:
                        raise AtomicCaptureError("conflict")
                    return NoteWriteResult(canonical, "already_applied")

                def verify_committed(staged_identity):
                    """Confirm the canonical entry still names our complete staged bytes."""
                    verify()
                    with ExitStack() as stack:
                        if fd is None:
                            stack.enter_context(windows_pinned_path(parent / name))
                            read_fd = os.open(parent / name, os.O_RDONLY | os.O_BINARY)
                        else:
                            read_fd = os.open(
                                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
                            )
                        stream = stack.enter_context(os.fdopen(read_fd, "rb"))
                        info = os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode) or (
                            info.st_dev, info.st_ino
                        ) != (staged_identity.st_dev, staged_identity.st_ino):
                            raise AtomicCaptureError("commit_unknown")
                        if stream.read(len(markdown) + 1) != markdown:
                            raise AtomicCaptureError("commit_unknown")
                    verify()

                entries = os.listdir(fd if fd is not None else parent)
                if any(entry.casefold() == name and entry != name for entry in entries):
                    raise AtomicCaptureError("unsafe_destination")
                if name in entries:
                    return read_existing()
                temporary = ".vaultbridge-" + uuid.uuid4().hex + ".tmp"
                temporary_path = parent / temporary
                write_fd = (
                    open_windows_staged_file(temporary_path) if fd is None
                    else os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                )
                staged_identity = os.fstat(write_fd)
                try:
                    with os.fdopen(write_fd, "wb") as stream:
                        stream.write(markdown)
                        stream.flush()
                        os.fsync(stream.fileno())
                        verify()
                        commit_attempted = True
                        try:
                            if fd is None:
                                # The staging handle denies delete/rename and external
                                # writes until this link operation has completed.
                                os.link(temporary_path, parent / name)
                            else:
                                # linkat follows only our live descriptor alias, never
                                # the replaceable directory entry used for staging.
                                os.link(
                                    f"/proc/self/fd/{stream.fileno()}", name,
                                    dst_dir_fd=fd, follow_symlinks=True,
                                )
                        except FileExistsError:
                            commit_attempted = False
                            return read_existing()
                        verify()
                        if fd is not None:
                            os.fsync(fd)
                        verify_committed(staged_identity)
                        return NoteWriteResult(canonical, "created")
                finally:
                    with suppress(OSError):
                        if fd is None:
                            temporary_path.unlink()
                        else:
                            remaining = os.stat(temporary, dir_fd=fd, follow_symlinks=False)
                            if (remaining.st_dev, remaining.st_ino) == (
                                staged_identity.st_dev, staged_identity.st_ino
                            ):
                                os.unlink(temporary, dir_fd=fd)
        except AtomicCaptureError:
            if commit_attempted:
                raise AtomicCaptureError("commit_unknown") from None
            raise
        except (UnsafeWritePathError, UnicodeError):
            raise AtomicCaptureError("commit_unknown" if commit_attempted else "unsafe_destination") from None
        except OSError as exc:
            import errno

            category = "commit_unknown" if commit_attempted else "write_unavailable"
            if not commit_attempted and exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR):
                category = "unsafe_destination"
            raise AtomicCaptureError(category) from None

    @contextmanager
    def _promotion_parent(self, raw: str, *, missing_ok: bool = False):
        """Pin one exactly spelled, existing path without following symlinks."""
        if type(raw) is not str or not raw or "\\" in raw or raw != raw.strip():
            raise PromotionWriteError("unsafe_destination")
        parts = PurePosixPath(raw).parts
        if (not parts or any(part in (".", "..", "") for part in parts)
                or raw != "/".join(parts) or PureWindowsPath(raw).drive
                or not raw.endswith(".md") or raw.startswith("/")):
            raise PromotionWriteError("unsafe_destination")
        with ExitStack() as stack:
            parent = self.vault_root
            if os.name == "nt":
                for ancestor in reversed((parent, *parent.parents)):
                    stack.enter_context(windows_pinned_path(ancestor))
                fd = None
            else:
                fd = open_root_directory(parent)
                stack.callback(os.close, fd)
            pinned = [(parent, fd)]
            for component in parts[:-1]:
                entries = os.listdir(fd if fd is not None else parent)
                if component not in entries:
                    category = "unsafe_destination" if any(
                        entry.casefold() == component.casefold() for entry in entries
                    ) else "destination_missing"
                    raise PromotionWriteError(category)
                parent /= component
                if fd is None:
                    stack.enter_context(windows_pinned_path(parent))
                    if not parent.is_dir():
                        raise PromotionWriteError("unsafe_destination")
                else:
                    fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    stack.callback(os.close, fd)
                pinned.append((parent, fd))

            name = parts[-1]

            def verify():
                for path, opened_fd in pinned:
                    if opened_fd is not None:
                        current, opened = path.lstat(), os.fstat(opened_fd)
                        if not stat.S_ISDIR(current.st_mode) or (
                            current.st_dev, current.st_ino
                        ) != (opened.st_dev, opened.st_ino):
                            raise PromotionWriteError("unsafe_destination")

            def entries():
                verify()
                names = os.listdir(fd if fd is not None else parent)
                if any(entry.casefold() == name.casefold() and entry != name for entry in names):
                    raise PromotionWriteError("unsafe_destination")
                return name in names

            if not missing_ok and not entries():
                raise PromotionWriteError("destination_missing")
            verify()
            yield parent, fd, name, entries, verify

    def _promotion_read_open(self, parent, fd, name, verify):
        with ExitStack() as stack:
            if fd is None:
                stack.enter_context(windows_pinned_path(parent / name))
                opened = os.open(parent / name, os.O_RDONLY | os.O_BINARY)
            else:
                opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            stream = stack.enter_context(os.fdopen(opened, "rb"))
            identity = os.fstat(stream.fileno())
            if not stat.S_ISREG(identity.st_mode):
                raise PromotionWriteError("unsafe_destination")
            data = stream.read(self.max_note_bytes + 1)
        verify()
        if len(data) > self.max_note_bytes:
            raise PromotionWriteError("size_limit")
        try:
            data.decode("utf-8")
        except UnicodeError:
            raise PromotionWriteError("unsafe_destination") from None
        return data, identity

    def _promotion_recovery_bytes(
        self, path: str, *, parent_identity: tuple[int, int],
        target_identity: tuple[int, int],
    ) -> bytes | None:
        """Recheck only the canonical entry after a possible write, without retrying it."""
        try:
            with coordinated_write(self.vault_root), self._promotion_parent(path) as (
                parent, fd, name, entries, verify,
            ):
                current_parent = os.fstat(fd) if fd is not None else parent.stat()
                if (current_parent.st_dev, current_parent.st_ino) != parent_identity or not entries():
                    return None
                data, current = self._promotion_read_open(parent, fd, name, verify)
                if (current.st_dev, current.st_ino) != target_identity:
                    return None
                return data
        except (PromotionWriteError, OSError, UnsafeWritePathError, ValueError):
            return None

    def read_promotion_bytes(self, path: str, *, source: bool = False) -> bytes:
        """Bounded exact-path read; source errors never expose the path."""
        try:
            with self._promotion_parent(path) as (parent, fd, name, entries, verify):
                if not entries():
                    raise PromotionWriteError("destination_missing")
                data, _ = self._promotion_read_open(parent, fd, name, verify)
                return data
        except PromotionWriteError:
            if source:
                raise PromotionWriteError("unsafe_source") from None
            raise
        except (OSError, UnsafeWritePathError, ValueError):
            raise PromotionWriteError("unsafe_source" if source else "unsafe_destination") from None

    def create_promotion_if_absent(self, *, path: str, markdown: bytes, check_source) -> str:
        """Atomically link complete bytes into one absent exact destination."""
        if type(markdown) is not bytes or len(markdown) > self.max_note_bytes:
            raise PromotionWriteError("size_limit" if type(markdown) is bytes else "invalid_request")
        try:
            markdown.decode("utf-8")
        except UnicodeError:
            raise PromotionWriteError("invalid_request") from None
        attempted = False
        parent_identity = None
        target_identity = None
        try:
            with coordinated_write(self.vault_root), self._promotion_parent(path, missing_ok=True) as (
                parent, fd, name, entries, verify,
            ):
                check_source()
                parent_info = os.fstat(fd) if fd is not None else parent.stat()
                parent_identity = (parent_info.st_dev, parent_info.st_ino)
                if entries():
                    existing, _ = self._promotion_read_open(parent, fd, name, verify)
                    return "already_applied" if existing == markdown else "conflict"
                temporary = ".vaultbridge-" + uuid.uuid4().hex + ".tmp"
                temporary_path = parent / temporary
                opened = (open_windows_staged_file(temporary_path) if fd is None else os.open(
                    temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
                ))
                identity = os.fstat(opened)
                target_identity = (identity.st_dev, identity.st_ino)
                try:
                    with os.fdopen(opened, "wb") as stream:
                        stream.write(markdown)
                        stream.flush()
                        os.fsync(stream.fileno())
                        verify()
                        attempted = True
                        try:
                            if fd is None:
                                os.link(temporary_path, parent / name)
                            else:
                                os.link(f"/proc/self/fd/{stream.fileno()}", name,
                                        dst_dir_fd=fd, follow_symlinks=True)
                        except FileExistsError:
                            attempted = False
                            existing, _ = self._promotion_read_open(parent, fd, name, verify)
                            return "already_applied" if existing == markdown else "conflict"
                        verify()
                        if fd is not None:
                            os.fsync(fd)
                        existing, current = self._promotion_read_open(parent, fd, name, verify)
                        if existing != markdown or (current.st_dev, current.st_ino) != (
                            identity.st_dev, identity.st_ino
                        ):
                            raise PromotionWriteError("commit_unknown")
                        return "created"
                finally:
                    with suppress(OSError):
                        if fd is None:
                            temporary_path.unlink()
                        else:
                            remaining = os.stat(temporary, dir_fd=fd, follow_symlinks=False)
                            if (remaining.st_dev, remaining.st_ino) == (identity.st_dev, identity.st_ino):
                                os.unlink(temporary, dir_fd=fd)
        except PromotionWriteError:
            if attempted:
                recovered = self._promotion_recovery_bytes(
                    path, parent_identity=parent_identity, target_identity=target_identity,
                )
                if recovered == markdown:
                    return "already_applied"
                raise PromotionWriteError("commit_unknown") from None
            raise
        except (OSError, UnsafeWritePathError) as exc:
            if attempted:
                recovered = self._promotion_recovery_bytes(
                    path, parent_identity=parent_identity, target_identity=target_identity,
                )
                if recovered == markdown:
                    return "already_applied"
            category = "commit_unknown" if attempted else "write_unavailable"
            if not attempted and (isinstance(exc, UnsafeWritePathError)
                                  or getattr(exc, "errno", None) in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR)):
                category = "unsafe_destination"
            raise PromotionWriteError(category) from None

    def append_promotion_once(self, *, path: str, preimage_sha256: str, block: bytes,
                              marker_prefixes: tuple[bytes, bytes], check_source,
                              check_destination) -> str:
        """Append under the shared writer lock after exact-preimage and marker checks."""
        if type(block) is not bytes or len(block) > self.max_note_bytes:
            raise PromotionWriteError("size_limit" if type(block) is bytes else "invalid_request")
        attempted = False
        parent_identity = None
        target_identity = None
        try:
            with coordinated_write(self.vault_root), self._promotion_parent(path) as (
                parent, fd, name, entries, verify,
            ):
                check_source()
                parent_info = os.fstat(fd) if fd is not None else parent.stat()
                parent_identity = (parent_info.st_dev, parent_info.st_ino)
                if not entries():
                    raise PromotionWriteError("destination_missing")
                existing, identity = self._promotion_read_open(parent, fd, name, verify)
                target_identity = (identity.st_dev, identity.st_ino)
                check_destination(existing)
                opening, closing = marker_prefixes
                openings, closings = existing.count(opening), existing.count(closing)
                if openings or closings:
                    if openings == closings == 1 and existing.count(block) == 1:
                        return "already_applied"
                    raise PromotionWriteError("conflict")
                if hashlib.sha256(existing).hexdigest() != preimage_sha256:
                    raise PromotionWriteError("destination_changed")
                if len(existing) + len(block) > self.max_note_bytes:
                    raise PromotionWriteError("size_limit")
                with ExitStack() as stack:
                    if fd is None:
                        stack.enter_context(windows_pinned_path(parent / name))
                        opened = os.open(parent / name, os.O_WRONLY | os.O_APPEND | os.O_BINARY)
                    else:
                        opened = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, dir_fd=fd)
                    stream = stack.enter_context(os.fdopen(opened, "ab"))
                    current = os.fstat(stream.fileno())
                    if (current.st_dev, current.st_ino, current.st_size) != (
                        identity.st_dev, identity.st_ino, len(existing)
                    ):
                        raise PromotionWriteError("destination_changed")
                    verify()
                    attempted = True
                    stream.write(block)
                    stream.flush()
                    os.fsync(stream.fileno())
                verify()
                actual, current = self._promotion_read_open(parent, fd, name, verify)
                if actual != existing + block or (current.st_dev, current.st_ino) != (
                    identity.st_dev, identity.st_ino
                ):
                    raise PromotionWriteError("commit_unknown")
                return "appended"
        except PromotionWriteError:
            if attempted:
                recovered = self._promotion_recovery_bytes(
                    path, parent_identity=parent_identity, target_identity=target_identity,
                )
                if self._promotion_block_proved(recovered, block, marker_prefixes, check_destination):
                    return "already_applied"
                raise PromotionWriteError("commit_unknown") from None
            raise
        except (OSError, UnsafeWritePathError) as exc:
            if attempted:
                recovered = self._promotion_recovery_bytes(
                    path, parent_identity=parent_identity, target_identity=target_identity,
                )
                if self._promotion_block_proved(recovered, block, marker_prefixes, check_destination):
                    return "already_applied"
            category = "commit_unknown" if attempted else "write_unavailable"
            if not attempted and (isinstance(exc, UnsafeWritePathError)
                                  or getattr(exc, "errno", None) in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR)):
                category = "unsafe_destination"
            elif not attempted and isinstance(exc, FileNotFoundError):
                category = "destination_missing"
            raise PromotionWriteError(category) from None

    @staticmethod
    def _promotion_block_proved(data, block, marker_prefixes, check_destination) -> bool:
        if data is None:
            return False
        try:
            check_destination(data)
        except PromotionWriteError:
            return False
        opening, closing = marker_prefixes
        return data.count(opening) == data.count(closing) == data.count(block) == 1

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
