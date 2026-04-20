import asyncio
import logging
import os
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI
from sqlmodel import Session, select

from app.backends.registry import BackendRegistry
from app.backends.todoist import TodoistBackend
from app.config import get_settings
from app.db import create_db_and_tables, get_session, make_engine
from app.debouncer import ProjectDebouncer
from app.models import SortingProject
from app.routes.oauth import build_oauth_router
from app.routes.projects import build_router as build_projects_router
from app.routes.providers import build_providers_router
from app.routes.setup import build_setup_router
from app.routes.webhook import build_webhook_router
from app.setup import (
    compute_setup_status,
    is_todoist_authorized,
    resolve_app_api_key,
)
from app.sorter import sort_project
from app.suppression import SuppressionTracker


# pydantic-ai picks credentials up from provider-specific env vars.
# Map the prefix of `LLM_MODEL` (e.g. "anthropic:claude-...") to the right one
# so users only have to set `LLM_API_KEY` in .env. For OpenAI-compatible
# providers (ollama, openai) a custom endpoint can be set via `LLM_BASE_URL`.
_PROVIDER_API_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "google-gla": "GOOGLE_API_KEY",
    "google-vertex": "GOOGLE_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "cohere": "COHERE_API_KEY",
    "ollama": "OLLAMA_API_KEY",
}

_PROVIDER_BASE_URL_ENV = {
    "openai": "OPENAI_BASE_URL",
    "ollama": "OLLAMA_BASE_URL",
}


def _export_llm_env(model: str, api_key: str, base_url: str) -> None:
    provider = model.split(":", 1)[0] if ":" in model else ""
    api_var = _PROVIDER_API_KEY_ENV.get(provider)
    if api_var and api_key and not os.environ.get(api_var):
        os.environ[api_var] = api_key
    url_var = _PROVIDER_BASE_URL_ENV.get(provider)
    if url_var and base_url and not os.environ.get(url_var):
        os.environ[url_var] = base_url


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    # Resolve APP_API_KEY (auto-generate if placeholder or empty)
    settings.app_api_key = resolve_app_api_key(settings)
    _export_llm_env(settings.llm_model, settings.llm_api_key, settings.llm_base_url)
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

    app = FastAPI(
        title="Todolist Sorter",
        version="0.1.0",
        description=(
            "Self-hosted service that watches a Todoist project for new "
            "items and reorders them into a user-defined category "
            "sequence using an LLM. Optionally also transforms item "
            "content (typo fixes, emoji) based on per-project "
            "`additional_instructions`.\n\n"
            "**Auth.** Management endpoints require an `X-API-Key: "
            "<APP_API_KEY>` header. The webhook, OAuth callback, "
            "health probe, and `/setup` pages are public.\n\n"
            "First-time setup: open `/setup` in a browser."
        ),
        contact={
            "name": "Source on GitHub",
            "url": "https://github.com/mcdax/todolist-sorter",
        },
        license_info={"name": "MIT"},
        lifespan=lifespan,
    )

    @app.get(
        "/healthz",
        tags=["health"],
        summary="Liveness probe",
        description="Returns `{\"status\":\"ok\"}` if the process is up. "
                    "Does not touch the database or any external service.",
    )
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
    app.include_router(build_providers_router(
        api_key=settings.app_api_key,
        registry=registry,
    ))
    app.include_router(build_oauth_router(
        client_id=settings.todoist_client_id,
        client_secret=settings.todoist_client_secret,
        database_url=settings.database_url,
    ))

    def _get_setup_status(request):
        with Session(engine) as s:
            projects_count = len(s.exec(select(SortingProject)).all())
        authorized = is_todoist_authorized(settings.database_url)
        return compute_setup_status(request, settings, projects_count, authorized)

    app.include_router(build_setup_router(
        settings=settings,
        get_setup_status=_get_setup_status,
    ))
    return app
