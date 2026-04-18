import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI
from sqlmodel import Session

from app.backends.registry import BackendRegistry
from app.backends.todoist import TodoistBackend
from app.config import get_settings
from app.db import create_db_and_tables, get_session, make_engine
from app.debouncer import ProjectDebouncer
from app.models import SortingProject
from app.routes.projects import build_router as build_projects_router
from app.routes.webhook import build_webhook_router
from app.sorter import sort_project
from app.suppression import SuppressionTracker


def create_app() -> FastAPI:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    create_db_and_tables(engine)

    registry = BackendRegistry()
    registry.register(TodoistBackend(
        api_token=settings.todoist_api_token,
        client_secret=settings.todoist_client_secret,
    ))

    suppression = SuppressionTracker()

    def _on_reorder(pid: UUID, ids: list[str]) -> None:
        suppression.mark(
            pid, ids, window_seconds=settings.suppression_window_seconds,
        )

    async def _run_sort(project_id: UUID) -> None:
        with Session(engine) as s:
            project = s.get(SortingProject, project_id)
            if not project:
                return
            try:
                backend = registry.get(project.provider)
            except KeyError:
                return
            await sort_project(
                project_id=project_id, session=s,
                backend=backend, llm_model=settings.llm_model,
                on_reorder=_on_reorder,
            )

    debouncer = ProjectDebouncer(_run_sort)

    def _on_sort_requested(pid: UUID) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                debouncer.touch(pid, delay=settings.default_debounce_seconds)
            )
        except RuntimeError:
            # No running loop (e.g. in tests); silently skip
            pass

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield

    app = FastAPI(title="Todolist Sorter", lifespan=lifespan)

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    app.include_router(build_projects_router(
        api_key=settings.app_api_key,
        session_dep=lambda: get_session(engine),
        on_sort_requested=_on_sort_requested,
    ))
    app.include_router(build_webhook_router(
        registry=registry,
        debouncer=debouncer,
        suppression=suppression,
        session_dep=lambda: get_session(engine),
        default_delay=settings.default_debounce_seconds,
    ))
    return app
