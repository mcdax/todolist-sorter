import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.backends.registry import BackendRegistry
from app.models import CategoryCache, SortingProject
from app.normalize import content_key
from app.suppression import SuppressionTracker

log = logging.getLogger(__name__)


def build_webhook_router(
    *,
    registry: BackendRegistry,
    debouncer: Any,
    suppression: SuppressionTracker,
    session_dep: Callable[[], Iterator[Session]],
    default_delay: float = 5.0,
) -> APIRouter:
    router = APIRouter()

    def _get_session():
        yield from session_dep()

    @router.post("/webhook/{provider}")
    async def receive(
        provider: str,
        request: Request,
        s: Session = Depends(_get_session),
    ):
        try:
            backend = registry.get(provider)
        except KeyError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"unknown provider '{provider}'",
            )

        body = await request.body()
        if not backend.verify_webhook(dict(request.headers), body):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "invalid signature",
            )

        try:
            payload = json.loads(body)
        except Exception:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "invalid JSON",
            )

        external_pid = backend.extract_project_id(payload)
        if external_pid is None:
            return {"status": "ignored"}

        project = s.exec(
            select(SortingProject).where(
                SortingProject.provider == provider,
                SortingProject.external_project_id == external_pid,
                SortingProject.enabled.is_(True),
            )
        ).first()
        if not project:
            return {"status": "ignored"}

        event_name = backend.extract_event_name(payload)
        item_id = backend.extract_item_id(payload)
        if event_name == "item:updated" and item_id is not None:
            if suppression.is_suppressed(project.id, item_id):
                return {"status": "suppressed"}

        trigger = backend.extract_trigger_content(payload)
        if trigger is not None:
            cached = s.get(CategoryCache, (project.id, content_key(trigger)))
            if cached is not None:
                await debouncer.fire_now(project.id)
                return {"status": "queued"}

        await debouncer.touch(
            project.id,
            delay=project.debounce_seconds or default_delay,
        )
        return {"status": "queued"}

    return router
