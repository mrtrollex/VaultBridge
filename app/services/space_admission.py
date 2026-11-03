"""Private operation admission; no scheduler, filesystem queue or owner locks."""

from __future__ import annotations

import threading
from contextlib import contextmanager

from app.services.knowledge_spaces import SpaceError
from app.services.scoped_budget import CancellationToken

MAX_WAITING_OPERATIONS = 64


class _OperationCancellation:
    def __init__(self, caller, stopped):
        self._caller = caller
        self._stopped = stopped

    def check(self):
        self._caller.check()
        if self._stopped.is_set():
            raise SpaceError("operation_cancelled")


class ScopedOperationAdmission:
    """One active operation and <=64 waiters, retaining no request payloads.

    Authorization happens in the operation before entering. The condition is
    released before owner/resource work. Scheduler callbacks never enter here.
    Caller cancellation is polled at <=50ms intervals while waiting; shutdown
    wakes all waiters immediately and also signals the active operation's token.
    Reentry is a programming error: delegation must enter exactly once.
    """

    def __init__(self):
        self._condition = threading.Condition()
        self._stopped = threading.Event()
        self._open = False
        self._active_thread = None
        self._waiting = 0

    def open(self):
        with self._condition:
            if self._stopped.is_set():
                raise SpaceError("operation_cancelled")
            self._open = True
            self._condition.notify_all()

    def begin_shutdown(self):
        with self._condition:
            self._open = False
            self._stopped.set()
            self._condition.notify_all()

    def drain(self):
        with self._condition:
            if self._active_thread == threading.get_ident():
                raise RuntimeError("Cannot drain admission from an active operation")
            self._condition.wait_for(lambda: self._active_thread is None and self._waiting == 0)

    @contextmanager
    def operation(self, cancel=None):
        token = _OperationCancellation(CancellationToken() if cancel is None else cancel, self._stopped)
        tid = threading.get_ident()
        with self._condition:
            token.check()
            if not self._open:
                raise SpaceError("unavailable_space")
            if self._active_thread == tid:
                raise RuntimeError("Nested scoped operation admission")
            if self._active_thread is not None:
                if self._waiting >= MAX_WAITING_OPERATIONS:
                    raise SpaceError("unavailable_space")
                self._waiting += 1
                self._condition.notify_all()
                try:
                    while self._active_thread is not None:
                        token.check()
                        self._condition.wait(0.05)
                    token.check()
                finally:
                    self._waiting -= 1
                    self._condition.notify_all()
            self._active_thread = tid
        try:
            token.check()
            yield token
            token.check()
        finally:
            with self._condition:
                self._active_thread = None
                self._condition.notify_all()
