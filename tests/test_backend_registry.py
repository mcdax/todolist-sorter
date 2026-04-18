import pytest

from app.backends.registry import BackendRegistry


class _FakeBackend:
    name = "fake"


def test_register_and_get():
    r = BackendRegistry()
    b = _FakeBackend()
    r.register(b)
    assert r.get("fake") is b


def test_get_unknown_raises():
    r = BackendRegistry()
    with pytest.raises(KeyError):
        r.get("nope")


def test_register_duplicate_raises():
    r = BackendRegistry()
    r.register(_FakeBackend())
    with pytest.raises(ValueError):
        r.register(_FakeBackend())
