"""Vault-owned accounted filesystem path. No legacy discovery/read delegation."""

import heapq
import os
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from app.services._vault_writes import UnsafeWritePathError, windows_pinned_path
from app.services.scoped_budget import BudgetExhausted
from app.services.vault import (
    SEMANTIC_EXCLUDED_DIRECTORIES,
    NoteReadResult,
    NoteUnavailableError,
    _markdown_discovery_name,
)


def key(path):
    return path.casefold(), path


def validate_path(raw, *, folder=False):
    if (type(raw) is not str or not raw or raw != raw.strip()
            or len(raw.encode("utf-8")) > 1024 or "\x00" in raw):
        raise ValueError("unsafe scope")
    normalized = raw.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if (PureWindowsPath(raw).drive or normalized.startswith("/")
            or ".." in parts):
        raise ValueError("unsafe scope")
    return PurePosixPath(normalized).as_posix()


@dataclass(frozen=True, slots=True)
class ScopedPathFact:
    path: str
    identity: tuple
    modified_ns: int
    size: int


@dataclass(frozen=True, slots=True)
class ScopedVaultSnapshot:
    facts: tuple[ScopedPathFact, ...]
    spellings: dict[str, str] = field(repr=False)
    unsafe_names: frozenset[str]
    directory_aliases: dict[str, str] = field(repr=False)
    reasons: tuple[str, ...]
    alias_identities: dict[str, tuple] = field(default_factory=dict, repr=False)

    @property
    def resolution_complete(self):
        return not set(self.reasons) & {"discovery_limit", "path_limit", "alias_limit", "note_unavailable"}


class _Reverse:
    def __init__(self, path):
        self.path = path

    def __lt__(self, other):
        return key(self.path) > key(other.path)


class ScopedVaultSession:
    def __init__(self, vault, sid, budget, cancel, root_identity):
        self.vault, self.sid, self.budget, self.cancel = vault, sid, budget, cancel
        self.root = vault.vault_root
        self.root_identity = root_identity
        self.resolution_snapshot = None
        self._root_stack = None
        self._root_handle = None

    def __enter__(self):
        self._root_stack = ExitStack()
        try:
            self._root_handle = self._root_stack.enter_context(self.pinned())
        except BaseException:
            self._root_stack.close()
            self._root_stack = None
            raise
        return self

    def __exit__(self, *args):
        if self._root_stack is not None:
            self._root_stack.close()
        self._root_stack = self._root_handle = None

    def probe(self):
        self.budget.charge(self.sid, "probes")

    def call(self, function, *args, **kwargs):
        self.probe()
        return function(*args, **kwargs)

    @staticmethod
    def identity(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    @contextmanager
    def pinned(self, relative="", *, file=False, fresh_root=False):
        """Pin every component, including ancestors above the configured root."""
        self.cancel.check()
        if len(PurePosixPath(relative).parts) > 64 + int(file):
            raise BudgetExhausted("directories")
        if self._root_handle is not None and not relative and not fresh_root:
            info = (self.call(os.stat, self._root_handle) if os.name == "nt" else
                    self.call(os.fstat, self._root_handle))
            self._check_root(info)
            yield self._root_handle
            return
        target = self.root / relative
        reuse_root = self._root_handle is not None and bool(PurePosixPath(relative).parts)
        if reuse_root:
            parts = PurePosixPath(relative).parts
            chain = tuple(self.root.joinpath(*parts[:index]) for index in range(1, len(parts) + 1))
        else:
            chain = tuple(reversed(target.parents)) + (target,)
        with ExitStack() as stack:
            if os.name == "nt":
                for path in chain:
                    if path != target or not file:
                        self.budget.charge(self.sid, "directories")
                    stack.enter_context(windows_pinned_path(path, probe=self.probe))
                    info = self.call(os.lstat, path)
                    if path == self.root:
                        self._check_root(info)
                yield target
            else:
                descriptor = self._root_handle if reuse_root else None
                for index, path in enumerate(chain):
                    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                    if index != len(chain) - 1 or not file:
                        flags |= os.O_DIRECTORY
                        self.budget.charge(self.sid, "directories")
                    descriptor = self.call(os.open, path if index == 0 and not reuse_root else path.name,
                                           flags, dir_fd=descriptor)
                    stack.callback(os.close, descriptor)
                    info = self.call(os.fstat, descriptor)
                    if path == self.root:
                        self._check_root(info)
                yield descriptor

    def _check_root(self, info):
        if not stat.S_ISDIR(info.st_mode) or (
                self.root_identity is not None
                and (info.st_dev, info.st_ino) != self.root_identity):
            raise NoteUnavailableError("root_unavailable")

    def verify_root(self):
        with self.pinned(fresh_root=True):
            pass

    def resolve(self, relative):
        """Bounded explicit component resolver; no Path.resolve/stat hidden work."""
        todo = list(PurePosixPath(relative).parts)
        done = []
        info = None
        hops = 0
        alias_changed = False
        while todo:
            self.cancel.check()
            part = todo.pop(0)
            if part == "..":
                if not done:
                    raise UnsafeWritePathError("unsafe_path")
                done.pop()
                info = None
                continue
            if part in {"", "."}:
                continue
            with self.pinned("/".join(done)) as parent:
                path = self.root.joinpath(*done, part)
                info = (self.call(os.lstat, path) if os.name == "nt" else
                        self.call(os.stat, part, dir_fd=parent, follow_symlinks=False))
                if os.name == "nt":
                    # Native Windows lookup accepts other case spellings; the scoped
                    # identity contract requires exact observed components. Stream
                    # that proof under the same entry/open/probe budgets.
                    self.budget.charge(self.sid, "directories")
                    self.probe()
                    with os.scandir(self.root.joinpath(*done)) as entries:
                        while True:
                            self.cancel.check()
                            self.budget.charge(self.sid, "entries")
                            try:
                                entry = next(entries)
                            except StopIteration:
                                raise FileNotFoundError() from None
                            if entry.name == part:
                                break
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    if self.resolution_snapshot is not None:
                        spelling = "/".join((*done, part))
                        expected = self.resolution_snapshot.alias_identities.get(spelling)
                        if expected != self.identity(info):
                            alias_changed = True
                    hops += 1
                    if hops > 40:
                        raise UnsafeWritePathError("unsafe_path")
                    destination = (self.call(os.readlink, path) if os.name == "nt" else
                                   self.call(os.readlink, part, dir_fd=parent))
                    # Native Windows readlink uses the extended prefix for junctions.
                    # This is OS-produced private input, never caller path syntax.
                    if os.name == "nt" and destination.startswith("\\\\?\\UNC\\"):
                        destination = "\\\\" + destination[8:]
                    elif os.name == "nt" and destination.startswith("\\\\?\\"):
                        destination = destination[4:]
                    link = Path(destination)
                    if link.is_absolute():
                        try:
                            destination = link.relative_to(self.root).as_posix()
                        except ValueError:
                            raise UnsafeWritePathError("unsafe_path") from None
                        done = []
                    todo = list(PurePosixPath(destination.replace("\\", "/")).parts) + todo
                    info = None
                    continue
                done.append(part)
        if info is None:
            # Terminal '.'/'..' expansion has no final component lookup. Pin
            # the canonical directory and charge its metadata refresh once.
            with self.pinned("/".join(done)) as handle:
                info = self.call(os.stat, handle) if os.name == "nt" else self.call(os.fstat, handle)
        if alias_changed:
            raise NoteUnavailableError("note_unavailable")
        return "/".join(done), info

    def discover(self):
        facts, spellings, aliases, unsafe = {}, {}, {}, set()
        alias_identities = {}
        heap = []
        reasons = set()
        quota = self.budget.allocations[self.sid]["paths"]

        def retain(path, info, spelling):
            try:
                encoded_length = len(path.encode("utf-8"))
            except UnicodeError:
                reasons.add("note_unavailable")
                return
            if encoded_length > 1024:
                reasons.add("path_limit")
                return
            if path not in facts:
                if len(facts) == quota:
                    reasons.add("path_limit")
                    if not heap or key(path) >= key(heap[0].path):
                        return
                    removed = heapq.heappop(heap).path
                    del facts[removed]
                    for name in tuple(spellings):
                        if spellings[name] == removed:
                            del spellings[name]
                else:
                    self.budget.charge(self.sid, "paths")
                heapq.heappush(heap, _Reverse(path))
                facts[path] = ScopedPathFact(path, self.identity(info), info.st_mtime_ns, info.st_size)
                spellings[path] = path
            if spelling != path and spelling not in spellings:
                if spelling not in alias_identities:
                    reasons.add("alias_limit")
                else:
                    spellings[spelling] = path

        def walk(relative, depth):
            self.cancel.check()
            if depth > 64:
                raise BudgetExhausted("directories")
            self.budget.charge(self.sid, "directories")
            if not relative and self._root_handle is not None:
                from contextlib import nullcontext

                directory_context = nullcontext(self._root_handle)
            else:
                directory_context = self.pinned(relative)
            with directory_context as directory:
                # scandir's descriptor open is a primitive, as well as a directory open.
                self.probe()
                with os.scandir(directory) as entries:
                    while True:
                        self.cancel.check()
                        self.budget.charge(self.sid, "entries")
                        try:
                            entry = next(entries)
                        except StopIteration:
                            break
                        name = entry.name
                        spelling = f"{relative}/{name}" if relative else name
                        # Windows DirEntry caches omit file IDs; use lstat for identity.
                        info = (self.call(os.lstat, self.root / spelling) if os.name == "nt" else
                                self.call(entry.stat, follow_symlinks=False))
                        if name in SEMANTIC_EXCLUDED_DIRECTORIES:
                            continue
                        if stat.S_ISDIR(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400:
                            walk(spelling, depth + 1)
                            continue
                        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                            alias_identity = self.identity(info)
                            try:
                                canonical, target_info = self.resolve(spelling)
                            except (OSError, UnsafeWritePathError):
                                if _markdown_discovery_name(name):
                                    try:
                                        self.budget.charge(self.sid, "aliases")
                                    except BudgetExhausted:
                                        reasons.add("alias_limit")
                                    else:
                                        unsafe.add(name)
                                continue
                            if stat.S_ISDIR(target_info.st_mode):
                                try:
                                    self.budget.charge(self.sid, "directory_aliases")
                                except BudgetExhausted:
                                    reasons.add("alias_limit")
                                else:
                                    aliases[spelling] = canonical
                                    alias_identities[spelling] = alias_identity
                                continue
                            try:
                                self.budget.charge(self.sid, "aliases")
                            except BudgetExhausted:
                                reasons.add("alias_limit")
                            else:
                                alias_identities[spelling] = alias_identity
                            info = target_info
                        else:
                            canonical = spelling
                        if (_markdown_discovery_name(name) and stat.S_ISREG(info.st_mode)
                                and PurePosixPath(canonical).suffix.lower() == ".md"
                                and not set(PurePosixPath(canonical).parts) & SEMANTIC_EXCLUDED_DIRECTORIES):
                            retain(canonical, info, spelling)
        try:
            walk("", 0)
        except BudgetExhausted:
            return ScopedVaultSnapshot((), {}, frozenset(), {}, ("discovery_limit",))
        except (OSError, UnsafeWritePathError, NoteUnavailableError):
            return ScopedVaultSnapshot((), {}, frozenset(), {}, ("discovery_limit",))
        return ScopedVaultSnapshot(tuple(facts[path] for path in sorted(facts, key=key)),
                                   spellings, frozenset(unsafe), aliases, tuple(sorted(reasons)), alias_identities)

    def verify(self, fact):
        with self.pinned(fact.path, file=True) as handle:
            info = self.call(os.stat, handle) if os.name == "nt" else self.call(os.fstat, handle)
            if not stat.S_ISREG(info.st_mode) or self.identity(info) != fact.identity:
                raise NoteUnavailableError("note_unavailable")
            return info

    def read(self, fact, *, incoming=False):
        self.cancel.check()
        remaining = self.budget.remaining(self.sid, "bytes")
        if remaining == 0:
            raise BudgetExhausted("bytes")
        self.budget.admit_read(self.sid, fact.path, incoming=incoming)
        with self.pinned(fact.path, file=True) as handle:
            if os.name == "nt":
                fd = self.call(os.open, handle, os.O_RDONLY | os.O_BINARY)
            else:
                fd = self.call(os.dup, handle)
            # BufferedReader may read ahead past the byte admission. FileIO issues
            # exactly the bounded read and returns its actual transferred bytes.
            with os.fdopen(fd, "rb", buffering=0) as stream:
                before = self.call(os.fstat, stream.fileno())
                if not stat.S_ISREG(before.st_mode) or self.identity(before) != fact.identity:
                    raise NoteUnavailableError("note_unavailable")
                if before.st_size > self.vault.max_note_bytes:
                    raise NoteUnavailableError("note_unavailable")
                data = stream.read(min(before.st_size + 1, remaining))
                self.budget.charge(self.sid, "bytes", len(data))
                after = self.call(os.fstat, stream.fileno())
        if before.st_size >= remaining and len(data) == remaining:
            raise BudgetExhausted("bytes")
        if (len(data) > self.vault.max_note_bytes or len(data) != before.st_size
                or self.identity(after) != fact.identity):
            raise NoteUnavailableError("note_unavailable")
        self.verify(fact)
        return NoteReadResult(fact.path, data.decode("utf-8"))
