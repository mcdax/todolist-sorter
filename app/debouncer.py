import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID


RunFn = Callable[[UUID], Awaitable[None]]


@dataclass
class _PerProjectState:
    last_event_at: float | None = None
    pending: asyncio.Task | None = None
    sort_running: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ProjectDebouncer:
    def __init__(self, run_fn: RunFn) -> None:
        self._run = run_fn
        self._state: dict[UUID, _PerProjectState] = {}

    def _get(self, project_id: UUID) -> _PerProjectState:
        st = self._state.get(project_id)
        if st is None:
            st = _PerProjectState()
            self._state[project_id] = st
        return st

    async def touch(self, project_id: UUID, delay: float = 5.0) -> None:
        st = self._get(project_id)
        now = time.monotonic()
        last = st.last_event_at
        st.last_event_at = now

        if last is None or (now - last) > delay:
            self._cancel_if_sleeping(st)
            st.pending = asyncio.create_task(self._run_after(project_id, 0))
        else:
            self._cancel_if_sleeping(st)
            st.pending = asyncio.create_task(self._run_after(project_id, delay))

    async def fire_now(self, project_id: UUID) -> None:
        st = self._get(project_id)
        st.last_event_at = time.monotonic()
        self._cancel_if_sleeping(st)
        st.pending = asyncio.create_task(self._run_after(project_id, 0))

    def _cancel_if_sleeping(self, st: _PerProjectState) -> None:
        if st.pending is not None and not st.sort_running and not st.pending.done():
            st.pending.cancel()

    async def _run_after(self, project_id: UUID, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        st = self._get(project_id)
        async with st.lock:
            st.sort_running = True
            try:
                await self._run(project_id)
            finally:
                st.sort_running = False
