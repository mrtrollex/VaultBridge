from __future__ import annotations

import stat
import tempfile
from pathlib import Path

import pytest

from scripts import smoke_mcp_http
from scripts.smoke_mcp_http import (
    DISPOSABLE_ROOT_PREFIX,
    cleanup_disposable_root,
)


def test_cleanup_disposable_root_restores_permissions_and_removes_root() -> None:
    root = Path(tempfile.mkdtemp(prefix=DISPOSABLE_ROOT_PREFIX)).resolve()
    restricted_directory = root / "vault"
    restricted_directory.mkdir()
    restricted_note = restricted_directory / "Smoke.md"
    restricted_note.write_text("synthetic smoke content", encoding="utf-8")
    restricted_note.chmod(0)

    cleanup_disposable_root(root, expected_root=root)

    assert not root.exists()


def test_cleanup_disposable_root_rejects_unexpected_path(tmp_path: Path) -> None:
    root = tmp_path / f"{DISPOSABLE_ROOT_PREFIX}unrelated"
    root.mkdir()

    with pytest.raises(ValueError, match="refusing unsafe disposable cleanup target"):
        cleanup_disposable_root(root, expected_root=root.resolve())

    assert root.is_dir()
    root.chmod(stat.S_IRWXU)


def test_cleanup_disposable_root_propagates_removal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(tempfile.mkdtemp(prefix=DISPOSABLE_ROOT_PREFIX)).resolve()

    def fail_removal(*_: object, **__: object) -> None:
        raise PermissionError("synthetic cleanup failure")

    monkeypatch.setattr(smoke_mcp_http.shutil, "rmtree", fail_removal)

    with pytest.raises(PermissionError, match="synthetic cleanup failure"):
        cleanup_disposable_root(root, expected_root=root)

    assert root.is_dir()
    root.rmdir()
