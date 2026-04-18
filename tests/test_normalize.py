import pytest

from app.normalize import content_key


@pytest.mark.parametrize("raw,expected", [
    ("Apples", "apples"),
    ("  Yogurt  ", "yogurt"),
    ("Whole milk\t 3.5%", "whole milk 3.5%"),
    ("🍎 Apples", "🍎 apples"),
    ("A   B\nC", "a b c"),
    ("", ""),
])
def test_content_key(raw, expected):
    assert content_key(raw) == expected
