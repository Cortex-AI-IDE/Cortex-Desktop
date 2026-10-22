from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")

DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF = 0.2


class LoopExhausted(RuntimeError):
    pass


def run_loop(
    step: Callable[[int], T],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    accept: Callable[[T], bool] = bool,
    backoff: float = DEFAULT_BACKOFF,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts}")

    last: T | None = None
    for attempt in range(1, attempts + 1):
        last = step(attempt)
        if accept(last):
            return last
        if attempt < attempts and backoff > 0:
            sleep(backoff * attempt)

    raise LoopExhausted(f"no attempt accepted after {attempts}: {last!r}")


def main() -> int:
    seen: list[int] = []

    def step(attempt: int) -> int:
        seen.append(attempt)
        return attempt

    result = run_loop(
        step,
        attempts=5,
        accept=lambda value: value >= 3,
        sleep=lambda _: None,
    )
    print(f"loop.py: accepted attempt {result} after {len(seen)} call(s) {seen}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
