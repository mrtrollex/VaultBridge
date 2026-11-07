"""Transport cancellation signals the domain token and drains the same owner."""

import asyncio
from contextvars import ContextVar

from app.services.scoped_budget import CancellationToken

current_read_cancellation = ContextVar("vaultbridge_read_cancellation", default=None)


async def run_async_read(action, *, disconnected=None, cancel=None):
    token = CancellationToken() if cancel is None else cancel
    context = current_read_cancellation.set(token)
    try:
        work = asyncio.create_task(action())
    finally:
        current_read_cancellation.reset(context)
    return await _drain(work, token, disconnected)


async def run_read(action, *, disconnected=None, cancel=None):
    token = CancellationToken() if cancel is None else cancel
    work = asyncio.create_task(asyncio.to_thread(action, token))
    return await _drain(work, token, disconnected)


async def _drain(work, token, disconnected):
    connection_closed = False
    try:
        while not work.done():
            await asyncio.wait((work,), timeout=0.05)
            if disconnected is not None and await disconnected():
                connection_closed = True
                token.cancel()
        if connection_closed:
            # Drain first, then let the disconnected transport end without a
            # success/error response. Cancellation never publishes read evidence.
            try:
                work.result()
            except Exception:
                pass
            raise asyncio.CancelledError()
        return work.result()
    except asyncio.CancelledError:
        token.cancel()
        try:
            await asyncio.shield(work)
        except Exception:
            pass
        raise
