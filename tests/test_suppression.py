import time
from uuid import uuid4

from app.suppression import SuppressionTracker


def test_unmarked_project_is_not_suppressed():
    t = SuppressionTracker()
    assert t.is_suppressed(uuid4(), "42") is False


def test_marked_ids_are_suppressed_within_window():
    t = SuppressionTracker()
    pid = uuid4()
    t.mark(pid, ["1", "2", "3"], window_seconds=1.0)
    assert t.is_suppressed(pid, "1") is True
    assert t.is_suppressed(pid, "2") is True
    assert t.is_suppressed(pid, "99") is False


def test_expires_after_window():
    t = SuppressionTracker(clock=lambda: 1000.0)
    pid = uuid4()
    t.mark(pid, ["1"], window_seconds=0.5)
    t._clock = lambda: 1001.0  # type: ignore[assignment]
    assert t.is_suppressed(pid, "1") is False


def test_remark_replaces_previous_set():
    t = SuppressionTracker()
    pid = uuid4()
    t.mark(pid, ["1"], window_seconds=60)
    t.mark(pid, ["2"], window_seconds=60)
    assert t.is_suppressed(pid, "1") is False
    assert t.is_suppressed(pid, "2") is True
