"""R12 registration, session attribution, inspection and event allocation boundaries."""
import os
import shutil
import sqlite3
import sys
from contextlib import ExitStack, closing, contextmanager

import pytest

from app.services.knowledge_spaces import SpaceId
from app.services.scoped_budget import CancellationToken, ScopedBudget
from app.services.space_runtime import compose_space_runtime
from tests.test_space_runtime import backend as backend
from tests.test_space_runtime import binding, configuration


@contextmanager
def counted_primitives(monkeypatch):
    calls = []
    with monkeypatch.context() as patches:
        for name in ('open', 'fstat', 'dup', 'stat', 'lstat'):
            function = getattr(os, name)

            def counted(*args, _function=function, _name=name, **kwargs):
                calls.append(_name)
                return _function(*args, **kwargs)

            patches.setattr(os, name, counted)
        if os.name == 'nt':
            import ctypes
            library = ctypes.WinDLL
            covered = {'CreateFileW', 'GetFileInformationByHandle', 'GetHandleInformation', 'DuplicateHandle'}

            class Call:
                def __init__(self, function, name):
                    object.__setattr__(self, 'function', function)
                    object.__setattr__(self, 'name', name)

                def __call__(self, *args):
                    calls.append(self.name)
                    return self.function(*args)

                def __getattr__(self, name):
                    return getattr(self.function, name)

                def __setattr__(self, name, value):
                    setattr(self.function, name, value)

            class Library:
                def __init__(self, *args, **kwargs):
                    self.library = library(*args, **kwargs)

                def __getattr__(self, name):
                    function = getattr(self.library, name)
                    return Call(function, name) if name in covered else function

            patches.setattr(ctypes, 'WinDLL', Library)
        yield calls


def test_disabled_default_rejects_foreign_namespace(tmp_path, backend):
    source = compose_space_runtime(configuration(tmp_path / 'source')).start()
    source.scheduler.wait(20)
    foreign = binding(source, 'space-1')._owners.semantic.db_path
    source.shutdown()
    settings = configuration(tmp_path / 'target', 1, disabled=('default',))
    target = tmp_path / 'target/default-data/semantic-index.sqlite3'
    target.parent.mkdir()
    shutil.copyfile(foreign, target)
    original = target.read_bytes()
    runtime = compose_space_runtime(settings).start()
    try:
        storage = binding(runtime)._owners.semantic._inspect.__self__.repository.read_immutable_status()
        print('storage', storage)
        assert storage.storage_error and not storage.storage_initialized
        status = runtime.operations.inspect_index()
        print('inspection', status)
        assert status.result == 'storage_unavailable'
        assert target.read_bytes() == original
        assert runtime.resource is None and runtime.scheduler is None and runtime.watcher is None
    finally:
        runtime.shutdown()


@pytest.mark.skipif(sys.platform != 'win32', reason='real Windows event sentinel')
def test_real_event_failure_has_no_phantom_owner():
    from watchdog.observers import winapi

    from app.core.resource_cleanup import NativeHandles
    owner = NativeHandles()
    try:
        with pytest.raises(OSError) as caught:
            owner.acquire_handle(winapi.CreateEvent, None, True, False, 'invalid\\event\\name')
        assert caught.value.winerror == 3
        assert not owner.pending
        owner.drain()
    finally:
        # Fixture alone knows this actual native call allocated nothing.
        for lease in tuple(owner._leases):
            assert lease.resource.number is None
            owner._forget(lease)


@pytest.mark.parametrize('reverse', [False, True])
def test_retained_sessions_charge_only_actual_space(tmp_path, backend, reverse, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path, disabled=('default', 'space-1'))).start()
    ids = tuple(SpaceId(name) for name in ('default', 'space-1'))
    budget, token = ScopedBudget(ids), CancellationToken()
    owners = {sid: binding(runtime, sid.value)._owners for sid in ids}
    sessions = {sid: owners[sid].vault.scoped_session(space_id=sid, budget=budget, cancel=token,
                root_identity=owners[sid]._root_identity) for sid in ids}
    try:
        with ExitStack() as stack:
            for sid in reversed(ids) if reverse else ids:
                stack.enter_context(sessions[sid])
            for sid in (*ids, *reversed(ids)):
                fact = sessions[sid].discover().facts[0]
                for operation in (lambda: sessions[sid].verify(fact), sessions[sid].verify_root):
                    before = {key: budget.used[key]['probes'] for key in ids}
                    with counted_primitives(monkeypatch) as calls:
                        operation()
                    delta = {key: budget.used[key]['probes'] - before[key] for key in ids}
                    print('space', sid, 'charged', delta)
                    assert delta[sid] == len(calls) > 0
                    assert all(delta[key] == 0 for key in ids if key != sid)
            # Real missing-note acquisition failure, followed by cancellation,
            # while both sessions remain retained. Neither may borrow peer credit.
            from app.services.knowledge_spaces import SpaceError
            sid = ids[0]
            before = {key: budget.used[key]['probes'] for key in ids}
            note = owners[sid].vault.vault_root / 'same.md'
            fact = sessions[sid].discover().facts[0]
            note.unlink()
            with pytest.raises(OSError):
                sessions[sid].verify(fact)
            note.write_text('restored', encoding='utf-8')
            assert budget.used[ids[1]]['probes'] == before[ids[1]]
            before = dict(budget.total)
            token.cancel()
            with pytest.raises(SpaceError):
                sessions[sid].verify_root()
            assert budget.total == before
        assert not any(budget.reserved.values())
    finally:
        runtime.shutdown()


@pytest.mark.skipif(sys.platform != 'linux', reason='real Linux registration descriptors')
@pytest.mark.parametrize('raises', [False, True])
def test_registration_iterator_survives_failed_startup(tmp_path, backend, monkeypatch, raises):
    from app.services.watcher_ingress_linux import _BoundedInotify
    settings = configuration(tmp_path, 1, watch=True)
    root = tmp_path / 'default'
    scan, init, captured, natives = os.scandir, _BoundedInotify.__init__, [], []

    class FailedScan:
        def __init__(self, iterator):
            self.iterator, self.attempts = iterator, 0

        def __iter__(self):
            return self

        def __next__(self):
            raise OSError('registration iteration failed')

        def close(self):
            self.attempts += 1
            if raises:
                raise OSError('registration disposal outcome unknown')
            return None  # Opaque success without disposing the real iterator is not proof.

    def failed(path):
        iterator = scan(path)
        if os.fsdecode(path) == str(root):
            item = FailedScan(iterator)
            captured.append(item)
            return item
        return iterator

    def published(native, *args, **kwargs):
        natives.append(native)
        return init(native, *args, **kwargs)

    runtime = compose_space_runtime(settings)
    monkeypatch.setattr(os, 'scandir', failed)
    monkeypatch.setattr(_BoundedInotify, '__init__', published)
    try:
        with pytest.raises((OSError, RuntimeError)):
            runtime.start()
        assert captured and natives
        for _ in range(2):
            with pytest.raises((OSError, RuntimeError)):
                runtime.shutdown()
            assert runtime._state == 'stopping'
            assert not natives[0]._resources_closed
            assert all(record.state in ('closed', 'absent', 'replaced')
                       for record in natives[0]._descriptors.values())
            assert any(lease.resource is captured[0]
                       for lease in natives[0]._registration_iterators._leases)
        assert captured[0].attempts == 1  # Opaque side effects cannot authorize a retry.
        assert any(os.readlink('/proc/self/fd/' + fd) == str(root)
                   for fd in os.listdir('/proc/self/fd') if os.path.exists('/proc/self/fd/' + fd))
    finally:
        # Only fixture has proof of the real iterator's current ownership.
        for item in captured:
            item.iterator.close()
        if hasattr(natives[0], '_registration_iterators'):
            for lease in tuple(natives[0]._registration_iterators._leases):
                natives[0]._registration_iterators._forget(lease)
        runtime.shutdown()


@pytest.mark.parametrize('name', ['default', 'space-1'])
@pytest.mark.parametrize('metadata', ['legacy', 'valid', 'foreign', 'partial', 'version', 'root'])
def test_disabled_inspection_namespace_rules_are_read_only(tmp_path, backend, name, metadata):
    from app.repositories.semantic_binding import BINDING_KEYS, ExpectedIndexBinding
    source = compose_space_runtime(configuration(tmp_path / 'source')).start()
    source.scheduler.wait(20)
    path = binding(source, 'space-1')._owners.semantic.db_path
    source.shutdown()
    settings = configuration(tmp_path / 'target', disabled=('default', 'space-1'))
    target = tmp_path / 'target' / (name + '-data') / 'semantic-index.sqlite3'
    target.parent.mkdir()
    shutil.copyfile(path, target)
    root = tmp_path / 'target' / name
    expected = (ExpectedIndexBinding._for_default(root) if name == 'default' else
                ExpectedIndexBinding(name, root))
    with closing(sqlite3.connect(target)) as connection:
        connection.executemany('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)',
                               zip(BINDING_KEYS, expected.values, strict=True))
        if metadata == 'legacy':
            connection.executemany('DELETE FROM meta WHERE key=?', [(key,) for key in BINDING_KEYS])
        elif metadata == 'foreign':
            connection.execute('UPDATE meta SET value=? WHERE key=?', ('foreign', BINDING_KEYS[1]))
        elif metadata == 'partial':
            connection.execute('DELETE FROM meta WHERE key=?', (BINDING_KEYS[0],))
        elif metadata in ('version', 'root'):
            connection.execute('UPDATE meta SET value=? WHERE key=?',
                               ('invalid', BINDING_KEYS[0 if metadata == 'version' else 2]))
        connection.commit()
    original, loads = target.read_bytes(), len(backend.instances)
    runtime = compose_space_runtime(settings).start()
    try:
        facade = binding(runtime, name)._owners.semantic
        for _ in range(2):
            status = facade._inspect.__self__.repository.read_immutable_status()
            accepted = metadata == 'valid' or (metadata == 'legacy' and name == 'default')
            assert status.storage_initialized == accepted
            assert status.storage_error != accepted
            if not accepted:
                assert status.index_signature is None
                assert runtime.operations.inspect_index(SpaceId(name)).result == 'storage_unavailable'
        if name == 'default':
            from app.repositories.semantic import ImmutableIndexInspectionUnavailableError
            with pytest.raises(ImmutableIndexInspectionUnavailableError):
                facade._inspect.__self__.repository._repository(target).get_metadata('index_state')
        assert target.read_bytes() == original
        assert not list(target.parent.glob('semantic-index.sqlite3-*'))
        assert len(backend.instances) == loads
        assert runtime.resource is None and runtime.scheduler is None and runtime.watcher is None
    finally:
        runtime.shutdown()
    target.unlink()  # Real readers released on both Windows and Linux.


@pytest.mark.skipif(sys.platform != 'win32', reason='real Windows event startup rollback')
def test_event_certified_failure_and_opaque_acquisition_stay_distinct(tmp_path, backend, monkeypatch):
    from watchdog.observers import winapi

    from app.core.resource_cleanup import NativeHandles, event_allocation
    from app.services import watcher_ingress_windows as module
    create = winapi.CreateEvent
    runtime = compose_space_runtime(configuration(tmp_path, 1, watch=True))

    def absent(*args):
        return event_allocation(create, None, True, False, 'invalid\\event\\name')

    monkeypatch.setattr(module.winapi, 'CreateEvent', absent)
    with pytest.raises(OSError) as caught:
        runtime.start()
    assert caught.value.winerror == 3
    runtime.shutdown()
    assert runtime._state == 'stopped' and runtime.watcher._observer is None
    owner, handles = NativeHandles(), []

    def interrupted():
        handles.append(create(None, True, False, None))
        raise KeyboardInterrupt()

    try:
        with pytest.raises(KeyboardInterrupt):
            owner.acquire_handle(interrupted)
        for _ in range(2):
            with pytest.raises(OSError):
                owner.drain()
            assert owner.pending and NativeHandles.valid(handles[0])
    finally:
        for handle in handles:
            winapi.CloseHandle(handle)
        for lease in tuple(owner._leases):
            owner._forget(lease)  # Fixture has the unreturned allocation's receipt.


@pytest.mark.skipif(sys.platform != 'linux', reason='real Linux iterator safe retry')
def test_registration_iterator_pre_dispatch_retry_closes_original(tmp_path, monkeypatch):
    from app.services.watcher_ingress_linux import _BoundedInotify
    from tests.test_semantic_resources import launch
    native = _BoundedInotify(os.fsencode(tmp_path), loss=lambda: None)
    lease, _ = native._own_iterator(os.fsencode(tmp_path))
    original = lease.resource
    descriptors = [int(fd) for fd in os.listdir('/proc/self/fd')
                   if os.path.exists('/proc/self/fd/' + fd)
                   and os.readlink('/proc/self/fd/' + fd) == str(tmp_path)]
    assert len(descriptors) == 1
    attempts = []

    def prepare(iterator):
        attempts.append(iterator)
        raise OSError('before release dispatch')

    monkeypatch.setattr(native, '_prepare_iterator_close', prepare)
    with pytest.raises(OSError):
        native._registration_iterators.release(lease)
    with pytest.raises(OSError):
        native.close_after_reader()
    assert not native._resources_closed and lease.resource is original
    os.fstat(descriptors[0])
    monkeypatch.setattr(native, '_prepare_iterator_close', lambda iterator: attempts.append(iterator))
    tasks = [launch(native.close_after_reader) for _ in range(2)]
    for task in tasks:
        task.result(10)
    assert len(attempts) == 3 and all(item is original for item in attempts)
    assert native._resources_closed and not native._registration_iterators.pending
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


@pytest.mark.parametrize('reverse', [False, True])
def test_real_semantic_federation_keeps_session_cleanup_local(tmp_path, backend, monkeypatch, reverse):
    from app.services._scoped_vault import ScopedVaultSession
    from app.services.knowledge_spaces import ReadScope
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    original, measured = ScopedVaultSession.verify, []

    def checked(session, fact):
        before = {sid: counts['probes'] for sid, counts in session.budget.used.items()}
        try:
            return original(session, fact)
        finally:
            delta = {sid: counts['probes'] - before[sid] for sid, counts in session.budget.used.items()}
            measured.append((session.sid, delta))
            assert delta[session.sid] > 0
            assert all(value == 0 for sid, value in delta.items() if sid != session.sid)

    monkeypatch.setattr(ScopedVaultSession, 'verify', checked)
    ids = (SpaceId('default'), SpaceId('space-1'))
    try:
        result = runtime.operations.scoped_semantic_search('notes', ReadScope(ids[::-1] if reverse else ids))
        assert measured and {item.identity.space_id for item in result.items} == set(ids)
        assert all(row.state == 'complete' for row in result.coverage)
    finally:
        runtime.shutdown()
