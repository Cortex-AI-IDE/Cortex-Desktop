# ------------------------------------------------------------
# stream.py
# Python conversion of stream.ts (lines 1-77)
#
# Async iterator stream class with queue buffering:
# - One-shot async iteration (can only be iterated once)
# - Buffered enqueue with automatic read resolution
# - Done signal with pending-read resolution
# - Error propagation to pending readers
# - Optional cleanup callback on return()
# ------------------------------------------------------------

import asyncio
from typing import Any, AsyncIterator, Callable, Generic, Optional, TypeVar


T = TypeVar("T")


class Stream(AsyncIterator[T], Generic[T]):
    """
    Async iterator stream with queue buffering.

    Supports:
    - Buffered writes via enqueue()
    - Done signal via done()
    - Error propagation via error()
    - Optional cleanup callback on return()
    - One-shot iteration (raises if iterated twice)
    """

    def __init__(self, returned: Optional[Callable[[], None]] = None):
        self._queue: asyncio.Queue[T] = asyncio.Queue()
        self._read_resolve: Optional[Callable[[T], None]] = None
        self._read_reject: Optional[Callable[[Exception], None]] = None
        self._is_done: bool = False
        self._has_error: Any = None
        self._started: bool = False
        self._returned_fn: Optional[Callable[[], None]] = returned

    async def __anext__(self) -> T:
        if self._started:
            raise RuntimeError("Stream can only be iterated once")
        self._started = True

        # Wait for a value, error, or done signal
        if self._is_done:
            raise StopAsyncIteration
        if self._has_error:
            raise self._has_error

        if not self._queue.empty():
            return self._queue.get_nowait()

        future: asyncio.Future = asyncio.get_running_loop().create_future()

        def resolve(value: T) -> None:
            if not future.done():
                future.set_result(value)

        def reject(error: Exception) -> None:
            if not future.done():
                future.set_exception(error)

        self._read_resolve = resolve
        self._read_reject = reject

        try:
            result = await future
            # If done() resolved the future, raise StopAsyncIteration
            # to signal end of iteration (matches TS { done: true })
            if self._is_done:
                raise StopAsyncIteration
            return result
        finally:
            self._read_resolve = None
            self._read_reject = None

    def __aiter__(self) -> AsyncIterator[T]:
        if self._started:
            raise RuntimeError("Stream can only be iterated once")
        self._started = True
        return self

    def enqueue(self, value: T) -> None:
        if self._is_done:
            return

        if self._read_resolve is not None:
            resolve = self._read_resolve
            self._read_resolve = None
            self._read_reject = None
            resolve(value)
        else:
            self._queue.put_nowait(value)

    def done(self) -> None:
        self._is_done = True
        if self._read_resolve is not None:
            resolve = self._read_resolve
            self._read_resolve = None
            self._read_reject = None
            # Resolve with None — __anext__ raises StopAsyncIteration when it sees _is_done
            resolve(None)

    def error(self, error: Any) -> None:
        # Store whatever was thrown — may not be an Exception subclass
        self._has_error = error
        if self._read_reject is not None:
            reject = self._read_reject
            self._read_resolve = None
            self._read_reject = None
            # set_exception requires an Exception; wrap non-Exceptions
            if isinstance(error, Exception):
                reject(error)
            else:
                reject(Exception(str(error) if error is not None else "unknown"))

    async def aclose(self) -> None:
        self._is_done = True
        if self._returned_fn is not None:
            self._returned_fn()


__all__ = ["Stream"]
