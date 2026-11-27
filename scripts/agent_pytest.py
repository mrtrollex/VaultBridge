"""Fail managed pytest sessions when xdist reports an unexpected worker exit."""

import pytest

_WORKER_CRASHED = pytest.StashKey[bool]()


def pytest_testnodedown(node, error: object | None) -> None:
    if error is not None:
        node.config.stash[_WORKER_CRASHED] = True


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_sessionfinish(session: pytest.Session):
    result = yield
    # A worker can die before it owns a test, leaving pytest with no failure report.
    if session.config.stash.get(_WORKER_CRASHED, False) and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    return result
