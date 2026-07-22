"""Bounded convergence loop — the anti-runaway primitive used by multi-pass
extraction (P3). It repeats a producing step, accumulating unique items, and stops
as soon as a pass yields nothing new OR a hard cap is reached. It can never run away.
"""

from __future__ import annotations

from typing import Callable, Hashable, Iterable, TypeVar

from ..logging_setup import get_logger

T = TypeVar("T")
log = get_logger("rga.loop")


def run_until_convergence(
    produce: Callable[[int], Iterable[T]],
    key: Callable[[T], Hashable],
    *,
    max_passes: int,
) -> list[T]:
    """Accumulate unique items across passes.

    Args:
        produce: called as produce(pass_index) -> iterable of items for that pass.
        key: uniqueness key for an item (duplicates across passes are ignored).
        max_passes: hard upper bound on passes (must be >= 1).

    Returns:
        The list of unique items in first-seen order.

    Stops when a pass adds no new items, or after `max_passes` passes.
    """
    if max_passes < 1:
        raise ValueError("max_passes must be >= 1")

    seen: set[Hashable] = set()
    out: list[T] = []
    for i in range(max_passes):
        added = 0
        for item in produce(i):
            k = key(item)
            if k in seen:
                continue
            seen.add(k)
            out.append(item)
            added += 1
        log.info("convergence pass %d: +%d new (total %d)", i + 1, added, len(out))
        if added == 0:
            break
    return out
