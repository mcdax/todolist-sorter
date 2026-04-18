import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.models  # ensure all SQLModel tables are registered before create_all  # noqa: F401


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s


from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine


@pytest.fixture()
def sort_trigger_spy():
    calls: list = []

    def spy(pid):
        calls.append(pid)

    return calls, spy


@pytest.fixture()
def app_with_projects(engine: Engine, sort_trigger_spy):
    from app.db import get_session
    from app.routes.projects import build_router

    _, spy = sort_trigger_spy
    app = FastAPI()
    app.include_router(build_router(
        api_key="testkey",
        session_dep=lambda: get_session(engine),
        on_sort_requested=spy,
    ))
    return app


@pytest.fixture()
def client(app_with_projects):
    return TestClient(app_with_projects)
