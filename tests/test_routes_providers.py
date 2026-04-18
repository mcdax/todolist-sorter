from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backends.base import ProviderProject
from app.backends.registry import BackendRegistry
from app.routes.providers import build_providers_router

AUTH = {"X-API-Key": "testkey"}


class _FakeBackend:
    name = "todoist"

    async def list_projects(self):
        return [
            ProviderProject(id="111", name="Lidl"),
            ProviderProject(id="222", name="Home"),
        ]


@pytest.fixture()
def providers_client():
    registry = BackendRegistry()
    registry.register(_FakeBackend())
    app = FastAPI()
    app.include_router(build_providers_router(
        api_key="testkey", registry=registry,
    ))
    return TestClient(app)


def test_list_remote_projects(providers_client):
    r = providers_client.get("/providers/todoist/projects", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == [
        {"id": "111", "name": "Lidl"},
        {"id": "222", "name": "Home"},
    ]


def test_unknown_provider_404(providers_client):
    r = providers_client.get("/providers/ticktick/projects", headers=AUTH)
    assert r.status_code == 404


def test_requires_api_key(providers_client):
    r = providers_client.get("/providers/todoist/projects")
    assert r.status_code == 401
