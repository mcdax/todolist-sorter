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


import httpx
import pytest

from app.models import SortingProject


@pytest.mark.asyncio
async def test_get_tasks_happy_path(respx_mock):
    respx_mock.get("https://api.todoist.com/api/v1/tasks").mock(
        return_value=httpx.Response(200, json={
            "results": [
                {"id": "111", "content": "Apples", "project_id": "999", "order": 1},
                {"id": "222", "content": "Milk", "project_id": "999", "order": 2},
            ],
            "next_cursor": None,
        })
    )
    b = TodoistBackend(api_token="tok", client_secret="s")
    project = SortingProject(
        name="Lidl", provider="todoist",
        external_project_id="999", categories=[],
    )

    tasks = await b.get_tasks(project)

    assert [t.id for t in tasks] == ["111", "222"]
    assert tasks[0].content == "Apples"


import json as _json
from urllib.parse import parse_qs


@pytest.mark.asyncio
async def test_reorder_calls_sync_api(respx_mock):
    route = respx_mock.post("https://api.todoist.com/api/v1/sync").mock(
        return_value=httpx.Response(200, json={"sync_status": {}})
    )
    b = TodoistBackend(api_token="tok", client_secret="s")
    project = SortingProject(
        name="Lidl", provider="todoist",
        external_project_id="999", categories=[],
    )

    await b.reorder(project, ["222", "111", "333"])

    assert route.called
    form = parse_qs(route.calls.last.request.content.decode())
    commands = _json.loads(form["commands"][0])
    assert len(commands) == 1
    cmd = commands[0]
    assert cmd["type"] == "item_reorder"
    assert cmd["args"]["items"] == [
        {"id": "222", "child_order": 1},
        {"id": "111", "child_order": 2},
        {"id": "333", "child_order": 3},
    ]


@pytest.mark.asyncio
async def test_list_projects(respx_mock):
    respx_mock.get("https://api.todoist.com/api/v1/projects").mock(
        return_value=httpx.Response(200, json={
            "results": [
                {"id": "111", "name": "Lidl Einkauf"},
                {"id": "222", "name": "Private"},
            ],
            "next_cursor": None,
        })
    )
    b = TodoistBackend(api_token="tok", client_secret="s")
    projects = await b.list_projects()
    assert [(p.id, p.name) for p in projects] == [
        ("111", "Lidl Einkauf"), ("222", "Private"),
    ]


@pytest.mark.asyncio
async def test_reorder_skips_empty(respx_mock):
    route = respx_mock.post("https://api.todoist.com/api/v1/sync").mock(
        return_value=httpx.Response(200, json={})
    )
    b = TodoistBackend(api_token="tok", client_secret="s")
    project = SortingProject(
        name="Lidl", provider="todoist",
        external_project_id="999", categories=[],
    )
    await b.reorder(project, [])
    assert not route.called


@pytest.mark.asyncio
async def test_get_tasks_paginates(respx_mock):
    route = respx_mock.get("https://api.todoist.com/api/v1/tasks").mock(
        side_effect=[
            httpx.Response(200, json={
                "results": [{"id": "1", "content": "A"}],
                "next_cursor": "c2",
            }),
            httpx.Response(200, json={
                "results": [{"id": "2", "content": "B"}],
                "next_cursor": None,
            }),
        ]
    )
    b = TodoistBackend(api_token="tok", client_secret="s")
    project = SortingProject(
        name="L", provider="todoist",
        external_project_id="999", categories=[],
    )
    tasks = await b.get_tasks(project)
    assert [(t.id, t.content) for t in tasks] == [("1", "A"), ("2", "B")]
    # Second call must have sent the cursor
    q = dict(route.calls[1].request.url.params)
    assert q.get("cursor") == "c2"
