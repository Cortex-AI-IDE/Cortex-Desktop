# ------------------------------------------------------------
# generators.py
# Python conversion of utils/generators.ts (lines 1-89)
#
# Async generator utilities:
# - lastX(): get last value from async generator
# - returnValue(): get return value from async generator
# - all(): run multiple async generators concurrently up to a cap
# - toArray(): collect async generator into list
# - fromArray(): wrap array as async generator
# ------------------------------------------------------------

import asyncio
from typing import (
    Any, AsyncGenerator, List, Optional, TypeVar,
)

T = TypeVar("T")

_NO_VALUE = object()


async def last_x(as_: AsyncGenerator[T]) -> T:
    """Return the last value yielded by an async generator."""
    last_value: Any = _NO_VALUE
    async for a in as_:
        last_value = a
    if last_value is _NO_VALUE:
        raise RuntimeError("No items in generator")
    return last_value


async def return_value(as_: AsyncGenerator[Any, T]) -> T:
    """
    Return the final return value of an async generator.

    Mirrors TS: consumes all yielded values then returns e.value when done is True.
    Python note: Python async generators don't expose the return value via StopAsyncIteration
    (unlike TS where e.value is accessible when done=true). This function returns None
    for generators without an explicit return value.
    """
    async for _ in as_:
        pass
    # Python async generators do not carry return values through StopAsyncIteration.
    # This is a known Python limitation; return None when no explicit return exists.
    return None  # type: ignore[return-value]


async def all_(
    generators: List[AsyncGenerator[T, None]],
    concurrency_cap: int = 0,
) -> AsyncGenerator[T, None]:
    """
    Run multiple async generators concurrently up to a concurrency cap.

    Values are yielded as soon as they are produced (not in order).
    Mirrors the TS Promise.race pattern from generators.ts exactly:
    - Start up to concurrency_cap generators
    - When one yields, immediately schedule its next __anext__()
    - When one finishes, start the next waiting generator
    """
    if not generators:
        return

    cap = concurrency_cap if concurrency_cap > 0 else len(generators)

    # Each entry: (generator, current_pending_task_or_None)
    gen_tasks: List[tuple[AsyncGenerator[T, None], Optional[asyncio.Task]]] = [
        (gen, None) for gen in generators
    ]
    waiting: List[int] = []  # indices into gen_tasks not yet started
    running: int = 0

    # Start initial batch
    for i, (gen, _) in enumerate(gen_tasks):
        if running < cap:
            task = asyncio.create_task(gen.__anext__())
            gen_tasks[i] = (gen, task)
            running += 1
        else:
            waiting.append(i)

    active: set[asyncio.Task] = {t for _, t in gen_tasks if t is not None}

    while active:
        done, active = await asyncio.wait(
            active,
            return_when=asyncio.FIRST_COMPLETED,
        )

        for completed in done:
            # Find which gen_tasks entry this task belongs to
            for i, (gen, task) in enumerate(gen_tasks):
                if task is completed:
                    running -= 1
                    try:
                        value = completed.result()
                        # Reschedule this generator's next __anext__()
                        next_task = asyncio.create_task(gen.__anext__())
                        gen_tasks[i] = (gen, next_task)
                        active.add(next_task)
                        running += 1
                        yield value
                    except StopAsyncIteration:
                        # Generator is exhausted; start next waiting generator if any
                        if waiting:
                            next_idx = waiting.pop(0)
                            next_gen, _ = gen_tasks[next_idx]
                            next_task = asyncio.create_task(next_gen.__anext__())
                            gen_tasks[next_idx] = (next_gen, next_task)
                            active.add(next_task)
                            running += 1
                    break


async def to_array(as_: AsyncGenerator[T, None]) -> List[T]:
    """Collect all values from an async generator into a list."""
    result: List[T] = []
    async for a in as_:
        result.append(a)
    return result


async def from_array(values: List[T]) -> AsyncGenerator[T, None]:
    """Wrap a list as an async generator, yielding each value."""
    for value in values:
        yield value


__all__ = [
    "last_x",
    "return_value",
    "all_",
    "to_array",
    "from_array",
]

