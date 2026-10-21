"""Private namespace binding; never a scoring signature or authorization grant."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path


class IndexBindingUnavailableError(RuntimeError):
    def __init__(self):
        super().__init__("semantic_unavailable")


BINDING_KEYS = (
    "knowledge_space_binding_version",
    "knowledge_space_id",
    "knowledge_space_root_sha256",
)


def ordinary_identity(path: Path, *, directory=False):
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        or (not directory and info.st_nlink != 1)
    ):
        raise IndexBindingUnavailableError
    return info.st_dev, info.st_ino


@dataclass(frozen=True, slots=True, repr=False)
class ExpectedIndexBinding:
    """Issued internally from an already validated non-default definition."""

    space_id: str
    _root: Path = field(repr=False)
    _root_directories: tuple = field(init=False, repr=False)
    root_sha256: str = field(init=False, repr=False)

    def __post_init__(self):
        if (
            not isinstance(self.space_id, str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.space_id)
            or self.space_id == "default"
        ):
            raise ValueError("expected non-default space")
        root = self._root.resolve(strict=True)
        object.__setattr__(self, "_root", root)
        object.__setattr__(self, "_root_directories", tuple(
            (parent, ordinary_identity(parent, directory=True))
            for parent in reversed((root, *root.parents))
        ))
        object.__setattr__(self, "root_sha256", hashlib.sha256(
            os.path.normcase(str(root)).encode("utf-8")
        ).hexdigest())

    @property
    def values(self):
        return "1", self.space_id, self.root_sha256

    def check_source_root(self, root):
        # Compare the assigned native representation, without resolving an
        # alternate caller path or accepting aliases/case-folded spellings.
        # Physical root identity remains checked by repository preflight/D1.
        if not isinstance(root, Path) or str(root) != str(self._root):
            raise IndexBindingUnavailableError

    def check_root(self, *, probe=None):
        # Ancestors first; do not traverse a replacement symlink into the root.
        for parent, previous in self._root_directories:
            if probe is not None:
                probe()
            identity = ordinary_identity(parent, directory=True)
            if identity != previous:
                raise IndexBindingUnavailableError


def validate_binding(one, expected: ExpectedIndexBinding, *, charge_bytes=lambda size: None,
                     allow_empty=False):
    """Validate in the caller's transaction, with preflight before each payload.

    `one` owns cursor work, cancellation and optional VM accounting. Return True
    only for a completely unbound, recognized empty store eligible to initialize.
    Missing tables/columns and duplicate meta keys fail closed.
    """
    if one(
        "SELECT count(*) FROM sqlite_master WHERE type='table' "
        "AND name IN ('meta','notes','chunks')"
    )[0] != 3:
        raise IndexBindingUnavailableError
    if one("SELECT count(*) FROM sqlite_master WHERE type='trigger'")[0]:
        raise IndexBindingUnavailableError
    # These keys drive UPSERTs and D1's row cursor. Column presence alone also
    # accepts CREATE TABLE AS SELECT, which discards every constraint.
    for table, key, kind in (("meta", "key", "TEXT"), ("notes", "path", "TEXT"),
                             ("chunks", "id", "INTEGER")):
        primary = one(
            f"SELECT count(*), max(name), max(upper(type)), max(pk) "
            f"FROM pragma_table_info('{table}') WHERE pk > 0"
        )
        if tuple(primary) != (1, key, kind, 1):
            raise IndexBindingUnavailableError
    if one("SELECT count(*) FROM pragma_index_list('chunks') WHERE origin='pk'")[0]:
        # INTEGER PRIMARY KEY must alias rowid (including automatic IDs), not
        # an indexed DESC key or a WITHOUT ROWID primary key.
        raise IndexBindingUnavailableError
    # A single-column path reference with cascading deletion is required to
    # remove stale notes without leaving searchable orphaned chunks.
    foreign = one("SELECT count(*) FROM pragma_foreign_key_list('chunks')")[0]
    cascade = one(
        'SELECT count(*) FROM pragma_foreign_key_list(\'chunks\') '
        'WHERE seq=0 AND "table"=\'notes\' AND "from"=\'path\' '
        'AND "to"=\'path\' AND upper(on_delete)=\'CASCADE\''
    )[0]
    if foreign != 1 or cascade != 1:
        raise IndexBindingUnavailableError
    # The original chunks schema has no UNIQUE indexes: its integer primary
    # key aliases rowid. Also reject autoindexes from added table constraints.
    if one('SELECT count(*) FROM pragma_index_list(\'chunks\') WHERE "unique" != 0')[0]:
        raise IndexBindingUnavailableError
    index_owner = one(
        "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name='idx_chunks_path'"
    )
    if index_owner is None or index_owner[0] != "chunks":
        raise IndexBindingUnavailableError
    full_index = one(
        'SELECT "unique", partial FROM pragma_index_list(\'chunks\') WHERE name=\'idx_chunks_path\''
    )
    if full_index is None or tuple(full_index) != (0, 0):
        raise IndexBindingUnavailableError
    index = one("SELECT name FROM pragma_index_info('idx_chunks_path') WHERE seqno=0")
    if index is None or index[0] != "path":
        raise IndexBindingUnavailableError
    # Verify the existing schema even when no rows survive. LIMIT 0 never transfers
    # derived payload, and cannot treat a missing/unreadable table as empty.
    for sql in (
        "SELECT key,value FROM meta LIMIT 0",
        "SELECT path,mtime_ns,size,sha256,indexed_at FROM notes LIMIT 0",
        "SELECT id,path,chunk_index,heading,content,embedding,dimensions FROM chunks LIMIT 0",
    ):
        one(sql)
    values = []
    for key in BINDING_KEYS:
        descriptor = one(
            "SELECT count(*), max(length(CAST(value AS BLOB))) FROM meta WHERE key=?", (key,)
        )
        if descriptor[0] == 0:
            values.append(None)
            continue
        if descriptor[0] != 1 or type(descriptor[1]) is not int or descriptor[1] < 0:
            raise IndexBindingUnavailableError
        charge_bytes(descriptor[1])
        # Exact expected lengths bound unbudgeted lifecycle reads too.
        if descriptor[1] != len(expected.values[len(values)].encode("utf-8")):
            raise IndexBindingUnavailableError
        value = one("SELECT value FROM meta WHERE key=?", (key,))[0]
        if not isinstance(value, str):
            raise IndexBindingUnavailableError
        values.append(value)
    if tuple(values) == expected.values:
        return False
    if allow_empty and values == [None, None, None] and one(
        "SELECT EXISTS(SELECT 1 FROM notes) OR EXISTS(SELECT 1 FROM chunks)"
    )[0] == 0:
        return True
    raise IndexBindingUnavailableError
