import base64
import hashlib
import hmac
import json
import logging
import os
import uuid
from typing import ClassVar

import httpx

from app.backends.base import ProviderProject, Task
from app.models import SortingProject

log = logging.getLogger(__name__)


_API_BASE = "https://api.todoist.com/api/v1"
_SYNC_URL = "https://api.todoist.com/api/v1/sync"
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
            if os.environ.get("WEBHOOK_DEBUG"):
                log.warning("webhook: missing X-Todoist-Hmac-SHA256 header")
            return False
        expected = base64.b64encode(
            hmac.new(self._client_secret.encode(), body, hashlib.sha256).digest()
        ).decode()
        ok = hmac.compare_digest(sig, expected)
        if not ok and os.environ.get("WEBHOOK_DEBUG"):
            body_preview = body[:200].decode("utf-8", errors="replace")
            log.warning(
                "webhook HMAC mismatch:\n"
                "  received_sig = %s\n"
                "  expected_sig = %s\n"
                "  body_length  = %d\n"
                "  body_preview = %r\n"
                "  secret_len   = %d",
                sig, expected, len(body), body_preview, len(self._client_secret),
            )
        return ok

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
        tasks: list[Task] = []
        cursor: str | None = None
        async with self._client() as c:
            while True:
                params = {
                    "project_id": project.external_project_id,
                    "limit": 200,
                }
                if cursor:
                    params["cursor"] = cursor
                r = await c.get(f"{_API_BASE}/tasks", params=params)
                r.raise_for_status()
                data = r.json()
                for item in data.get("results", []):
                    tasks.append(Task(id=str(item["id"]), content=item["content"]))
                cursor = data.get("next_cursor")
                if not cursor:
                    break
        return tasks

    # ------------------------------------------------------------------
    # REST: list projects
    # ------------------------------------------------------------------

    async def list_projects(self) -> list[ProviderProject]:
        projects: list[ProviderProject] = []
        cursor: str | None = None
        async with self._client() as c:
            while True:
                params: dict = {"limit": 200}
                if cursor:
                    params["cursor"] = cursor
                r = await c.get(f"{_API_BASE}/projects", params=params)
                r.raise_for_status()
                data = r.json()
                for p in data.get("results", []):
                    projects.append(ProviderProject(id=str(p["id"]), name=p["name"]))
                cursor = data.get("next_cursor")
                if not cursor:
                    break
        return projects

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
