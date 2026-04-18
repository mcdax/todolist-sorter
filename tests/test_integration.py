import asyncio
import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.backends.registry import BackendRegistry
from app.backends.todoist import TodoistBackend
from app.db import get_session
from app.debouncer import ProjectDebouncer
from app.models import CategoryCache, SortingProject
from app.routes.webhook import build_webhook_router
from app.sorter import sort_project
from app.suppression import SuppressionTracker

import app.models  # noqa: F401 - ensure SQLModel tables registered


def _sign(secret: str, body: bytes) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()


@pytest.mark.asyncio
async def test_end_to_end_webhook_sort_flow():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    pid = uuid4()
    with Session(engine) as s:
        s.add(SortingProject(
            id=pid, name="Lidl", provider="todoist",
            external_project_id="999",
            categories=["🥬 Vegetables", "🍎 Fruit"],
            debounce_seconds=0,
        ))
        s.commit()

    registry = BackendRegistry()
    registry.register(TodoistBackend(
        api_token="test-token", client_secret="secret",
    ))
    suppression = SuppressionTracker()

    llm = TestModel(custom_output_args={
        "assignments": [
            {"item_id": "T1", "category_name": "🍎 Fruit"},
            {"item_id": "T2", "category_name": "🥬 Vegetables"},
        ]
    })

    def _on_reorder(pid_, ids):
        suppression.mark(pid_, ids, window_seconds=30)

    async def _run_sort(project_id):
        with Session(engine) as s:
            project = s.get(SortingProject, project_id)
            backend = registry.get(project.provider)
            await sort_project(
                project_id=project_id, session=s,
                backend=backend, llm_model=llm,
                on_reorder=_on_reorder,
            )

    debouncer = ProjectDebouncer(_run_sort)

    app = FastAPI()
    app.include_router(build_webhook_router(
        registry=registry, debouncer=debouncer,
        suppression=suppression,
        session_dep=lambda: get_session(engine),
        default_delay=0,
    ))

    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://api.todoist.com/rest/v2/tasks").mock(
            return_value=httpx.Response(200, json=[
                {"id": "T1", "content": "Apples",
                 "project_id": "999", "order": 1},
                {"id": "T2", "content": "Lettuce",
                 "project_id": "999", "order": 2},
            ])
        )
        reorder_route = mock.post(
            "https://api.todoist.com/sync/v9/sync"
        ).mock(return_value=httpx.Response(200, json={"sync_status": {}}))

        body = json.dumps({
            "event_name": "item:added",
            "event_data": {"id": "T2", "content": "Lettuce",
                           "project_id": "999"},
        }).encode()
        sig = _sign("secret", body)

        with TestClient(app) as c:
            r = c.post("/webhook/todoist", content=body,
                       headers={"X-Todoist-Hmac-SHA256": sig,
                                "Content-Type": "application/json"})
            assert r.status_code == 200

        for _ in range(100):
            if reorder_route.called:
                break
            await asyncio.sleep(0.02)
        assert reorder_route.called

        form = parse_qs(reorder_route.calls.last.request.content.decode())
        commands = json.loads(form["commands"][0])
        items = commands[0]["args"]["items"]
        assert [it["id"] for it in items] == ["T2", "T1"]

    with Session(engine) as s:
        rows = s.exec(select(CategoryCache)).all()
        ck_to_cat = {r.content_key: r.category_name for r in rows}
        assert ck_to_cat == {"apples": "🍎 Fruit", "lettuce": "🥬 Vegetables"}

    assert suppression.is_suppressed(pid, "T1") is True
    assert suppression.is_suppressed(pid, "T2") is True
