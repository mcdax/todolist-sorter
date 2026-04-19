"""Tests for app/setup.py — compute_setup_status, resolve_app_api_key, marker file."""
import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import app.setup as setup_mod
from app.setup import (
    PLACEHOLDER_APP_API_KEY,
    PLACEHOLDER_CLIENT_ID,
    PLACEHOLDER_CLIENT_SECRET,
    PLACEHOLDER_API_TOKEN,
    PLACEHOLDER_LLM_API_KEY,
    compute_setup_status,
    is_auto_generated,
    is_todoist_authorized,
    mark_todoist_authorized,
    resolve_app_api_key,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_settings(**kwargs):
    defaults = {
        "todoist_client_id":     PLACEHOLDER_CLIENT_ID,
        "todoist_client_secret": PLACEHOLDER_CLIENT_SECRET,
        "todoist_api_token":     PLACEHOLDER_API_TOKEN,
        "llm_api_key":           PLACEHOLDER_LLM_API_KEY,
        "llm_model":             "anthropic:claude-sonnet-4-6",
        "app_api_key":           PLACEHOLDER_APP_API_KEY,
        "database_url":          "sqlite:///./data/app.db",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _fake_request(scheme="https", host="example.com"):
    url = MagicMock()
    url.scheme = scheme
    url.netloc = host
    req = MagicMock()
    req.url = url
    return req


# ---------------------------------------------------------------------------
# compute_setup_status — all placeholders
# ---------------------------------------------------------------------------


def test_compute_setup_status_all_placeholder():
    settings = _fake_settings()
    request = _fake_request()
    status = compute_setup_status(request, settings, projects_count=0, authorized=False)

    creds = status["credentials"]
    for field in ("todoist_client_id", "todoist_client_secret", "todoist_api_token",
                  "llm_api_key", "app_api_key"):
        assert creds[field]["set"] is True, f"{field} should appear as set (placeholder)"
        assert creds[field]["placeholder"] is True, f"{field} should be placeholder"

    # With placeholder client_id, authorize_url should be empty
    assert status["oauth"]["authorize_url"] == ""
    assert status["todoist_authorized"] is False
    assert status["projects_count"] == 0


def test_compute_setup_status_real_values():
    settings = _fake_settings(
        todoist_client_id="real-client-id",
        todoist_client_secret="real-client-secret",
        todoist_api_token="real-api-token",
        llm_api_key="real-llm-key",
        app_api_key="real-app-key",
    )
    request = _fake_request(scheme="https", host="myapp.example.com")
    status = compute_setup_status(request, settings, projects_count=2, authorized=True)

    creds = status["credentials"]
    for field in ("todoist_client_id", "todoist_client_secret", "todoist_api_token",
                  "llm_api_key", "app_api_key"):
        assert creds[field]["set"] is True
        assert creds[field]["placeholder"] is False

    assert "real-client-id" in status["oauth"]["authorize_url"]
    assert "oauth" in status["oauth"]["authorize_url"]
    assert "callback" in status["oauth"]["authorize_url"]
    assert status["todoist_authorized"] is True
    assert status["projects_count"] == 2


def test_compute_setup_status_redirect_uri_https():
    settings = _fake_settings(todoist_client_id="cid")
    request = _fake_request(scheme="https", host="prod.example.com")
    status = compute_setup_status(request, settings, projects_count=0, authorized=False)
    assert status["oauth"]["redirect_uri_matches"] is True
    assert status["oauth"]["redirect_uri"] == "https://prod.example.com/oauth/callback"


def test_compute_setup_status_redirect_uri_localhost():
    settings = _fake_settings(todoist_client_id="cid")
    request = _fake_request(scheme="http", host="localhost:8000")
    status = compute_setup_status(request, settings, projects_count=0, authorized=False)
    assert status["oauth"]["redirect_uri_matches"] is True


def test_compute_setup_status_redirect_uri_non_local_http():
    settings = _fake_settings(todoist_client_id="cid")
    request = _fake_request(scheme="http", host="public.example.com")
    status = compute_setup_status(request, settings, projects_count=0, authorized=False)
    assert status["oauth"]["redirect_uri_matches"] is False


def test_compute_status_empty_client_id_gives_no_url():
    settings = _fake_settings(todoist_client_id="")
    request = _fake_request()
    status = compute_setup_status(request, settings, projects_count=0, authorized=False)
    assert status["oauth"]["authorize_url"] == ""
    creds = status["credentials"]
    assert creds["todoist_client_id"]["set"] is False


# ---------------------------------------------------------------------------
# Auto-generated flag in compute_setup_status
# ---------------------------------------------------------------------------


def test_auto_generated_flag_surfaces(monkeypatch):
    monkeypatch.setattr(setup_mod, "_AUTO_GENERATED", True)
    settings = _fake_settings(app_api_key="some-real-key")
    request = _fake_request()
    status = compute_setup_status(request, settings, projects_count=0, authorized=False)
    assert status["credentials"]["app_api_key"]["auto_generated"] is True


def test_auto_generated_flag_false_by_default(monkeypatch):
    monkeypatch.setattr(setup_mod, "_AUTO_GENERATED", False)
    settings = _fake_settings(app_api_key="some-real-key")
    request = _fake_request()
    status = compute_setup_status(request, settings, projects_count=0, authorized=False)
    assert status["credentials"]["app_api_key"]["auto_generated"] is False


# ---------------------------------------------------------------------------
# resolve_app_api_key
# ---------------------------------------------------------------------------


def test_resolve_returns_real_key_unchanged(tmp_path):
    settings = _fake_settings(
        app_api_key="my-real-key",
        database_url=f"sqlite:///{tmp_path}/app.db",
    )
    result = resolve_app_api_key(settings)
    assert result == "my-real-key"
    assert not is_auto_generated()


def test_resolve_generates_when_placeholder(tmp_path):
    settings = _fake_settings(
        app_api_key=PLACEHOLDER_APP_API_KEY,
        database_url=f"sqlite:///{tmp_path}/app.db",
    )
    result = resolve_app_api_key(settings)
    assert result != PLACEHOLDER_APP_API_KEY
    assert len(result) >= 32
    assert is_auto_generated()

    # Side file should exist
    key_file = tmp_path / ".api_key"
    assert key_file.exists()
    assert key_file.read_text().strip() == result


def test_resolve_reads_existing_side_file(tmp_path):
    key_file = tmp_path / ".api_key"
    key_file.write_text("pre-existing-key\n", encoding="utf-8")

    settings = _fake_settings(
        app_api_key=PLACEHOLDER_APP_API_KEY,
        database_url=f"sqlite:///{tmp_path}/app.db",
    )
    result = resolve_app_api_key(settings)
    assert result == "pre-existing-key"
    # Reading an existing file should NOT set auto_generated
    assert not is_auto_generated()


def test_resolve_generates_when_empty(tmp_path):
    settings = _fake_settings(
        app_api_key="",
        database_url=f"sqlite:///{tmp_path}/app.db",
    )
    result = resolve_app_api_key(settings)
    assert len(result) >= 32
    assert is_auto_generated()


def test_resolve_second_call_reads_file(tmp_path):
    """Calling resolve twice on placeholder → same key both times."""
    settings = _fake_settings(
        app_api_key=PLACEHOLDER_APP_API_KEY,
        database_url=f"sqlite:///{tmp_path}/app.db",
    )
    key1 = resolve_app_api_key(settings)
    key2 = resolve_app_api_key(settings)
    # Second call should read from file
    assert key1 == key2


# ---------------------------------------------------------------------------
# todoist_authorized marker file
# ---------------------------------------------------------------------------


def test_marker_file_not_present(tmp_path):
    db_url = f"sqlite:///{tmp_path}/app.db"
    assert is_todoist_authorized(db_url) is False


def test_mark_and_detect_authorized(tmp_path):
    db_url = f"sqlite:///{tmp_path}/app.db"
    mark_todoist_authorized(db_url)
    assert is_todoist_authorized(db_url) is True
    assert (tmp_path / ".todoist_authorized").exists()


def test_mark_authorized_idempotent(tmp_path):
    db_url = f"sqlite:///{tmp_path}/app.db"
    mark_todoist_authorized(db_url)
    mark_todoist_authorized(db_url)  # should not raise
    assert is_todoist_authorized(db_url) is True
