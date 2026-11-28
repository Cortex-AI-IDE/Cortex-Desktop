"""Run a provider's blocking stream generator off the asyncio event loop.

Providers expose chat_stream() as a sync generator over requests; iterating it
on the loop blocked the loop for 154s inside ssl read (2026-09-09), so prompts
sent during the stall never ran and Stop did nothing (the stop flag is read in
the per-chunk body, and zero chunks arrived). aiter_offloaded runs the
generator on a worker thread and the consumer wakes every poll_interval to
re-check should_stop. It does not make a stalled request finish sooner; the
worker stays blocked until the provider's own timeout.
"""

import asyncio
import logging
import threading
from typing import Any, AsyncIterator, Callable, Iterable, Optional

log = logging.getLogger(__name__)

# How long the worker may block handing one chunk to the loop before it
# re-checks whether the consumer has gone away. Without this a consumer that
# stops reading leaves the worker parked on a full queue forever.
_PUT_SLICE_SECONDS = 2.0


async def aiter_offloaded(
    make_gen: Callable[[], Iterable[Any]],
    *,
    should_stop: Optional[Callable[[], bool]] = None,
    poll_interval: float = 0.25,
    queue_size: int = 64,
    name: str = "llm-stream",
) -> AsyncIterator[Any]:
    """Iterate a blocking generator without blocking the event loop.

    ``make_gen`` is called on the worker thread, so generator construction is
    offloaded too. ``should_stop`` is polled while waiting, so Stop ends the
    iteration promptly even when the provider has produced nothing. Generator
    exceptions are re-raised here with their original traceback.
    """
    loop = asyncio.get_running_loop()
    # Unbounded asyncio queue; the bound lives in `slots` below, on the
    # worker's side, where blocking is allowed.
    queue: asyncio.Queue = asyncio.Queue()
    slots = threading.Semaphore(queue_size)
    cancelled = threading.Event()

    def _put(item: Any) -> None:
        """Hand one item to the loop, exactly once. Blocks the WORKER, never the loop.

        Blocking is deliberate backpressure against an unbounded queue. It
        used to put with run_coroutine_threadsafe, wait 2 s, then cancel and
        put the SAME item again; when the first put had completed just as the
        wait timed out, the chunk was delivered twice (text repeated in chat,
        owner report 2026-11-22). Now a slot is taken first and the item is
        handed over with one call_soon_threadsafe, which cannot be half-done.
        """
        while not cancelled.is_set():
            if not slots.acquire(timeout=_PUT_SLICE_SECONDS):
                continue
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:
                return  # loop closed: nothing can be delivered
            return

    def _pump() -> None:
        gen: Any = None
        try:
            gen = make_gen()
            for item in gen:
                if cancelled.is_set():
                    break
                _put(("item", item))
        except BaseException as exc:  # noqa: BLE001 - forwarded verbatim
            _put(("error", exc))
        finally:
            # Close the generator so the provider can release its response and
            # socket rather than waiting for garbage collection.
            try:
                closer = getattr(gen, "close", None)
                if closer is not None:
                    closer()
            except Exception:
                pass  # silent-ok: closing an already-finished generator is harmless
            _put(("end", None))

    worker = threading.Thread(target=_pump, name=name, daemon=True)
    worker.start()

    try:
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), poll_interval)
                slots.release()   # one more item may be queued by the worker
            except asyncio.TimeoutError:
                # Nothing arrived this slice. This is the only place a stop can
                # be noticed when the provider has sent no chunks at all.
                if should_stop is not None and should_stop():
                    log.info("[STREAM] Stop requested while waiting on the provider")
                    return
                continue

            if kind == "item":
                yield payload
            elif kind == "error":
                raise payload
            else:  # "end"
                return
    finally:
        # Release the worker: a mid-_put notices within _PUT_SLICE_SECONDS, otherwise
        # next loop iteration; no drain task needed (spawning one caused "Task was
        # destroyed but pending"). No `return` here: returning from finally would
        # swallow the CancelledError shutting down this generator.
        cancelled.set()
