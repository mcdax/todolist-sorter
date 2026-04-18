import asyncio
from uuid import UUID, uuid4

import pytest

from app.debouncer import ProjectDebouncer


@pytest.mark.asyncio
async def test_leading_edge_fires_immediately():
    pid = uuid4()
    fired: list[UUID] = []

    async def runner(p):
        fired.append(p)

    d = ProjectDebouncer(runner)
    await d.touch(pid, delay=0.1)
    await asyncio.sleep(0.01)
    assert fired == [pid]


@pytest.mark.asyncio
async def test_trailing_edge_collapses_burst():
    pid = uuid4()
    fired: list[UUID] = []

    async def runner(p):
        fired.append(p)

    d = ProjectDebouncer(runner)
    await d.touch(pid, delay=0.05)
    await asyncio.sleep(0.01)
    for _ in range(5):
        await d.touch(pid, delay=0.05)
        await asyncio.sleep(0.005)
    await asyncio.sleep(0.1)
    assert len(fired) == 2


@pytest.mark.asyncio
async def test_fire_now_bypasses_delay():
    pid = uuid4()
    fired: list[UUID] = []

    async def runner(p):
        fired.append(p)

    d = ProjectDebouncer(runner)
    await d.touch(pid, delay=0.05)
    await asyncio.sleep(0.01)
    await d.fire_now(pid)
    await asyncio.sleep(0.02)
    assert len(fired) == 2


@pytest.mark.asyncio
async def test_lock_serializes_per_project():
    pid = uuid4()
    starts: list[float] = []
    ends: list[float] = []

    async def runner(_):
        starts.append(asyncio.get_event_loop().time())
        await asyncio.sleep(0.05)
        ends.append(asyncio.get_event_loop().time())

    d = ProjectDebouncer(runner)
    await d.touch(pid, delay=0)
    await asyncio.sleep(0.005)
    await d.fire_now(pid)
    await asyncio.sleep(0.2)
    assert starts[1] >= ends[0]
