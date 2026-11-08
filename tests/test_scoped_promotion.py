"""Scoped v2 provenance and actual private runtime integration."""
import dataclasses
import hashlib
import json
import shutil

import pytest

from app.core.config import Settings
from app.services.capture import CaptureService
from app.services.frontmatter import FrontmatterParser
from app.services.knowledge_spaces import Capability, Dialect, QualifiedPath, SpaceError, SpaceId, WriteScope
from app.services.promotion import PromotionError, PromotionService, _json_bytes
from app.services.space_runtime import compose_space_runtime
from app.services.vault import VaultService
from tests.test_promotion import CAPTURE_ID, PROMOTION_ID, SOURCE


def request():
    return dict(content="Original body", state="draft", capture_id=CAPTURE_ID,
                captured_at="2026-10-03T12:00:00+00:00", title="Title", source="declared<source")


def runtime_settings(tmp_path, indexing="disabled"):
    records = []
    for name in ("default", "research", "denied"):
        root = tmp_path / name
        root.mkdir()
        (root / "Notes").mkdir()
        records.append(dict(space_id=name, root=str(root), semantic_data_path=str(tmp_path / (name+"-data")),
            read="allow", write="deny" if name == "denied" else "allow", indexing=indexing,
            dialects=[x.value for x in Dialect], capabilities=[x.value for x in Capability]))
    settings = Settings(vault_path=records[0]["root"], semantic_data_path=records[0]["semantic_data_path"],
                        knowledge_spaces_json=json.dumps(dict(version=1, spaces=records)))
    return settings


@pytest.fixture
def runtime(tmp_path):
    rt = compose_space_runtime(runtime_settings(tmp_path)).start()
    yield rt
    rt.shutdown()


def path(sid, rel):
    return QualifiedPath(SpaceId(sid), rel)


def capture(rt, sid="research", **changes):
    return rt.operations.scoped_capture(WriteScope(SpaceId(sid)), {**request(), **changes}).result


def review(rt, sid="research"):
    return rt.operations.scoped_promotion_review(path(sid, SOURCE), CAPTURE_ID,
                                               destination=path(sid, "Notes/Target.md"))


def approve(rt, sid="research", action="create"):
    snapshot = review(rt, sid)
    data = dict(source_path=SOURCE, capture_id=CAPTURE_ID, expected_source_sha256=snapshot.review.source_sha256,
        approved_content="Approved body", action=action, destination="Notes/Target.md", promotion_id=PROMOTION_ID,
        approved_at="2026-10-03T13:00:00Z", transfer_fields=["title"])
    if action == "create":
        data["expected_destination"] = "absent"
    else:
        data["expected_destination_sha256"] = rt.operations.scoped_promotion_destination(
            path(sid, "Notes/Target.md")).result.sha256
    return rt.operations.scoped_promotion_decide(snapshot, data)


@pytest.mark.parametrize("sid", ["default", "research"])
@pytest.mark.parametrize("action", ["create", "append"])
def test_v2_digest_bytes_and_retry(runtime, sid, action):
    rt = runtime
    assert capture(rt, sid).category == "created"
    assert capture(rt, sid).category == "already_applied"
    root = rt.registry._owners[SpaceId(sid)].vault.vault_root
    metadata = FrontmatterParser.parse((root / SOURCE).read_text()).metadata
    assert type(metadata["capture_provenance_version"]) is int
    assert metadata["capture_provenance_version"] == 2 and metadata["capture_space_id"] == sid
    target = root / "Notes/Target.md"
    if action == "append":
        target.write_bytes(b"Existing")
    approved = approve(rt, sid, action)
    d = approved.decision
    digest = dict(action=action, approved_at=d.approved_at, approved_content=d.approved_content,
        capture_id=CAPTURE_ID, captured_at=d.captured_at, destination=d.destination,
        expected_source_sha256=d.expected_source_sha256, promotion_id=PROMOTION_ID, source_path=SOURCE,
        transfer_fields=["title"], transfer={"capture_title": "Title"}, capture_declared_source="declared<source",
        provenance_version=2, source_space_id=sid, destination_space_id=sid)
    digest.update({"expected_destination": "absent"} if action == "create" else
                  {"expected_destination_sha256": d.expected_destination_sha256})
    assert d.decision_sha256 == hashlib.sha256(_json_bytes(digest)).hexdigest()
    if action == "create":
        fm = FrontmatterParser.parse(d.markdown.decode()).metadata
        assert fm["promotion_provenance_version"] == 2
        assert fm["promotion_space_id"] == fm["promoted_from_capture_space_id"] == sid
    else:
        assert b"vaultbridge-promotion:v2 id=" in d.markdown
        assert b'"source_space_id":"' + sid.encode() + b'"' in d.markdown
        assert b"\\u003csource" in d.markdown
    expected = "created" if action == "create" else "appended"
    assert rt.operations.scoped_promotion_apply(approved).result.category == expected
    before = target.read_bytes()
    assert rt.operations.scoped_promotion_apply(approved).result.category == "already_applied"
    assert target.read_bytes() == before
    assert not list(root.parent.glob("*-data"))


@pytest.mark.parametrize("metadata", [{"capture_space_id": "research"}, {"capture_provenance_version": 2}])
def test_reserved_metadata(runtime, metadata):
    assert capture(runtime, metadata=metadata).category == "invalid_request"


@pytest.mark.parametrize("scope", [None, [SpaceId("research")],
                                   WriteScope(SpaceId("denied")), WriteScope(SpaceId("unknown"))])
def test_denial_before_owner_work(runtime, scope, monkeypatch):
    monkeypatch.setattr(VaultService, "root_binding_identity", lambda *a: pytest.fail("owner work before denial"))
    with pytest.raises(SpaceError):
        runtime.operations.scoped_capture(scope, request())


def test_cross_space_rejected_before_read(runtime, monkeypatch):
    monkeypatch.setattr(VaultService, "read_promotion_bytes", lambda *a, **k: pytest.fail("read"))
    with pytest.raises(SpaceError, match="invalid_scope"):
        runtime.operations.scoped_promotion_review(path("research", SOURCE), CAPTURE_ID,
                                                  destination=path("default", "Target.md"))


@pytest.mark.parametrize("version", [None, True, "2", 1, 3])
def test_invalid_provenance(runtime, version):
    capture(runtime)
    root = runtime.registry._owners[SpaceId("research")].vault.vault_root
    p = root / SOURCE
    data = p.read_text()
    data = data.replace('"capture_provenance_version": 2', '"capture_provenance_version": ' + json.dumps(version))
    p.write_text(data)
    with pytest.raises(PromotionError, match="unsafe_source"):
        review(runtime)


def test_copied_v1_v2_and_downgrade(runtime):
    rt = runtime
    default = rt.registry._owners[SpaceId("default")].vault
    research = rt.registry._owners[SpaceId("research")].vault
    assert CaptureService(default).capture(request()).committed
    (research.vault_root / SOURCE).parent.mkdir(parents=True)
    shutil.copyfile(default.vault_root / SOURCE, research.vault_root / SOURCE)
    with pytest.raises(PromotionError, match="unsafe_source"):
        review(rt)
    (research.vault_root / SOURCE).unlink()
    assert capture(rt).committed
    shutil.copyfile(research.vault_root / SOURCE, default.vault_root / SOURCE)
    with pytest.raises(PromotionError, match="unsafe_source"):
        review(rt, "default")
    with pytest.raises(PromotionError, match="unsafe_source"):
        PromotionService(default).review(SOURCE, CAPTURE_ID)


def test_stale_approval_and_wrong_registry(runtime, tmp_path):
    capture(runtime)
    approved = approve(runtime)
    wrong = dataclasses.replace(approved, space_id=SpaceId("default"))
    with pytest.raises(SpaceError, match="invalid_scope"):
        runtime.operations.scoped_promotion_apply(wrong)
    root = runtime.registry._owners[SpaceId("research")].vault.vault_root
    (root / SOURCE).write_bytes((root / SOURCE).read_bytes() + b"changed")
    with pytest.raises(PromotionError, match="source_changed"):
        runtime.operations.scoped_promotion_apply(approved)
    assert not (root / "Notes/Target.md").exists()


def test_mixed_markers_cannot_prove_retry(runtime):
    capture(runtime)
    root = runtime.registry._owners[SpaceId("research")].vault.vault_root
    target = root / "Notes/Target.md"
    target.write_bytes(b"Existing")
    approved = approve(runtime, action="append")
    target.write_bytes(target.read_bytes() + approved.decision.markdown +
        b"<!-- vaultbridge-promotion:v1 id=" + PROMOTION_ID.encode() + b" -->")
    before = target.read_bytes()
    assert runtime.operations.scoped_promotion_apply(approved).result.category == "conflict"
    assert target.read_bytes() == before


def test_registry_replay(runtime, tmp_path):
    capture(runtime)
    approved = approve(runtime)
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = compose_space_runtime(runtime_settings(other_root)).start()
    try:
        with pytest.raises(SpaceError, match="invalid_scope"):
            other.operations.scoped_promotion_apply(approved)
        with pytest.raises(SpaceError, match="invalid_scope"):
            other.operations.scoped_promotion_decide(review(runtime), json.loads(approved._request_json))
    finally:
        other.shutdown()


@pytest.mark.parametrize("failure", [False, True])
def test_enabled_enqueue_is_qualified_after_commit(tmp_path, monkeypatch, failure):
    from app.services.semantic_scheduler import SharedSemanticScheduler
    from tests.test_space_runtime import backend as backend_fixture
    # Use the same deterministic local backend as E6, with no download/model service.
    backend_fixture.__wrapped__(monkeypatch)
    rt = compose_space_runtime(runtime_settings(tmp_path, "enabled")).start()
    calls = []

    def enqueue(scheduler, binding, target):
        assert binding._owners is rt.registry._owners[target.space_id]
        assert target.space_id == SpaceId("research")
        assert (binding._owners.vault.vault_root / target.relative_path).is_file()
        calls.append(target)
        if failure:
            raise OSError("injected enqueue failure")
        return True

    monkeypatch.setattr(SharedSemanticScheduler, "enqueue", enqueue)
    try:
        result = capture(rt)
        assert result.committed
        assert result.index_state == ("index_unavailable" if failure else "index_pending")
        assert capture(rt).category == "already_applied"
        assert len(calls) == 1
        approved = approve(rt)
        assert rt.operations.scoped_promotion_apply(approved).result.committed
        assert len(calls) == 2
        assert rt.operations.scoped_promotion_apply(approved).result.category == "already_applied"
        assert len(calls) == 2
    finally:
        rt.shutdown()


def test_cancellation_after_commit_retains_result(runtime, monkeypatch):
    original = CaptureService.capture

    def committed_then_stop(owner, *args, **kwargs):
        result = original(owner, *args, **kwargs)
        runtime.admission.begin_shutdown()
        return result

    monkeypatch.setattr(CaptureService, "capture", committed_then_stop)
    assert capture(runtime).committed
    with pytest.raises(SpaceError, match="operation_cancelled"):
        capture(runtime)


def test_capture_uncertain_commit_is_not_enqueued(runtime, monkeypatch):
    import os
    original = os.link

    def linked_then_failed(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("injected after commit")

    monkeypatch.setattr(os, "link", linked_then_failed)
    result = capture(runtime)
    assert result.category == "commit_unknown" and not result.committed
    monkeypatch.setattr(os, "link", original)
    assert capture(runtime).category == "already_applied"


def test_scoped_symlink_escape(runtime, tmp_path):
    import os
    if os.name == "nt":
        # Native Windows junction creation requires no symlink privilege.
        import subprocess
        root = runtime.registry._owners[SpaceId("research")].vault.vault_root
        outside = tmp_path / "outside"
        outside.mkdir()
        subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "Inbox"), str(outside)],
                       check=True, capture_output=True)
    else:
        root = runtime.registry._owners[SpaceId("research")].vault.vault_root
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "Inbox").symlink_to(outside, target_is_directory=True)
    assert capture(runtime).category == "unsafe_destination"
    assert not list(outside.iterdir())


def test_legacy_capture_bytes_match_explicit_golden(tmp_path):
    vault = VaultService(vault_root=tmp_path, max_note_bytes=1_000_000)
    result = CaptureService(vault).capture(request())
    assert result.committed
    assert (tmp_path / SOURCE).read_bytes() == (
        b'---\n{"capture_id": "12345678-1234-4123-8123-123456789abc", '
        b'"capture_state": "draft", "captured_at": "2026-10-03T12:00:00Z", '
        b'"title": "Title", "source": "declared<source"}\n---\n\nOriginal body')


@pytest.mark.parametrize("field,value", [("approved_content", "Unapproved"), ("destination", "Notes/Other.md")])
def test_changed_decision_request_cannot_mutate(runtime, field, value):
    capture(runtime)
    approved = approve(runtime)
    changed = json.loads(approved._request_json)
    changed[field] = value
    forged = dataclasses.replace(approved, _request_json=json.dumps(changed))
    with pytest.raises(PromotionError):
        runtime.operations.scoped_promotion_apply(forged)
    root = runtime.registry._owners[SpaceId("research")].vault.vault_root
    assert not list((root / "Notes").iterdir())


def test_source_rechecked_inside_coordinated_commit(runtime, monkeypatch):
    capture(runtime)
    approved = approve(runtime)
    root = runtime.registry._owners[SpaceId("research")].vault.vault_root
    original = VaultService.create_promotion_if_absent

    def changed(owner, **kwargs):
        source = root / SOURCE
        source.write_bytes(source.read_bytes() + b"changed during apply")
        return original(owner, **kwargs)

    monkeypatch.setattr(VaultService, "create_promotion_if_absent", changed)
    assert runtime.operations.scoped_promotion_apply(approved).result.category == "source_changed"
    assert not (root / "Notes/Target.md").exists()


def test_stale_destination_and_parallel_exact_retry(runtime):
    from concurrent.futures import ThreadPoolExecutor
    capture(runtime)
    root = runtime.registry._owners[SpaceId("research")].vault.vault_root
    target = root / "Notes/Target.md"
    target.write_bytes(b"Existing")
    approved = approve(runtime, action="append")
    target.write_bytes(b"Changed")
    assert runtime.operations.scoped_promotion_apply(approved).result.category == "destination_changed"
    assert target.read_bytes() == b"Changed"
    approved = approve(runtime, action="append")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _: runtime.operations.scoped_promotion_apply(approved).result.category, range(2)))
    assert sorted(results) == ["already_applied", "appended"]
    assert target.read_bytes() == b"Changed" + approved.decision.markdown


@pytest.mark.parametrize("late_default_sync", [False, True])
def test_actual_scheduler_indexes_only_bound_space(tmp_path, monkeypatch, late_default_sync):
    import threading

    from app.services.semantic_search import SemanticSearchService
    from tests.test_space_runtime import backend as backend_fixture
    backend_fixture.__wrapped__(monkeypatch)
    indexed = threading.Event()
    wait_started = threading.Event()
    original = SemanticSearchService.sync_step

    def step(owner, *args, **kwargs):
        result = original(owner, *args, **kwargs)
        if result.indexed and any(c.path == SOURCE for c in owner.repository.load_chunks()):
            assert owner.vault_root.name == "research"
            if late_default_sync and not indexed.is_set():
                # Deterministically leave default work pending at the research
                # receipt. No sleep or assumption about which owner ran first.
                assert rt.registry._owners[SpaceId("default")].scheduler.request_full_sync()
                indexed.set()
                assert wait_started.wait(20)
            indexed.set()
        return result

    monkeypatch.setattr(SemanticSearchService, "sync_step", step)
    rt = compose_space_runtime(runtime_settings(tmp_path, "enabled")).start()
    original_wait = rt.scheduler.wait

    def wait_for_all(timeout):
        wait_started.set()
        return original_wait(timeout)

    monkeypatch.setattr(rt.scheduler, "wait", wait_for_all)
    try:
        assert capture(rt).committed
        assert indexed.wait(20)
        # A research batch receipt does not drain other owners' startup work.
        # In particular, default's WAL/SHM can still roll over between the bound
        # reader's identity probes. Assert isolation only after all work drains.
        rt.scheduler.wait(20)
        for entry in rt.scheduler._owners.values():
            status = rt.scheduler.status(entry.binding)
            assert not status.running and not status.requires_full_sync and status.error_type is None
        own = rt.registry._owners
        assert any(c.path == SOURCE for c in own[SpaceId("research")].semantic.repository.load_chunks())
        assert not own[SpaceId("default")].semantic.repository.load_chunks()
    finally:
        wait_started.set()
        rt.shutdown()


def test_root_failure_is_safe_before_write(runtime, monkeypatch):
    def unavailable(owner):
        raise OSError("private host path and raw failure")
    monkeypatch.setattr(VaultService, "root_binding_identity", unavailable)
    with pytest.raises(SpaceError, match="^unavailable_space$"):
        capture(runtime)


@pytest.mark.parametrize("operation", ["capture", "review"])
@pytest.mark.parametrize("interruption", [OSError, KeyboardInterrupt])
def test_fdopen_failure_disposes_actual_descriptor(runtime, monkeypatch, operation, interruption):
    import errno
    import os
    if operation == "review":
        assert capture(runtime).committed
    held = []

    def failed(fd, *args, **kwargs):
        assert kwargs["closefd"] is False
        held.append(fd)
        raise interruption("fdopen before transfer")

    monkeypatch.setattr(os, "fdopen", failed)
    if interruption is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt, match="fdopen before transfer"):
            capture(runtime) if operation == "capture" else review(runtime)
    elif operation == "capture":
        assert capture(runtime).category == "write_unavailable"
    else:
        with pytest.raises(PromotionError, match="unsafe_source"):
            review(runtime)
    assert len(held) == 1
    with pytest.raises(OSError) as caught:
        os.fstat(held[0])
    assert caught.value.errno == errno.EBADF
    runtime.shutdown()
    assert runtime._state == "stopped"
    assert not runtime.registry._owners[SpaceId("research")].vault._scoped_resources.pending
    root = runtime.registry._owners[SpaceId("research")].vault.vault_root
    assert not list(root.rglob("*.tmp"))
    if operation == "capture":
        assert not (root / SOURCE).exists()


@pytest.mark.parametrize("operation", ["capture", "review"])
@pytest.mark.parametrize("ambiguous", [False, True])
def test_fdopen_failed_cleanup_is_retained(tmp_path, monkeypatch, operation, ambiguous):
    import errno
    import os

    from app.core.resource_cleanup import CleanupState, DescriptorDisposal
    rt = compose_space_runtime(runtime_settings(tmp_path)).start()
    if operation == "review":
        assert capture(rt).committed
    owner = rt.registry._owners[SpaceId("research")].vault._scoped_resources
    original, close, prepare = os.fdopen, os.close, DescriptorDisposal._prepare_descriptor
    held, calls = [], []

    def failed(fd, *args, **kwargs):
        held.append(fd)
        raise OSError("primary fdopen failure")

    def safe(fd):
        if held and fd == held[0]:
            raise OSError("certified pre-dispatch failure")
        return prepare(fd)

    def opaque(fd):
        if held and fd == held[0]:
            calls.append(fd)
            raise OSError("opaque release failure")
        return close(fd)

    monkeypatch.setattr(os, "fdopen", failed)
    monkeypatch.setattr(DescriptorDisposal, "_prepare_descriptor", staticmethod(prepare if ambiguous else safe))
    if ambiguous:
        monkeypatch.setattr(os, "close", opaque)
    try:
        if operation == "capture":
            assert capture(rt).category == "write_unavailable"
        else:
            with pytest.raises(PromotionError, match="unsafe_source"):
                review(rt)
        monkeypatch.setattr(os, "fdopen", original)
        assert held and owner.pending
        state = next(x.resource for x in owner._leases if getattr(x.resource, "descriptor", None) == held[0])
        expected = CleanupState.AMBIGUOUS if ambiguous else CleanupState.VERIFIED_SAFE_TO_RETRY
        assert state.descriptor_release.state is expected
        os.fstat(held[0])
        with pytest.raises(OSError):
            rt.shutdown()
        assert rt._state == "stopping" and owner.pending
        os.fstat(held[0])
        monkeypatch.setattr(DescriptorDisposal, "_prepare_descriptor", staticmethod(prepare))
        monkeypatch.setattr(os, "close", close)
        if ambiguous:
            for _ in range(2):
                with pytest.raises(OSError):
                    rt.shutdown()
                assert rt._state == "stopping" and owner.pending
                os.fstat(held[0])
            assert calls == [held[0]]
            # Test fixture knows opaque() never dispatched; production does not.
            close(held[0])
        rt.shutdown()
        assert rt._state == "stopped" and not owner.pending
        with pytest.raises(OSError) as caught:
            os.fstat(held[0])
        assert caught.value.errno == errno.EBADF
        if operation == "capture":
            assert not (tmp_path / "research" / SOURCE).exists()
    finally:
        monkeypatch.setattr(os, "fdopen", original)
        monkeypatch.setattr(os, "close", close)
        monkeypatch.setattr(DescriptorDisposal, "_prepare_descriptor", staticmethod(prepare))
        rt.shutdown()


def test_fdopen_concurrent_shutdown_closes_once(tmp_path, monkeypatch):
    import errno
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor
    rt = compose_space_runtime(runtime_settings(tmp_path)).start()
    entered, release = threading.Event(), threading.Event()
    held, closed = [], []
    original = os.close

    def failed(fd, *args, **kwargs):
        held.append(fd)
        entered.set()
        assert release.wait(10)
        raise OSError("constructor failed during shutdown")

    def close(fd):
        if held and fd == held[0]:
            closed.append(fd)
        return original(fd)

    monkeypatch.setattr(os, "fdopen", failed)
    monkeypatch.setattr(os, "close", close)
    with ThreadPoolExecutor(max_workers=3) as pool:
        work = pool.submit(capture, rt)
        assert entered.wait(10)
        shutdowns = [pool.submit(rt.shutdown) for _ in range(2)]
        try:
            assert rt._state != "stopped"
            os.fstat(held[0])
        finally:
            release.set()
        with pytest.raises(SpaceError, match="operation_cancelled"):
            work.result(10)
        for stopped in shutdowns:
            stopped.result(10)
    assert rt._state == "stopped" and closed == held
    with pytest.raises(OSError) as caught:
        os.fstat(held[0])
    assert caught.value.errno == errno.EBADF


def test_fdopen_ambiguous_release_never_closes_reused_descriptor(tmp_path, monkeypatch):
    import os
    rt = compose_space_runtime(runtime_settings(tmp_path)).start()
    close, fdopen = os.close, os.fdopen
    held, witness, calls = [], [], []

    def failed(fd, *args, **kwargs):
        held.append(fd)
        raise OSError("fdopen failed")

    def reused(fd):
        if held and fd == held[0]:
            calls.append(fd)
            witness.append(os.dup(fd))
            close(fd)
            os.dup2(witness[0], fd)  # Same OFD, different descriptor allocation.
            raise OSError("opaque failure after release and reuse")
        return close(fd)

    monkeypatch.setattr(os, "fdopen", failed)
    monkeypatch.setattr(os, "close", reused)
    try:
        assert capture(rt).category == "write_unavailable"
        monkeypatch.setattr(os, "fdopen", fdopen)
        monkeypatch.setattr(os, "close", close)
        for _ in range(3):
            with pytest.raises(OSError):
                rt.shutdown()
            assert rt._state == "stopping"
            os.fstat(held[0])
            os.fstat(witness[0])
        assert calls == held
    finally:
        monkeypatch.setattr(os, "fdopen", fdopen)
        monkeypatch.setattr(os, "close", close)
        close(held[0])  # Fixture releases its own known replacement allocation.
        close(witness[0])
        rt.shutdown()
        assert rt._state == "stopped"


@pytest.mark.parametrize("action", ["create", "append"])
def test_promotion_write_fdopen_failure_disposes_descriptor(runtime, monkeypatch, action):
    import errno
    import os
    assert capture(runtime).committed
    root = runtime.registry._owners[SpaceId("research")].vault.vault_root
    target = root / "Notes/Target.md"
    if action == "append":
        target.write_bytes(b"Existing")
    approved = approve(runtime, action=action)
    original, held = os.fdopen, []

    def failed(fd, mode, **kwargs):
        if mode == ("wb" if action == "create" else "ab"):
            held.append(fd)
            raise OSError("write wrapper failure")
        return original(fd, mode, **kwargs)

    monkeypatch.setattr(os, "fdopen", failed)
    assert runtime.operations.scoped_promotion_apply(approved).result.category == "write_unavailable"
    assert held
    with pytest.raises(OSError) as caught:
        os.fstat(held[0])
    assert caught.value.errno == errno.EBADF
    assert not list(root.rglob("*.tmp"))
    if action == "create":
        assert not target.exists()
    else:
        assert target.read_bytes() == b"Existing"


@pytest.mark.parametrize("existing", [False, True])
def test_capture_read_fdopen_failure_is_owned(runtime, monkeypatch, existing):
    import errno
    import os
    if existing:
        assert capture(runtime).committed
    original, held = os.fdopen, []

    def failed(fd, mode, **kwargs):
        if mode == "rb":
            held.append(fd)
            raise OSError("capture read constructor failure")
        return original(fd, mode, **kwargs)

    monkeypatch.setattr(os, "fdopen", failed)
    expected = "write_unavailable" if existing else "commit_unknown"
    assert capture(runtime).category == expected
    for fd in held:
        with pytest.raises(OSError) as caught:
            os.fstat(fd)
        assert caught.value.errno == errno.EBADF
    assert held
    root = runtime.registry._owners[SpaceId("research")].vault.vault_root
    assert (root / SOURCE).is_file()  # Never remove a possibly committed capture.
    assert not list(root.rglob("*.tmp"))
