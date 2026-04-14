"""Shared primitives for QObject workers.

``fanout`` parallelises N independent API calls into a ThreadPoolExecutor
and emits results as they arrive, respecting a threading.Event interrupt.
All four workers share the same pattern; keep it in one place so behaviour
stays consistent (retry policy, cancel semantics, max workers).
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, TypeVar

log = logging.getLogger(__name__)

K = TypeVar("K")          # input keys (uuid, id, url)
T = TypeVar("T")           # fetched item

MAX_WORKERS = 8


def fanout(
    fn: Callable[[K], T],
    keys: Iterable[K],
    emit: Callable[[T], None],
    interrupted: threading.Event,
    label: str = "item",
    max_workers: int = MAX_WORKERS,
) -> None:
    """Run ``fn(key)`` concurrently, calling ``emit(result)`` per success.

    - Exceptions from ``fn`` are swallowed with a warning — one bad id
      doesn't kill the whole library load.
    - ``interrupted`` is polled between completions; when set, the pool
      shuts down with ``cancel_futures=True``.
    - If ``fn`` returns ``None``, the item is skipped silently.
    """
    keys = list(keys)
    if not keys:
        return
    pool = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = {pool.submit(fn, k): k for k in keys}
        for fut in as_completed(futures):
            if interrupted.is_set():
                break
            key = futures[fut]
            try:
                item = fut.result()
            except Exception as exc:
                log.warning("Failed to load %s %r: %s", label, key, exc)
                continue
            if item is not None:
                emit(item)
    finally:
        was_int = interrupted.is_set()
        pool.shutdown(wait=not was_int, cancel_futures=was_int)
