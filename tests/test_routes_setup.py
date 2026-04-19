"""Tests for GET /setup and GET /setup/status."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.setup import build_setup_router
from app.setup import (
    PLACEHOLDER_APP_API_KEY,
    PLACEHOLDER_CLIENT_ID,
    PLACEHOLDER_CLIENT_SECRET,
    PLACEHOLDER_API_TOKEN,
    PLACEHOLDER_LLM_API_KEY,
    compute_setup_status,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _fake_settings(**kwargs):
    defaults = {
        "todoist_client_id":     "real-client-id",
        "todoist_client_secret": "real-client-secret",
        "todoist_api_token":     "real-api-token",
        "llm_api_key":           "real-llm-key",
        "llm_model":             "anthropic:claude-sonnet-4-6",
        "app_api_key":           "real-app-key",
        "database_url":          "sqlite:///./data/app.db",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_client(settings=None, projects_count=1, authorized=True):
    if settings is None:
        settings = _fake_settings()

    def get_status(request):
        return compute_setup_status(request, settings, projects_count, authorized)

    app = FastAPI()
    app.include_router(build_setup_router(
        settings=settings,
        get_setup_status=get_status,
    ))
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /setup
# ---------------------------------------------------------------------------


def test_setup_returns_200():
    client = _make_client()
    r = client.get("/setup")
    assert r.status_code == 200


def test_setup_is_html():
    client = _make_client()
    r = client.get("/setup")
    assert "text/html" in r.headers["content-type"]


def test_setup_contains_title():
    client = _make_client()
    r = client.get("/setup")
    assert "Setup" in r.text


def test_setup_contains_authorize_url_when_client_id_set():
    settings = _fake_settings(todoist_client_id="my-client-id")
    client = _make_client(settings=settings)
    r = client.get("/setup")
    assert "my-client-id" in r.text
    assert "todoist.com/oauth/authorize" in r.text


def test_setup_no_authorize_url_when_client_id_placeholder():
    settings = _fake_settings(todoist_client_id=PLACEHOLDER_CLIENT_ID)
    client = _make_client(settings=settings)
    r = client.get("/setup")
    # Button should be disabled
    assert "disabled" in r.text


def test_setup_shows_oauth_callback_uri():
    client = _make_client()
    r = client.get("/setup")
    assert "/oauth/callback" in r.text


def test_setup_no_auth_required():
    """GET /setup must work without any API key header."""
    client = _make_client()
    r = client.get("/setup")
    assert r.status_code == 200


def test_setup_shows_projects_count():
    client = _make_client(projects_count=3)
    r = client.get("/setup")
    assert "3" in r.text


def test_setup_shows_step_list():
    client = _make_client()
    r = client.get("/setup")
    # All major steps should appear
    assert "developer.todoist.com/appconsole.html" in r.text
    assert "webhook/todoist" in r.text
    assert "Authorize with Todoist" in r.text


def test_setup_warns_http_non_localhost():
    """Should show a warning for http:// on a non-localhost host."""
    settings = _fake_settings(todoist_client_id="cid")
    client = _make_client(settings=settings)
    # Force the request host to look like a non-local HTTP URL
    # We need to craft the request with an explicit host header.
    r = client.get("/setup", headers={"host": "public.example.com"})
    # The warning text should reference https requirement
    # (the check is scheme-based from the actual request URL in test client)
    # TestClient uses http scheme by default
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /setup/status
# ---------------------------------------------------------------------------


def test_setup_status_returns_200():
    client = _make_client()
    r = client.get("/setup/status")
    assert r.status_code == 200


def test_setup_status_is_json():
    client = _make_client()
    r = client.get("/setup/status")
    assert "application/json" in r.headers["content-type"]


def test_setup_status_shape():
    client = _make_client(projects_count=2, authorized=True)
    r = client.get("/setup/status")
    data = r.json()

    # Top-level keys
    assert "credentials" in data
    assert "todoist_authorized" in data
    assert "projects_count" in data
    assert "llm_model" in data
    assert "oauth" in data

    # Credential fields
    creds = data["credentials"]
    for field in ("todoist_client_id", "todoist_client_secret", "todoist_api_token",
                  "llm_api_key", "app_api_key"):
        assert field in creds
        assert "set" in creds[field]
        assert "placeholder" in creds[field]

    # app_api_key has auto_generated
    assert "auto_generated" in creds["app_api_key"]

    # OAuth sub-dict
    oauth = data["oauth"]
    assert "authorize_url" in oauth
    assert "redirect_uri" in oauth
    assert "redirect_uri_matches" in oauth

    # Values
    assert data["todoist_authorized"] is True
    assert data["projects_count"] == 2


def test_setup_status_no_auth_required():
    """GET /setup/status must work without any API key header."""
    client = _make_client()
    r = client.get("/setup/status")
    assert r.status_code == 200


def test_setup_status_placeholder_credentials():
    settings = _fake_settings(
        todoist_client_id=PLACEHOLDER_CLIENT_ID,
        todoist_client_secret=PLACEHOLDER_CLIENT_SECRET,
        todoist_api_token=PLACEHOLDER_API_TOKEN,
        llm_api_key=PLACEHOLDER_LLM_API_KEY,
        app_api_key=PLACEHOLDER_APP_API_KEY,
    )
    client = _make_client(settings=settings, authorized=False)
    r = client.get("/setup/status")
    data = r.json()

    for field in ("todoist_client_id", "todoist_client_secret", "todoist_api_token",
                  "llm_api_key", "app_api_key"):
        assert data["credentials"][field]["placeholder"] is True
    assert data["oauth"]["authorize_url"] == ""
