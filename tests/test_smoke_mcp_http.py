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


def test_clean_shutdown_waits_for_fingerprint_initialization(monkeypatch):
    import json
    from types import SimpleNamespace

    states = iter((True, True, False))
    events = []

    def request(*args):
        events.append("health")
        return 200, json.dumps({"semantic_indexer_running": next(states)}).encode()

    def run(*args, **kwargs):
        events.append(args[1])
        return SimpleNamespace(stdout="0\n")

    monkeypatch.setattr(smoke_mcp_http, "published_url", lambda name: "http://127.0.0.1:12345")
    monkeypatch.setattr(smoke_mcp_http, "request_status", request)
    monkeypatch.setattr(smoke_mcp_http.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(smoke_mcp_http, "run", run)
    smoke_mcp_http.stop_cleanly("synthetic")
    assert events == ["health", "health", "health", "stop", "inspect", "rm"]


def test_shutdown_preparation_is_bounded(monkeypatch):
    ticks = iter((0, 0, 1201))
    monkeypatch.setattr(smoke_mcp_http.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(smoke_mcp_http.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(smoke_mcp_http, "request_status", lambda *args: (
        200, b'{"semantic_indexer_running":true}'))
    with pytest.raises(RuntimeError, match="bounded shutdown preparation"):
        smoke_mcp_http.wait_until_indexer_idle("http://127.0.0.1:12345")


def test_network_cleanup_failure_cannot_report_pass(monkeypatch, capsys):
    roots = []
    original_mkdtemp = tempfile.mkdtemp

    def mkdtemp(**kwargs):
        root = original_mkdtemp(**kwargs)
        roots.append(Path(root))
        return root

    def run(*args, **kwargs):
        if args[1] == "network":
            raise RuntimeError("synthetic network failure")
        return object()

    monkeypatch.setattr(smoke_mcp_http.tempfile, "mkdtemp", mkdtemp)
    monkeypatch.setattr(smoke_mcp_http, "run", run)
    with pytest.raises(RuntimeError, match="disposable cleanup failed: network removal"):
        smoke_mcp_http.orchestrate("vaultbridge:test")
    assert roots and all(not root.exists() for root in roots)
    assert "PASS" not in capsys.readouterr().out


def test_vault_delta_allows_only_exact_intended_creation(tmp_path):
    (tmp_path / "existing.md").write_bytes(b"unchanged\r\n")
    (tmp_path / "settings.bin").write_bytes(b"\x00fixture")
    original = smoke_mcp_http.vault_snapshot(tmp_path)
    smoke_mcp_http.assert_vault_delta(tmp_path, original, {})
    (tmp_path / "created.md").write_bytes(b"exact new note\n")
    smoke_mcp_http.assert_vault_delta(tmp_path, original, {"created.md": b"exact new note\n"})


@pytest.mark.parametrize("unexpected", ["extra.md", "extra.bin"])
def test_vault_delta_rejects_extra_files(tmp_path, unexpected):
    original = smoke_mcp_http.vault_snapshot(tmp_path)
    (tmp_path / unexpected).write_bytes(b"unexpected")
    with pytest.raises(AssertionError, match="inventory"):
        smoke_mcp_http.assert_vault_delta(tmp_path, original, {})


def test_vault_delta_rejects_modified_original_fixture(tmp_path):
    path = tmp_path / "existing.md"
    path.write_bytes(b"original bytes\r\n")
    original = smoke_mcp_http.vault_snapshot(tmp_path)
    path.write_bytes(b"original bytes\n")
    with pytest.raises(AssertionError, match="unexpected synthetic bytes"):
        smoke_mcp_http.assert_vault_delta(tmp_path, original, {})


@pytest.mark.parametrize("extra", [b"different body", smoke_mcp_http.APPEND_BYTES])
def test_vault_delta_rejects_content_difference_or_duplicate_append(tmp_path, extra):
    expected = b"created body\n" + smoke_mcp_http.APPEND_BYTES
    (tmp_path / "created.md").write_bytes(expected + extra)
    with pytest.raises(AssertionError, match="unexpected synthetic bytes"):
        smoke_mcp_http.assert_vault_delta(tmp_path, {}, {"created.md": expected})


def test_created_bytes_only_accepts_variable_timestamp():
    expected = b'---\ncreated: 2026-10-10T10:00:00+00:00\ntags: ["smoke"]\nsource: chatgpt\n---\n\nbody\n'
    assert smoke_mcp_http.expected_created_bytes(expected, "body", ["smoke"]) == expected
    assert smoke_mcp_http.expected_created_bytes(expected + b"corruption", "body", ["smoke"]) == expected
    with pytest.raises(AssertionError, match="timestamp/header"):
        smoke_mcp_http.expected_created_bytes(b"unknown header", "body", [])
