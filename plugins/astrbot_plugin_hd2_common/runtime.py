"""Bounded blocking work and request-scoped diagnostics, without framework imports."""
import asyncio
import contextvars
import functools
import weakref
from uuid import uuid4

request_id = contextvars.ContextVar("hd2_request_id", default="")
_limits = weakref.WeakKeyDictionary()
_shared = weakref.WeakKeyDictionary()
_wiki_limits = weakref.WeakKeyDictionary()


async def singleflight(key, factory):
    """Share in-flight work only; a waiter cancellation never cancels its producer."""
    jobs = _shared.setdefault(asyncio.get_running_loop(), {})
    task = jobs.get(key)
    if task is None:
        task = jobs[key] = asyncio.create_task(factory())
        def finished(done):
            if jobs.get(key) is done:
                jobs.pop(key, None)
            if not done.cancelled():
                done.exception()  # retrieve errors even when every waiter has left
        task.add_done_callback(finished)
    return await asyncio.shield(task)


async def wiki_blocking(fn, *args, **kwargs):
    limit = _wiki_limits.setdefault(asyncio.get_running_loop(), asyncio.Semaphore(2))
    async with limit:
        return await blocking(fn, *args, **kwargs)


async def blocking(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    limit = _limits.setdefault(loop, asyncio.Semaphore(3))
    async with limit:
        # Keep the slot until a cancelled thread actually exits.
        task = asyncio.create_task(asyncio.to_thread(functools.partial(fn, *args, **kwargs)))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            finally:
                raise


def new_request(event=None):
    ident = getattr(event, "_hd2_request_id", "") if event is not None else ""
    if not ident:
        ident = uuid4().hex
        if event is not None:
            event._hd2_request_id = ident
    return request_id.set(ident)
