import base64
import hashlib
import hmac
import json
import uuid
from typing import ClassVar

import httpx

from app.backends.base import ProviderProject, Task
from app.models import SortingProject


_REST_BASE = "https://api.todoist.com/rest/v2"
_SYNC_URL = "https://api.todoist.com/sync/v9/sync"
_TRIGGER_EVENTS = {"item:added", "item:updated"}


class TodoistBackend:
    name: ClassVar[str] = "todoist"

    def __init__(self, api_token: str, client_secret: str) -> None:
        self._api_token = api_token
        self._client_secret = client_secret

    # ------------------------------------------------------------------
    # Webhook verification
    # ------------------------------------------------------------------

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        sig = _get_header(headers, "X-Todoist-Hmac-SHA256")
        if not sig:
            return False
        expected = base64.b64encode(
            hmac.new(self._client_secret.encode(), body, hashlib.sha256).digest()
        ).decode()
        return hmac.compare_digest(sig, expected)

    # ------------------------------------------------------------------
    # Payload parsing
    # ------------------------------------------------------------------

    def extract_project_id(self, payload: dict) -> str | None:
        data = payload.get("event_data") or {}
        pid = data.get("project_id")
        return str(pid) if pid is not None else None

    def extract_event_name(self, payload: dict) -> str | None:
        ev = payload.get("event_name")
        return str(ev) if ev else None

    def extract_item_id(self, payload: dict) -> str | None:
        data = payload.get("event_data") or {}
        iid = data.get("id")
        return str(iid) if iid is not None else None

    def extract_trigger_content(self, payload: dict) -> str | None:
        if payload.get("event_name") not in _TRIGGER_EVENTS:
            return None
        data = payload.get("event_data") or {}
        content = data.get("content")
        return str(content) if content else None

    # ------------------------------------------------------------------
    # HTTP client
    # ------------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._api_token}"},
            timeout=httpx.Timeout(15.0),
        )

    # ------------------------------------------------------------------
    # REST: fetch tasks
    # ------------------------------------------------------------------

    async def get_tasks(self, project: SortingProject) -> list[Task]:
        async with self._client() as c:
            r = await c.get(
                f"{_REST_BASE}/tasks",
                params={"project_id": project.external_project_id},
            )
            r.raise_for_status()
            return [
                Task(id=str(item["id"]), content=item["content"])
                for item in r.json()
            ]

    # ------------------------------------------------------------------
    # REST: list projects
    # ------------------------------------------------------------------

    async def list_projects(self) -> list[ProviderProject]:
        async with self._client() as c:
            r = await c.get(f"{_REST_BASE}/projects")
            r.raise_for_status()
            return [
                ProviderProject(id=str(p["id"]), name=p["name"])
                for p in r.json()
            ]

    # ------------------------------------------------------------------
    # Sync API: reorder tasks
    # ------------------------------------------------------------------

    async def reorder(
        self, project: SortingProject, ordered_ids: list[str]
    ) -> None:
        if not ordered_ids:
            return
        command = {
            "type": "item_reorder",
            "uuid": str(uuid.uuid4()),
            "args": {
                "items": [
                    {"id": tid, "child_order": i + 1}
                    for i, tid in enumerate(ordered_ids)
                ]
            },
        }
        async with self._client() as c:
            r = await c.post(
                _SYNC_URL,
                data={"commands": json.dumps([command])},
            )
            r.raise_for_status()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_header(headers: dict[str, str], name: str) -> str | None:
    lower = name.lower()
    for k, v in headers.items():
        if k.lower() == lower:
            return v
    return None
