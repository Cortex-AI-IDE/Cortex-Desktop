"""Run a provider's blocking stream generator off the asyncio event loop.

Every provider exposes ``chat_stream()`` as an ordinary synchronous generator
built on ``requests``. ``_call_llm`` used to iterate it directly:

    for chunk in provider.chat_stream(...):
        ...

which runs ``session.post()`` and ``iter_lines()`` *inside an event-loop
callback*. The whole agent loop therefore stops for as long as the socket read
takes, and on 2026-09-09 that was 154 seconds:

    asyncio\\events.py:94 in _run
      agent_bridge.py:2548  _handle_chat
      agent_bridge.py:5818  _call_llm
      alibaba_provider.py:471 chat_stream
        requests\\sessions.py:712 post
          ssl.py:1138 read          <- blocked here, loop dead

Two user-visible consequences, both in that day's log:

* Prompts sent during the stall were accepted by the UI and never ran. They
  logged ``process_message task_id=...`` and then nothing - no ``provider=``,
  no ``Agentic turn`` - because the coroutine that would start them could not
  be scheduled.
* Stop did nothing. ``stop_generation`` only sets a flag, and the flag is read
  inside the per-chunk loop body. With zero chunks received the body never ran
  once, so three presses of Stop were ignored and "Stream interrupted by stop
  request" appeared 107 seconds after the first one.

``aiter_offloaded`` fixes both. The generator runs on a worker thread and
chunks arrive over a queue, so the event loop stays free; and the consumer
wakes every ``poll_interval`` seconds to re-check ``should_stop``, so Stop
takes effect even when the provider has sent nothing at all.

What this does NOT do is make a stalled request finish faster. The worker
thread stays blocked in the socket read until the provider's own timeout
expires; it is simply a thread now, not the event loop. Aborting the in-flight
request itself needs the provider to close its response object, which is a
separate change.

Cost, measured over 20,000 chunks: 0.084 ms per chunk against 0.0001 ms for
the old direct iteration, almost all of it the per-chunk ``wait_for`` timer
that makes the stop poll possible. At 100 chunks/second that is ~8 ms of CPU
per second of streaming, so it was not worth trading for a fast path that
skips the timer while the queue is non-empty.
"""

import asyncio
import concurrent.futures
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

    ``make_gen`` is called on the worker thread, so building the generator is
    offloaded too - relevant because a provider may do real work (payload
    assembly, key lookup) before yielding anything.

    ``should_stop`` is polled while waiting for the next chunk. Returning True
    ends the iteration promptly even if the provider has produced nothing,
    which is the case Stop used to be unable to interrupt.

    Exceptions raised by the generator are re-raised here with their original
    traceback, so existing error handling around the call site is unaffected.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
    cancelled = threading.Event()

    def _put(item: Any) -> None:
        """Hand one item to the loop. Blocks the WORKER, never the loop.

        Blocking here is deliberate: it is the backpressure that stops a fast
        provider from queueing an unbounded number of chunks. It gives up in
        slices so that a consumer which has stopped reading cannot strand the
        thread.
        """
        while not cancelled.is_set():
            future = asyncio.run_coroutine_threadsafe(queue.put(item), loop)
            try:
                future.result(timeout=_PUT_SLICE_SECONDS)
                return
            except concurrent.futures.TimeoutError:
                future.cancel()
                continue
            except Exception:
                # Loop is closing or already closed. Nothing can be delivered.
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
                pass
            _put(("end", None))

    worker = threading.Thread(target=_pump, name=name, daemon=True)
    worker.start()

    try:
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), poll_interval)
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
        # Releases the worker. If it is mid-_put on a queue nobody is reading
        # any more it notices within _PUT_SLICE_SECONDS, and otherwise on its
        # next loop iteration, so no drain task is needed to free it. Spawning
        # one here only produced "Task was destroyed but it is pending" when
        # the loop was torn down before it finished.
        #
        # No `return` in this block: returning from a finally swallows whatever
        # is propagating out, including the CancelledError that shuts this
        # generator down.
        cancelled.set()
