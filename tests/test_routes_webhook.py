import base64
import hashlib
import hmac
import json
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.backends.registry import BackendRegistry
from app.backends.todoist import TodoistBackend
from app.db import get_session
from app.models import CategoryCache, SortingProject
from app.routes.webhook import build_webhook_router
from app.suppression import SuppressionTracker


def _sign(secret: str, body: bytes) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()


@pytest.fixture()
def webhook_ctx(engine):
    touches: list[tuple] = []

    class FakeDebouncer:
        async def touch(self, pid, delay=5.0):
            touches.append(("touch", pid))
        async def fire_now(self, pid):
            touches.append(("fire_now", pid))

    registry = BackendRegistry()
    registry.register(TodoistBackend(api_token="t", client_secret="webhook-secret"))
    suppression = SuppressionTracker()

    app = FastAPI()
    app.include_router(build_webhook_router(
        registry=registry,
        debouncer=FakeDebouncer(),
        suppression=suppression,
        session_dep=lambda: get_session(engine),
        default_delay=5.0,
    ))
    return TestClient(app), touches, suppression


def _create_project(engine, external_id="999"):
    pid = uuid4()
    with Session(engine) as s:
        s.add(SortingProject(
            id=pid, name="Lidl", provider="todoist",
            external_project_id=external_id,
            categories=["🍎 Fruit"],
        ))
        s.commit()
    return pid


def _post(client, body: bytes, sig: str):
    return client.post(
        "/webhook/todoist", content=body,
        headers={"X-Todoist-Hmac-SHA256": sig,
                 "Content-Type": "application/json"},
    )


def test_rejects_invalid_signature(webhook_ctx):
    client, _, _ = webhook_ctx
    r = client.post("/webhook/todoist", content=b"{}",
                    headers={"X-Todoist-Hmac-SHA256": "wrong"})
    assert r.status_code == 401


def test_unknown_provider_404(webhook_ctx):
    client, _, _ = webhook_ctx
    r = client.post("/webhook/unknown", content=b"{}")
    assert r.status_code == 404


def test_ignores_unknown_project(webhook_ctx):
    client, touches, _ = webhook_ctx
    body = json.dumps({
        "event_name": "item:added",
        "event_data": {"id": "1", "content": "Apples", "project_id": "nope"},
    }).encode()
    r = _post(client, body, _sign("webhook-secret", body))
    assert r.status_code == 200
    assert r.json() == {"status": "ignored"}
    assert touches == []


def test_cache_miss_touches_debouncer(webhook_ctx, engine):
    client, touches, _ = webhook_ctx
    pid = _create_project(engine)
    body = json.dumps({
        "event_name": "item:added",
        "event_data": {"id": "1", "content": "Cinnamon", "project_id": "999"},
    }).encode()
    r = _post(client, body, _sign("webhook-secret", body))
    assert r.status_code == 200
    assert touches == [("touch", pid)]


def test_cache_hit_fires_now(webhook_ctx, engine):
    client, touches, _ = webhook_ctx
    pid = _create_project(engine)
    with Session(engine) as s:
        s.add(CategoryCache(project_id=pid, content_key="apples",
                            category_name="🍎 Fruit"))
        s.commit()
    body = json.dumps({
        "event_name": "item:added",
        "event_data": {"id": "1", "content": "Apples", "project_id": "999"},
    }).encode()
    r = _post(client, body, _sign("webhook-secret", body))
    assert r.status_code == 200
    assert touches == [("fire_now", pid)]


def test_suppressed_echo_is_dropped(webhook_ctx, engine):
    client, touches, suppression = webhook_ctx
    pid = _create_project(engine)
    suppression.mark(pid, ["T1"], window_seconds=60)
    body = json.dumps({
        "event_name": "item:updated",
        "event_data": {"id": "T1", "content": "Apples", "project_id": "999"},
    }).encode()
    r = _post(client, body, _sign("webhook-secret", body))
    assert r.status_code == 200
    assert r.json() == {"status": "suppressed"}
    assert touches == []


def test_item_added_not_suppressed(webhook_ctx, engine):
    client, touches, suppression = webhook_ctx
    pid = _create_project(engine)
    suppression.mark(pid, ["T1"], window_seconds=60)
    body = json.dumps({
        "event_name": "item:added",
        "event_data": {"id": "T1", "content": "Apples", "project_id": "999"},
    }).encode()
    r = _post(client, body, _sign("webhook-secret", body))
    assert r.status_code == 200
    assert touches == [("touch", pid)]
