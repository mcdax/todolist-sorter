import re

_WS = re.compile(r"\s+")


def content_key(text: str) -> str:
    return _WS.sub(" ", text.strip()).lower()
