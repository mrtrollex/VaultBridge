from __future__ import annotations

import stat
import tempfile
from pathlib import Path

import pytest

from scripts import smoke_mcp_http
from scripts.smoke_mcp_http import (
    CLEANUP_COMMAND,
    CLEANUP_MOUNT,
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

    cleanup_disposable_root(root, expected_root=root, image="vaultbridge:test")

    assert not root.exists()


def test_cleanup_disposable_root_rejects_unexpected_path(tmp_path: Path) -> None:
    root = tmp_path / f"{DISPOSABLE_ROOT_PREFIX}unrelated"
    root.mkdir()

    with pytest.raises(ValueError, match="refusing unsafe disposable cleanup target"):
        cleanup_disposable_root(
            root, expected_root=root.resolve(), image="vaultbridge:test"
        )

    assert root.is_dir()
    root.chmod(stat.S_IRWXU)


def test_cleanup_disposable_root_uses_container_for_permission_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(tempfile.mkdtemp(prefix=DISPOSABLE_ROOT_PREFIX)).resolve()
    calls: list[tuple[str, ...]] = []

    def fail_removal(*_: object, **__: object) -> None:
        raise PermissionError("synthetic cleanup failure")

    def container_cleanup(*args: str, capture: bool = False) -> object:
        assert not capture
        calls.append(args)
        return object()

    monkeypatch.setattr(smoke_mcp_http.shutil, "rmtree", fail_removal)
    monkeypatch.setattr(smoke_mcp_http, "run", container_cleanup)

    cleanup_disposable_root(root, expected_root=root, image="vaultbridge:test")

    assert calls == [
        (
            "docker",
            "run",
            "--rm",
            "--user",
            "0:0",
            "--volume",
            f"{root}:{CLEANUP_MOUNT}:rw",
            "--entrypoint",
            "python",
            "vaultbridge:test",
            "-c",
            CLEANUP_COMMAND,
        )
    ]
    assert not root.exists()


def test_cleanup_disposable_root_propagates_container_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(tempfile.mkdtemp(prefix=DISPOSABLE_ROOT_PREFIX)).resolve()

    def fail_removal(*_: object, **__: object) -> None:
        raise PermissionError("synthetic host cleanup failure")

    def fail_container_cleanup(*_: str, **__: object) -> object:
        raise RuntimeError("synthetic helper failure")

    monkeypatch.setattr(smoke_mcp_http.shutil, "rmtree", fail_removal)
    monkeypatch.setattr(smoke_mcp_http, "run", fail_container_cleanup)

    with pytest.raises(RuntimeError, match="synthetic helper failure"):
        cleanup_disposable_root(root, expected_root=root, image="vaultbridge:test")

    assert root.is_dir()
    root.rmdir()
