from __future__ import annotations

import io
import json
import multiprocessing
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from app import cli
from app.core.config import Settings
from app.services.capture import CaptureService
from app.services.frontmatter import FrontmatterParser
from app.services.indexer import BackgroundSemanticIndexer
from app.services.vault import NoteConflictError, NoteNotFoundError, VaultService

CAPTURE_ID = "9f563c82-f963-4a44-9f36-1f7c6f459a4c"
REQUEST = {"capture_id": CAPTURE_ID, "captured_at": "2026-10-03T12:30:00Z", "state": "inbox", "content": "body\r\n"}
TARGET = "Inbox/Captures/" + CAPTURE_ID + ".md"


def service(root, *, max_note_bytes=1_000_000, enqueue=None):
    return CaptureService(VaultService(vault_root=root, max_note_bytes=max_note_bytes), enqueue=enqueue)


def test_portable_composition_and_exact_retry(tmp_path):
    request = {
        **REQUEST,
        "title": "../display only",
        "source": "unknown",
        "tags": ["#Tag", "Tag", "#Tag"],
        "capture_type": "quotation",
        "captured_at": "2026-10-03T12:30:00+00:00",
        "metadata": {"created": "2020-01-01", "unknown": {"yes": [True, None, 3, 1.5, "0123", "true"]}},
    }
    capture = service(tmp_path)
    result = capture.capture(request)
    assert (result.category, result.committed, result.path, result.capture_id) == ("created", True, TARGET, CAPTURE_ID)
    assert result.index_state == "index_unavailable"
    data = (tmp_path / TARGET).read_bytes()
    assert data.endswith(b"\n\nbody\r\n")
    parsed = FrontmatterParser.parse(data.decode())
    assert parsed.state == "valid"
    assert list(parsed.metadata) == [
        "capture_id",
        "capture_state",
        "captured_at",
        "title",
        "source",
        "tags",
        "capture_type",
        "created",
        "unknown",
    ]
    assert parsed.metadata["captured_at"] == "2026-10-03T12:30:00Z"
    assert parsed.metadata["tags"] == ("#Tag", "Tag", "#Tag")
    assert parsed.metadata["unknown"]["yes"] == (True, None, 3, 1.5, "0123", "true")
    assert parsed.metadata["created"] == "2020-01-01"
    # Includes a lost-response retry: no cached result is needed by a new instance.
    assert service(tmp_path).capture(request).category == "already_applied"
    assert capture.capture({**request, "captured_at": REQUEST["captured_at"]}).category == "already_applied"
    for field, value in [("title", "changed"), ("source", "other"), ("state", "draft"), ("content", "other")]:
        assert capture.capture({**request, field: value}).category == "conflict"
        assert (tmp_path / TARGET).read_bytes() == data
    assert list(tmp_path.rglob("*.md")) == [tmp_path / TARGET]


@pytest.mark.parametrize("optional", [{}, {"tags": []}, {"source": "unknown"}])
def test_optional_absence_is_preserved(tmp_path, optional):
    assert service(tmp_path).capture({**REQUEST, **optional}).committed
    metadata = FrontmatterParser.parse((tmp_path / TARGET).read_text()).metadata
    for name in ("title", "source", "tags", "capture_type"):
        assert (name in metadata) == (name in optional)
    if "tags" in optional:
        assert metadata["tags"] == ()


@pytest.mark.parametrize("field,bound", [("content", 65536), ("title", 256), ("source", 2048), ("capture_type", 64)])
def test_field_byte_bounds(tmp_path, field, bound):
    assert service(tmp_path).capture({**REQUEST, field: "é" * (bound // 2)}).committed
    other = tmp_path / "uncreated"
    assert service(other).capture({**REQUEST, field: "é" * (bound // 2) + "x"}).category == "size_limit"
    assert not other.exists()


@pytest.mark.parametrize("field", ["content", "state", "capture_id", "captured_at", "title", "source", "capture_type"])
@pytest.mark.parametrize("value", [None, True, 3, [], {}, "\ud800"])
def test_invalid_field_types_and_utf8_before_mutation(tmp_path, field, value):
    root = tmp_path / "absent"
    assert not service(root).capture({**REQUEST, field: value}).committed
    assert not root.exists()


@pytest.mark.parametrize(
    "change",
    [
        {"content": ""},
        {"content": " \r\n\t"},
        {"state": "reviewed"},
        {"capture_id": CAPTURE_ID.upper()},
        {"capture_id": "../bad"},
        {"capture_id": CAPTURE_ID.replace("-4a44-", "-1a44-")},
        {"captured_at": "2026-10-03T12:30:00"},
        {"captured_at": "2026-10-03T12:30:00.0Z"},
        {"captured_at": "2026-10-03T12:30:00+01:00"},
        {"captured_at": "2026-02-30T12:30:00Z"},
        {"captured_at": "2026-10-03T24:30:00Z"},
        {"tags": None},
        {"tags": "tag"},
        {"tags": [1]},
        {"metadata": []},
        {"metadata": {1: "value"}},
        {"metadata": {"capture_id": "other"}},
        {"metadata": {"title": "override"}},
        {"metadata": {"capture_state": "draft"}},
        {"metadata": {"extra": object()}},
        {"metadata": {"extra": float("inf")}},
        {"metadata": {"extra": {"unsupported"}}},
        {"folder": "Elsewhere"},
    ],
)
def test_invalid_requests_fail_before_any_filesystem_work(tmp_path, change, monkeypatch):
    capture = service(tmp_path)

    def forbidden(**_kwargs):
        raise AssertionError("invalid request reached filesystem boundary")

    monkeypatch.setattr(capture.vault_service, "create_capture_if_absent", forbidden)
    assert capture.capture({**REQUEST, **change}).category == "invalid_request"
    assert not list(tmp_path.iterdir())


def nested(depth):
    value = "value"
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize(
    "change",
    [
        {"tags": ["tag"] * 17},
        {"tags": ["é" * 513]},
        {"metadata": {"k" * 257: "v"}},
        {"metadata": {"k": "v" * 8191}},
        {"metadata": {"k": nested(8)}},
        {"metadata": {"k": [None] * 1024}},
        {"metadata": {str(i): "v" * 8000 for i in range(9)}},
    ],
)
def test_profile_bounds_before_mutation(tmp_path, change):
    assert service(tmp_path).capture({**REQUEST, **change}).category == "size_limit"
    assert not list(tmp_path.iterdir())


def test_allowed_profile_boundaries_and_complete_note_size(tmp_path):
    change = {"tags": ["x" * 1024] * 16, "metadata": {"k" * 256: nested(7), "scalar": "x" * 8190}}
    assert service(tmp_path).capture({**REQUEST, **change}).committed
    size = (tmp_path / TARGET).stat().st_size
    assert service(tmp_path, max_note_bytes=size).capture({**REQUEST, **change}).category == "already_applied"
    assert service(tmp_path, max_note_bytes=size - 1).capture({**REQUEST, **change}).category == "size_limit"


@pytest.mark.parametrize("entry", ["inbox", "Inbox", "captures"])
def test_unsafe_directory_spelling_and_files(tmp_path, entry):
    if entry == "captures":
        (tmp_path / "Inbox").mkdir()
        (tmp_path / "Inbox" / entry).mkdir()
    elif entry == "Inbox":
        (tmp_path / entry).write_text("file")
    else:
        (tmp_path / entry).mkdir()
    assert service(tmp_path).capture(REQUEST).category == "unsafe_destination"
    assert not list(tmp_path.rglob("*.md"))


@pytest.mark.parametrize("where", ["Inbox", "Inbox/Captures", TARGET])
def test_symlink_destinations_fail_safely(tmp_path, where):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = vault / where
    link.parent.mkdir(parents=True, exist_ok=True)
    target = outside
    if where == TARGET:
        target = outside / "other.md"
        target.write_text("unchanged")
    try:
        link.symlink_to(target, target_is_directory=where != TARGET)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable; run POSIX suite")
    assert service(vault).capture(REQUEST).category == "unsafe_destination"
    assert list(outside.iterdir()) == ([target] if where == TARGET else [])


@pytest.mark.parametrize(
    "data,category", [(b"broken", "conflict"), (b"\xff", "unsafe_destination"), (b"x" * 1001, "size_limit")]
)
def test_existing_invalid_or_oversize_targets_are_never_replaced(tmp_path, data, category):
    path = tmp_path / TARGET
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    assert service(tmp_path, max_note_bytes=1000).capture(REQUEST).category == category
    assert path.read_bytes() == data


def process_capture(root, request, barrier, queue, action="capture"):
    barrier.wait(timeout=20)
    vault = VaultService(vault_root=Path(root), max_note_bytes=1_000_000)
    if action == "capture":
        category = CaptureService(vault).capture(request).category
    elif action == "create":
        try:
            category = vault.create_note(title=CAPTURE_ID, folder="Inbox/Captures", content="legacy", tags=[]).status
        except NoteConflictError:
            category = "conflict"
    else:
        try:
            category = vault.append_note(path=TARGET, content="legacy append", dedupe_key="same").status
        except NoteNotFoundError:
            category = "missing"
    queue.put(category)


@pytest.mark.parametrize("different", [False, True])
def test_simultaneous_threads(tmp_path, different):
    barrier = Barrier(8)

    def attempt(index):
        barrier.wait(timeout=20)
        return (
            service(tmp_path).capture({**REQUEST, "content": str(index) if different else REQUEST["content"]}).category
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(8)))
    assert results.count("created") == 1
    assert results.count("conflict" if different else "already_applied") == 7
    assert len(list(tmp_path.rglob("*.md"))) == 1


@pytest.mark.parametrize("action", ["same", "different", "create", "append", "append_existing"])
def test_simultaneous_processes_and_legacy_callers(tmp_path, action):
    if action == "append_existing":
        assert service(tmp_path).capture(REQUEST).committed
    context = multiprocessing.get_context("spawn")
    barrier, queue = context.Barrier(4), context.Queue()
    processes = []
    for index in range(4):
        request = {**REQUEST, "content": str(index) if action == "different" else REQUEST["content"]}
        legacy_action = "append" if action == "append_existing" else action
        operation = legacy_action if legacy_action in ("create", "append") and index else "capture"
        process = context.Process(target=process_capture, args=(str(tmp_path), request, barrier, queue, operation))
        process.start()
        processes.append(process)
    results = [queue.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    if action in ("same", "different"):
        assert results.count("created") == 1
        assert results.count("already_applied" if action == "same" else "conflict") == 3
    elif action == "create":
        assert results.count("created") == 1
        assert service(tmp_path).capture(REQUEST).category in ("already_applied", "conflict")
    elif action == "append":
        assert results.count("created") == 1
        assert results.count("appended") <= 1
        assert (tmp_path / TARGET).read_text().count("legacy append") <= 1
    else:
        assert results.count("appended") == 1
        assert (tmp_path / TARGET).read_text().count("legacy append") == 1
    assert len(list(tmp_path.rglob("*.md"))) == 1


@pytest.mark.parametrize(
    "stage,category",
    [("flush", "write_unavailable"), ("link_before", "commit_unknown"), ("link_after", "commit_unknown")],
)
def test_injected_commit_failures_never_expose_partial_markdown(tmp_path, monkeypatch, stage, category):
    original_link = os.link
    original_fsync = os.fsync
    if stage == "flush":

        def fsync(_fd):
            raise OSError("private path and content")

        monkeypatch.setattr(os, "fsync", fsync)
    else:

        def link(*args, **kwargs):
            if stage == "link_after":
                original_link(*args, **kwargs)
            raise OSError("private path and content")

        monkeypatch.setattr(os, "link", link)
    assert service(tmp_path).capture(REQUEST).category == category
    path = tmp_path / TARGET
    assert path.exists() == (stage == "link_after")
    assert not list(tmp_path.rglob("*.tmp"))
    monkeypatch.setattr(os, "link", original_link)
    monkeypatch.setattr(os, "fsync", original_fsync)
    if path.exists():
        # An exact-path read can resolve the uncertain result without another write.
        assert service(tmp_path).capture(REQUEST).category == "already_applied"
    elif stage == "flush":
        # Failure before a possible commit permits a fresh attempt.
        assert service(tmp_path).capture(REQUEST).category == "created"
    else:
        # commit_unknown with an absent path is retained for operator resolution.
        assert category == "commit_unknown"


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "failed", "later_failed"])
def test_post_commit_index_evidence_and_retry(tmp_path, outcome, caplog):
    def sync(_event):
        raise OSError("private index failure")

    worker = BackgroundSemanticIndexer(sync, emit_note_paths=False)

    def enqueue(path):
        assert path == TARGET
        if outcome == "failed":
            raise OSError("private metadata URL")
        return worker.enqueue(path) if outcome == "later_failed" else outcome == "accepted"

    try:
        capture = service(tmp_path, enqueue=enqueue)
        result = capture.capture(REQUEST)
        assert result.category == "created" and result.committed
        assert result.index_state == (
            "index_pending" if outcome in ("accepted", "later_failed") else "index_unavailable"
        )
        if outcome == "later_failed":
            with pytest.raises(OSError):
                worker.wait()
        assert capture.capture(REQUEST).category == "already_applied"
        assert list(tmp_path.rglob("*.md")) == [tmp_path / TARGET]
        assert TARGET not in caplog.text and "private" not in caplog.text
    finally:
        worker.shutdown()


def test_programming_errors_are_not_validation_results(tmp_path):
    def bug(_path):
        raise sqlite3.ProgrammingError("bug")

    with pytest.raises(sqlite3.ProgrammingError):
        service(tmp_path, enqueue=bug).capture(REQUEST)
    assert service(tmp_path).capture(REQUEST).category == "already_applied"


def test_filesystem_programming_error_is_not_an_expected_failure(tmp_path, monkeypatch):
    def bug(*_args, **_kwargs):
        raise ValueError("programming bug")
    monkeypatch.setattr(os, "link", bug)
    with pytest.raises(ValueError, match="programming bug"):
        service(tmp_path).capture(REQUEST)
    assert not list(tmp_path.rglob("*.md"))


@pytest.mark.parametrize(
    "raw,category",
    [
        (b'{"content": "private", "content": "other"}', "invalid_request"),
        (b'{"metadata": {"nested": 1, "nested": 2}}', "invalid_request"),
        (b"[]", "invalid_request"),
        (b'"private"', "invalid_request"),
        (b'{"private": NaN}', "invalid_request"),
        (b"\xff", "invalid_request"),
        (b"x" * 1_048_577, "size_limit"),
        (b"[" * 2000, "invalid_request"),
    ],
    ids=["duplicate", "nested_duplicate", "array", "string", "nonfinite", "utf8", "size", "depth"],
)
def test_cli_bounded_private_invalid_input(tmp_path, raw, category):
    output = io.StringIO()
    assert cli.run_capture(Settings(), input_stream=io.BytesIO(raw), output=output, service=service(tmp_path)) == 1
    assert json.loads(output.getvalue()) == {"category": category, "committed": False}
    assert not list(tmp_path.iterdir())


def test_cli_success_and_retry_echo_only_authorized_evidence(tmp_path):
    for category in ("created", "already_applied"):
        output = io.StringIO()
        assert (
            cli.run_capture(
                Settings(),
                input_stream=io.BytesIO(json.dumps(REQUEST).encode()),
                output=output,
                service=service(tmp_path),
            )
            == 0
        )
        assert json.loads(output.getvalue()) == {
            "category": category,
            "committed": True,
            "capture_id": CAPTURE_ID,
            "path": TARGET,
            "index_state": "index_unavailable",
        }
    assert cli._parser().parse_args(["capture"]).action == "capture"


def test_legacy_create_keeps_missing_root_behavior(tmp_path):
    vault = VaultService(vault_root=tmp_path / "missing", max_note_bytes=1000)
    assert vault.create_note(title="legacy", folder="", content="body", tags=[]).status == "created"
    assert vault.create_note(title="legacy", folder="", content="body", tags=[]).status == "unchanged"


def test_complete_bytes_not_normalized_for_retry(tmp_path):
    assert service(tmp_path).capture(REQUEST).committed
    path = tmp_path / TARGET
    data = path.read_bytes()
    path.write_bytes(data.replace(b"\r\n", b"\n"))
    assert service(tmp_path).capture(REQUEST).category == "conflict"
    assert path.read_bytes() != data


def test_target_case_variant_and_directory_are_unsafe(tmp_path):
    parent = tmp_path / "Inbox/Captures"
    parent.mkdir(parents=True)
    variant = parent / (CAPTURE_ID.upper() + ".md")
    variant.write_bytes(b"unchanged")
    assert service(tmp_path).capture(REQUEST).category == "unsafe_destination"
    assert variant.read_bytes() == b"unchanged"
    variant.unlink()
    (tmp_path / TARGET).mkdir()
    assert service(tmp_path).capture(REQUEST).category == "unsafe_destination"


def test_partial_temporary_write_failure_is_not_a_markdown_commit(tmp_path, monkeypatch):
    fdopen = os.fdopen

    class PartialWriter:
        def __init__(self, fd):
            self.stream = fdopen(fd, "wb")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.stream.close()

        def write(self, data):
            self.stream.write(data[: len(data) // 2])
            raise OSError("private write error")

    monkeypatch.setattr(os, "fdopen", lambda fd, mode: PartialWriter(fd) if mode == "wb" else fdopen(fd, mode))
    assert service(tmp_path).capture(REQUEST).category == "write_unavailable"
    assert not list(tmp_path.rglob("*.md"))
    assert not list(tmp_path.rglob("*.tmp"))


def test_existing_target_io_denial_has_private_diagnostics(tmp_path, monkeypatch):
    assert service(tmp_path).capture(REQUEST).committed
    data = (tmp_path / TARGET).read_bytes()
    original = os.open

    def denied(path, flags, *args, **kwargs):
        if str(path).endswith(CAPTURE_ID + ".md"):
            raise PermissionError("private URL path title")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied)
    output = io.StringIO()
    assert (
        cli.run_capture(
            Settings(), input_stream=io.BytesIO(json.dumps(REQUEST).encode()), output=output, service=service(tmp_path)
        )
        == 1
    )
    assert json.loads(output.getvalue()) == {"category": "write_unavailable", "committed": False}
    assert (tmp_path / TARGET).read_bytes() == data


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative symlink race test")
def test_directory_swap_at_commit_cannot_redirect_to_symlink_target(tmp_path, monkeypatch):
    vault, outside = tmp_path / "vault", tmp_path / "outside"
    vault.mkdir()
    outside.mkdir()
    original = os.link

    def swap(*args, **kwargs):
        parent = vault / "Inbox/Captures"
        parent.rename(vault / "Inbox/Original")
        parent.symlink_to(outside, target_is_directory=True)
        original(*args, **kwargs)

    monkeypatch.setattr(os, "link", swap)
    result = service(vault).capture(REQUEST)
    assert result.category == "commit_unknown"
    assert not result.committed
    assert not list(outside.iterdir())
    assert (vault / "Inbox/Original" / (CAPTURE_ID + ".md")).read_bytes() == service(vault)._compose(REQUEST)[1]
    assert not (vault / TARGET).exists()
    assert not list(vault.rglob("*.tmp"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative symlink race test")
def test_destination_symlink_race_fails_without_overwrite(tmp_path, monkeypatch):
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"unchanged")
    vault = tmp_path / "vault"
    vault.mkdir()
    original = os.link

    def swap(*args, **kwargs):
        (vault / TARGET).symlink_to(outside)
        original(*args, **kwargs)

    monkeypatch.setattr(os, "link", swap)
    assert service(vault).capture(REQUEST).category == "unsafe_destination"
    assert outside.read_bytes() == b"unchanged"


@pytest.mark.parametrize("replacement", ["file", "symlink"])
def test_replaced_staged_source_never_commits_substitute(tmp_path, monkeypatch, replacement):
    if replacement == "symlink" and os.name == "nt":
        pytest.skip("symlink privilege is unavailable on this Windows host")
    original_link = os.link
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"outside bytes")
    attempted = []

    def substitute(*args, **kwargs):
        staged = list((tmp_path / "Inbox/Captures").glob(".vaultbridge-*.tmp"))
        assert len(staged) == 1
        try:
            staged[0].unlink()
        except PermissionError:
            attempted.append("denied")
        else:
            attempted.append("replaced")
            if replacement == "file":
                staged[0].write_bytes(b"substituted bytes")
            else:
                staged[0].symlink_to(outside)
        return original_link(*args, **kwargs)

    expected = service(tmp_path)._compose(REQUEST)[1]
    with monkeypatch.context() as patch:
        patch.setattr(os, "link", substitute)
        result = service(tmp_path).capture(REQUEST)
    artifact = tmp_path / TARGET
    assert attempted == (["denied"] if os.name == "nt" else ["replaced"])
    if result.committed:
        assert result.category == "created"
        assert artifact.is_file() and not artifact.is_symlink()
        assert artifact.read_bytes() == expected
    else:
        assert result.category == "commit_unknown"
        assert not artifact.exists()
    assert outside.read_bytes() == b"outside bytes"
    if result.committed:
        assert service(tmp_path).capture(REQUEST).category == "already_applied"
        assert (tmp_path / TARGET).read_bytes() == expected
    else:
        # The caller retains commit_unknown; absence alone does not authorize retry.
        assert result.category == "commit_unknown"


def test_staged_byte_mutation_cannot_be_reported_as_created(tmp_path, monkeypatch):
    original_link = os.link
    expected = service(tmp_path)._compose(REQUEST)[1]

    def rewrite(*args, **kwargs):
        staged = list((tmp_path / "Inbox/Captures").glob(".vaultbridge-*.tmp"))
        assert len(staged) == 1
        try:
            staged[0].write_bytes(b"substituted bytes")
        except PermissionError:
            pass  # The held Windows staging handle denies concurrent writes.
        return original_link(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", rewrite)
        result = service(tmp_path).capture(REQUEST)
    artifact = tmp_path / TARGET
    if os.name == "nt":
        assert result.category == "created"
        assert artifact.read_bytes() == expected
    else:
        # Active modification by a non-cooperating filesystem writer is outside
        # the atomic guarantee, but detected wrong bytes cannot confirm creation.
        assert result.category == "commit_unknown"
        assert artifact.read_bytes() == b"substituted bytes"


@pytest.mark.skipif(os.name == "nt", reason="POSIX rename race")
def test_relocated_capture_directory_reports_uncertain_commit_without_retry(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    original_link = os.link

    def relocate(*args, **kwargs):
        (vault / "Inbox/Captures").rename(outside / "Relocated")
        (vault / "Inbox/Captures").mkdir()
        return original_link(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", relocate)
        first = service(vault).capture(REQUEST)
    artifacts = list(tmp_path.rglob(CAPTURE_ID + ".md"))
    assert first.category == "commit_unknown"
    assert not first.committed
    assert not (vault / TARGET).exists()
    assert (outside / "Relocated" / (CAPTURE_ID + ".md")).read_bytes() == service(vault)._compose(REQUEST)[1]
    assert len(artifacts) == 1
    # A caller that received commit_unknown must obtain operator resolution;
    # the absent canonical path alone cannot authorize another write.


def test_large_portable_integer_preserves_typed_value(tmp_path):
    value = 1 << 15000
    assert service(tmp_path).capture({**REQUEST, "metadata": {"large": value}}).committed
    assert FrontmatterParser.parse((tmp_path / TARGET).read_text()).metadata["large"] == value


def test_unicode_metadata_round_trips_without_yaml_line_folding(tmp_path):
    value = "é 😀\u0085\u2028\u2029\x00\r\n"
    request = {**REQUEST, "title": value, "metadata": {value: [value, "true", "2026-10-03T12:30:00Z"]}}
    assert service(tmp_path).capture(request).committed
    parsed = FrontmatterParser.parse((tmp_path / TARGET).read_text(encoding="utf-8")).metadata
    assert parsed["title"] == value
    assert parsed[value] == (value, "true", "2026-10-03T12:30:00Z")


def test_cli_production_worker_is_lazy_private_and_drained(tmp_path, monkeypatch, caplog):
    calls = []

    class Semantic:
        def sync(self, _event):
            raise AssertionError("unexpected full sync")

        def sync_paths(self, paths, _event):
            calls.append(tuple(paths))
            raise OSError("private index failure")

    monkeypatch.setattr(cli, "semantic_search_service_from_settings", lambda _settings: Semantic())
    settings = Settings(vault_path=tmp_path)
    output = io.StringIO()
    assert cli.run_capture(settings, input_stream=io.BytesIO(b"{}"), output=output) == 1
    assert not calls
    output = io.StringIO()
    assert cli.run_capture(settings, input_stream=io.BytesIO(json.dumps(REQUEST).encode()), output=output) == 0
    assert calls == [(TARGET,)]
    assert json.loads(output.getvalue())["index_state"] == "index_pending"
    assert TARGET not in caplog.text and "private" not in caplog.text


def test_worker_submission_failure_reports_index_unavailable(tmp_path, monkeypatch, caplog):
    from concurrent.futures import ThreadPoolExecutor

    def failed(*_args, **_kwargs):
        raise RuntimeError("private worker failure")

    monkeypatch.setattr(ThreadPoolExecutor, "submit", failed)
    output = io.StringIO()
    settings = Settings(vault_path=tmp_path)
    assert cli.run_capture(settings, input_stream=io.BytesIO(json.dumps(REQUEST).encode()), output=output) == 0
    assert json.loads(output.getvalue())["index_state"] == "index_unavailable"
    assert "private" not in caplog.text and TARGET not in caplog.text


def test_aggregate_item_boundary(tmp_path):
    # Root has three fixed entries plus the additional entry; sequence has 1020.
    request = {**REQUEST, "metadata": {"items": [None] * 1020}}
    assert service(tmp_path).capture(request).committed
    assert service(tmp_path).capture({**REQUEST, "metadata": {"items": [None] * 1021}}).category == "size_limit"


@pytest.mark.skipif(os.name != "nt", reason="Windows handle pinning")
def test_windows_commit_pins_directory_against_rename(tmp_path, monkeypatch):
    original = os.link

    def attempted_swap(*args, **kwargs):
        with pytest.raises(OSError):
            (tmp_path / "Inbox/Captures").rename(tmp_path / "Inbox/Moved")
        original(*args, **kwargs)

    monkeypatch.setattr(os, "link", attempted_swap)
    assert service(tmp_path).capture(REQUEST).category == "created"


@pytest.mark.skipif(os.name == "nt", reason="POSIX ancestor symlink replacement")
def test_swapped_root_ancestor_cannot_redirect_writes(tmp_path):
    parent = tmp_path / "parent"
    (parent / "vault").mkdir(parents=True)
    capture = service(parent / "vault")
    parent.rename(tmp_path / "original")
    outside = tmp_path / "outside"
    (outside / "vault").mkdir(parents=True)
    parent.symlink_to(outside, target_is_directory=True)
    assert capture.capture(REQUEST).category == "unsafe_destination"
    assert not list(outside.rglob("*.md"))
    assert not (outside / "vault/Inbox").exists()
