from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
from typing import Iterator


class ResourceBusy(RuntimeError):
    pass


class ResourceLeaseManager:
    def __init__(self) -> None:
        self._lock = RLock()
        self._leased: set[str] = set()

    @contextmanager
    def acquire(self, resources: frozenset[str]) -> Iterator[None]:
        ordered = tuple(sorted(resources))
        with self._lock:
            conflicts = self._leased.intersection(ordered)
            if conflicts:
                raise ResourceBusy(
                    f"Resources already leased: {sorted(conflicts)}"
                )
            self._leased.update(ordered)
        try:
            yield
        finally:
            with self._lock:
                self._leased.difference_update(ordered)

