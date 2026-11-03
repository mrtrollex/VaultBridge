"""Real native reproductions for the three post-R9 ownership findings."""

import errno
import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.knowledge_spaces import QualifiedPath, SpaceError, SpaceId
from app.services.space_runtime import compose_space_runtime
from app.services.vault import NoteUnavailableError
from tests.test_space_runtime import backend as backend
from tests.test_space_runtime import binding, configuration
from tests.test_space_runtime_resource_owners import shutdown


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="real Linux inotify descriptor allocations")
@pytest.mark.parametrize("replacement", ["same_ofd", "different_inotify", "final_guard"])
def test_linux_interrupted_release_preserves_replacement(tmp_path, monkeypatch, replacement):
    from app.services.watcher_ingress_linux import _BoundedInotify

    root = tmp_path / "root"
    root.mkdir()
    native = _BoundedInotify(os.fsencode(root), loss=lambda: None)
    peer = _BoundedInotify(os.fsencode(root), loss=lambda: None)
    original = native._descriptors["_inotify_fd"]
    held = original.guard if replacement == "final_guard" else original
    witness = os.dup(held.number)
    close = os.close
    calls = []

    def interrupted(fd):
        if fd == held.number:
            calls.append(fd)
            close(fd)
            os.dup2(peer._inotify_fd if replacement == "different_inotify" else witness, fd)
            raise KeyboardInterrupt("closed and reused before interruption")
        return close(fd)

    try:
        monkeypatch.setattr(os, "close", interrupted)
        with pytest.raises(KeyboardInterrupt):
            native._dispose("test", held, guarded=replacement != "final_guard")
        monkeypatch.setattr(os, "close", close)
        for _ in range(3):
            with pytest.raises(OSError):
                native._dispose("test", held, guarded=replacement != "final_guard")
            os.fstat(held.number)
        assert calls == [held.number] and not native._resources_closed
    finally:
        monkeypatch.setattr(os, "close", close)
        close(witness)
        # Only the fixture owns and can release the replacement allocation.
        try:
            os.fstat(held.number)
        except OSError:
            pass
        else:
            close(held.number)
        native.close_after_reader()
        peer.close_after_reader()


def test_missing_note_allocation_has_no_phantom_debt(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, 1, disabled=("default",))).start()
    vault = binding(runtime)._owners.vault
    note = tmp_path / "default" / "same.md"
    verify = vault.verify_existing_markdown_path_result
    state = SimpleNamespace(remove=True)

    def verified(*args, **kwargs):
        result = verify(*args, **kwargs)
        if state.remove:
            state.remove = False
            note.unlink()
        return result

    monkeypatch.setattr(vault, "verify_existing_markdown_path_result", verified)
    try:
        with pytest.raises(NoteUnavailableError) as error:
            vault.read_verified_markdown_snapshot("same.md")
        print("native allocation failure", repr(error.value.__cause__))
        assert not vault._scoped_resources.pending
        note.write_text("recreated", encoding="utf-8")
        assert vault.read_verified_markdown_snapshot("same.md").content == "recreated"
        runtime.shutdown()
        assert runtime._state == "stopped" and runtime.resource is None
    finally:
        # Pre-fix reproduction cleanup only: the fixture knows no allocation
        # was returned by the failing native call, so empty shells own nothing.
        for lease in tuple(vault._scoped_resources._leases):
            item = lease.resource
            if getattr(item, "number", None) is None and getattr(item, "descriptor", None) is None:
                vault._scoped_resources._forget(lease)
        shutdown(runtime)


@pytest.mark.parametrize("operation", ["read_note", "outgoing", "backlinks"])
def test_qualified_reader_failed_close_is_runtime_owned(tmp_path, backend, monkeypatch, operation):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    vault = binding(runtime)._owners.vault
    peer = tmp_path / "space-1" / "same.md"
    peer_bytes = peer.read_bytes()
    opened, fdopen = io.open, os.fdopen
    state = SimpleNamespace(reader=None, fd=None, fail=True)

    class Reader(io.TextIOWrapper):
        def read(self, *args):
            return self.buffer.read(*args) if self.binary_result else super().read(*args)

        def close(self):
            if state.fail:
                raise OSError("reader close failed before native release")
            return super().close()

    def tracked(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, Path)) and Path(file) == tmp_path / "default" / "same.md":
            reader = Reader(io.FileIO(file, "r"), encoding="utf-8")
            reader.binary_result = False
            state.reader, state.fd = reader, reader.fileno()
            return reader
        return opened(file, mode, *args, **kwargs)

    def tracked_fd(fd, *args, **kwargs):
        reader = Reader(io.FileIO(fd, "r", closefd=False), encoding="utf-8")
        reader.binary_result = True
        state.reader, state.fd = reader, fd
        return reader

    monkeypatch.setattr(io, "open", tracked)
    monkeypatch.setattr(os, "fdopen", tracked_fd)
    try:
        with pytest.raises((SpaceError, NoteUnavailableError)):
            getattr(runtime.operations, operation)(QualifiedPath(SpaceId("default"), "same.md"))
        assert state.reader is not None and not state.reader.closed
        os.fstat(state.fd)
        assert vault._scoped_resources.pending
        for _ in range(2):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == "stopping" and not state.reader.closed
            os.fstat(state.fd)
        state.fail = False
        runtime.shutdown()
        assert runtime._state == "stopped" and state.reader.closed
        with pytest.raises(OSError) as error:
            os.fstat(state.fd)
        assert error.value.errno == errno.EBADF
        assert peer.read_bytes() == peer_bytes
    finally:
        state.fail = False
        monkeypatch.setattr(io, "open", opened)
        monkeypatch.setattr(os, "fdopen", fdopen)
        if state.reader is not None:
            state.reader.close()
        shutdown(runtime)


def test_certified_missing_descriptor_does_not_retire_other_allocations(tmp_path, backend):
    runtime = compose_space_runtime(configuration(tmp_path, 1, disabled=("default",))).start()
    owner = binding(runtime)._owners.vault._scoped_resources
    path = tmp_path / "default" / "same.md"
    with owner.descriptor(os.open, path, os.O_RDONLY) as lease:
        fd = lease.resource.descriptor
        assert not os.get_inheritable(fd)
        with pytest.raises(FileNotFoundError) as error:
            owner.open_descriptor(os.open, tmp_path / "absent", os.O_RDONLY)
        assert error.value.errno == errno.ENOENT
        assert owner._leases == {lease}
        os.fstat(fd)
    assert not owner.pending
    runtime.shutdown()
    assert runtime._state == "stopped"
    with pytest.raises(OSError) as error:
        os.fstat(fd)
    assert error.value.errno == errno.EBADF


def test_opaque_allocation_interruption_is_not_a_no_allocation_receipt(tmp_path, backend):
    runtime = compose_space_runtime(configuration(tmp_path, 1, disabled=("default",))).start()
    owner = binding(runtime)._owners.vault._scoped_resources
    captured = []

    def interrupted():
        captured.append(os.open(tmp_path / "default" / "same.md", os.O_RDONLY))
        raise KeyboardInterrupt("allocation succeeded without returning ownership")

    try:
        with pytest.raises(KeyboardInterrupt):
            owner.open_descriptor(interrupted)
        assert len(captured) == 1 and owner.pending
        for _ in range(3):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            os.fstat(captured[0])
            assert runtime._state == "stopping" and len(captured) == 1
    finally:
        os.close(captured[0])
        # Only this fixture has the missing acquisition receipt.
        for lease in tuple(owner._leases):
            owner._forget(lease)
        runtime.shutdown()


def test_qualified_and_legacy_note_output_remains_equal(tmp_path, backend):
    runtime = compose_space_runtime(configuration(tmp_path, 2, disabled=("default", "space-1"))).start()
    vault = binding(runtime)._owners.vault
    target = tmp_path / "default" / "same.md"
    target.write_bytes("\ufeffa\r\nb\rc\nSlovenský text".encode())
    expected = vault.read_note("same.md")
    result = runtime.operations.read_note(QualifiedPath(SpaceId("default"), "same.md"))
    assert result.note == expected and "\r" not in result.note.content
    assert result.identity.canonical_relative_path == "same.md"
    assert not vault._scoped_resources.pending
    runtime.shutdown()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="real Linux watcher shutdown ownership")
@pytest.mark.parametrize("safe", [False, True])
def test_linux_runtime_shutdown_uses_original_generation_evidence(tmp_path, backend, monkeypatch, safe):
    import threading

    from app.core.resource_cleanup import CleanupState
    from tests.test_semantic_resources import launch

    runtime = compose_space_runtime(configuration(tmp_path, 1, watch=True)).start()
    runtime.scheduler.wait(20)
    watcher = runtime.watcher
    producer = watcher._producers[0]
    native = producer._inotify._inotify
    held = native._descriptors["_inotify_fd"]
    close, identity = os.close, native._descriptor_identity
    witness = os.dup(held.number)
    calls, thread_errors = [], []
    entered, proceed, other_started = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(threading, "excepthook", lambda args: thread_errors.append(args.exc_type))

    def failed(fd):
        if fd == held.number:
            calls.append(fd)
            close(fd)
            os.dup2(witness, fd)
            raise KeyboardInterrupt("released and replaced watcher descriptor")
        return close(fd)

    def preflight(fd):
        if fd == held.number:
            raise OSError("read-only native descriptor preflight failed")
        return identity(fd)

    try:
        if safe:
            monkeypatch.setattr(native, "_descriptor_identity", preflight)
        else:
            monkeypatch.setattr(os, "close", failed)
        with pytest.raises((KeyboardInterrupt, OSError, RuntimeError)):
            runtime.shutdown()
        assert runtime._state == "stopping" and watcher._observer is not None
        assert held.release.state is (CleanupState.VERIFIED_SAFE_TO_RETRY if safe else CleanupState.AMBIGUOUS)
        monkeypatch.setattr(native, "_descriptor_identity", identity)
        monkeypatch.setattr(os, "close", close)
        if not safe:
            for _ in range(2):
                with pytest.raises((OSError, RuntimeError)):
                    runtime.shutdown()
                os.fstat(held.number)
            assert calls == [held.number]
            close(held.number)  # Fixture-owned replacement, not the original.
        close(witness)
        witness = None

        def blocked(fd):
            if fd == held.number:
                entered.set()
                assert proceed.wait(20)
            return identity(fd)

        monkeypatch.setattr(native, "_descriptor_identity", blocked)
        work_calls = len(backend.calls)
        first = launch(runtime.shutdown)
        assert entered.wait(20)

        def other():
            other_started.set()
            runtime.shutdown()

        second = launch(other)
        assert other_started.wait(20)
        proceed.set()
        first.result(20)
        second.result(20)
        assert runtime._state == "stopped" and native._resources_closed
        assert watcher._observer is None and runtime.scheduler._watcher_token is None
        assert len(backend.calls) == work_calls
        assert all(issubclass(error, (KeyboardInterrupt, OSError)) for error in thread_errors)
    finally:
        monkeypatch.setattr(native, "_descriptor_identity", identity)
        monkeypatch.setattr(os, "close", close)
        proceed.set()
        if witness is not None:
            close(witness)
        shutdown(runtime)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="real Linux partial native acquisition")
@pytest.mark.parametrize("allocation", ["inotify", "pipe"])
def test_interrupted_native_acquisition_retains_unknown_owner(tmp_path, backend, monkeypatch, allocation):
    from app.services import watcher_ingress_linux as module

    runtime = compose_space_runtime(configuration(tmp_path, 1, watch=True))
    init, pipe = module.inotify_init, os.pipe
    captured = []

    def interrupted():
        captured.extend([init()] if allocation == "inotify" else pipe())
        raise KeyboardInterrupt("native acquisition completed without returning ownership")

    if allocation == "inotify":
        monkeypatch.setattr(module, "inotify_init", interrupted)
    else:
        monkeypatch.setattr(os, "pipe", interrupted)
    try:
        with pytest.raises(KeyboardInterrupt):
            runtime.start()
        assert captured and runtime._state == "stopping"
        native = runtime.watcher._producers[0]._inotify._inotify
        assert not native._resources_closed
        unknown = tuple(d for d in native._descriptors.values() if d.number is None)
        assert len(unknown) == (1 if allocation == "inotify" else 2)
        for _ in range(2):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == "stopping"
            for fd in captured:
                os.fstat(fd)
    finally:
        monkeypatch.setattr(module, "inotify_init", init)
        monkeypatch.setattr(os, "pipe", pipe)
        for fd in captured:
            os.close(fd)  # Only the fixture has the unreturned acquisition receipt.
        for producer in runtime.watcher._producers:
            native = producer._inotify._inotify
            for key, descriptor in tuple(native._descriptors.items()):
                if descriptor.number is None:
                    del native._descriptors[key]  # Test-only verified external disposition.
        shutdown(runtime)


@pytest.mark.parametrize("interruption", [OSError, KeyboardInterrupt])
def test_qualified_native_close_ambiguity_preserves_same_object_reuse(tmp_path, backend, monkeypatch, interruption):
    runtime = compose_space_runtime(configuration(tmp_path, 1, disabled=("default",))).start()
    vault = binding(runtime)._owners.vault
    fdopen, close = os.fdopen, os.close
    state = SimpleNamespace(fd=None, witness=None, calls=0)

    def stream(fd, *args, **kwargs):
        state.fd, state.witness = fd, os.dup(fd)
        return fdopen(fd, *args, **kwargs)

    def interrupted(fd):
        if fd == state.fd:
            state.calls += 1
            close(fd)
            os.dup2(state.witness, fd)
            raise interruption("original released and allocation number reused for same object")
        return close(fd)

    monkeypatch.setattr(os, "fdopen", stream)
    monkeypatch.setattr(os, "close", interrupted)
    try:
        with pytest.raises((SpaceError, KeyboardInterrupt)):
            runtime.operations.read_note(QualifiedPath(SpaceId("default"), "same.md"))
        assert vault._scoped_resources.pending and state.calls == 1
        monkeypatch.setattr(os, "close", close)
        for _ in range(3):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == "stopping" and state.calls == 1
            os.fstat(state.fd)
        assert runtime.scheduler is runtime.resource is runtime.watcher is None
    finally:
        monkeypatch.setattr(os, "close", close)
        monkeypatch.setattr(os, "fdopen", fdopen)
        if state.fd is not None:
            close(state.fd)  # Fixture-owned replacement; never a controller retry.
        if state.witness is not None:
            close(state.witness)
        runtime.shutdown()
        assert runtime._state == "stopped" and not vault._scoped_resources.pending
