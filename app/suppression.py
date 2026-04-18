import time
from collections.abc import Callable, Iterable
from uuid import UUID


class SuppressionTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[UUID, tuple[frozenset[str], float]] = {}

    def mark(
        self, project_id: UUID, item_ids: Iterable[str], window_seconds: float
    ) -> None:
        deadline = self._clock() + window_seconds
        self._entries[project_id] = (frozenset(item_ids), deadline)

    def is_suppressed(self, project_id: UUID, item_id: str) -> bool:
        entry = self._entries.get(project_id)
        if entry is None:
            return False
        ids, deadline = entry
        if self._clock() >= deadline:
            self._entries.pop(project_id, None)
            return False
        return item_id in ids
