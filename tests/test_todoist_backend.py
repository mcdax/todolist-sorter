import base64
import hashlib
import hmac
import json

from app.backends.todoist import TodoistBackend


def _sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def test_verify_webhook_valid():
    secret = "s3cret"
    body = json.dumps({"event_name": "item:added"}).encode()
    sig = _sign(secret, body)
    b = TodoistBackend(api_token="t", client_secret=secret)
    assert b.verify_webhook({"X-Todoist-Hmac-SHA256": sig}, body) is True


def test_verify_webhook_invalid():
    b = TodoistBackend(api_token="t", client_secret="s3cret")
    assert b.verify_webhook(
        {"X-Todoist-Hmac-SHA256": "wrongsig"}, b"{}"
    ) is False


def test_verify_webhook_missing_header():
    b = TodoistBackend(api_token="t", client_secret="s3cret")
    assert b.verify_webhook({}, b"{}") is False


def test_header_lookup_case_insensitive():
    secret = "s3cret"
    body = b"{}"
    sig = _sign(secret, body)
    b = TodoistBackend(api_token="t", client_secret=secret)
    assert b.verify_webhook({"x-todoist-hmac-sha256": sig}, body) is True
