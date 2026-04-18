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


def test_extract_project_id_item_event():
    b = TodoistBackend(api_token="t", client_secret="s")
    payload = {
        "event_name": "item:added",
        "event_data": {"id": "111", "content": "Apples", "project_id": "999"},
    }
    assert b.extract_project_id(payload) == "999"


def test_extract_project_id_missing():
    b = TodoistBackend(api_token="t", client_secret="s")
    assert b.extract_project_id({"event_name": "item:added"}) is None


def test_extract_event_name():
    b = TodoistBackend(api_token="t", client_secret="s")
    assert b.extract_event_name({"event_name": "item:updated"}) == "item:updated"
    assert b.extract_event_name({}) is None


def test_extract_item_id():
    b = TodoistBackend(api_token="t", client_secret="s")
    payload = {"event_data": {"id": "42"}}
    assert b.extract_item_id(payload) == "42"
    assert b.extract_item_id({}) is None


def test_extract_trigger_content_on_add_or_update():
    b = TodoistBackend(api_token="t", client_secret="s")
    for ev in ("item:added", "item:updated"):
        payload = {
            "event_name": ev,
            "event_data": {"id": "1", "content": "Apples", "project_id": "9"},
        }
        assert b.extract_trigger_content(payload) == "Apples"


def test_extract_trigger_content_none_for_other_events():
    b = TodoistBackend(api_token="t", client_secret="s")
    payload = {
        "event_name": "item:completed",
        "event_data": {"id": "1", "content": "Apples", "project_id": "9"},
    }
    assert b.extract_trigger_content(payload) is None
