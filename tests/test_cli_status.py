"""Tests for `todolist-sorter status`."""
import httpx
import pytest
import respx
from click.testing import CliRunner

from app.cli import cli


# ---------------------------------------------------------------------------
# Status response shapes
# ---------------------------------------------------------------------------

def _status_payload(
    *,
    todoist_client_id_ok=True,
    todoist_client_secret_ok=True,
    todoist_api_token_ok=True,
    llm_api_key_ok=True,
    app_api_key_ok=True,
    app_api_key_auto=False,
    todoist_authorized=True,
    projects_count=1,
    llm_model="anthropic:claude-sonnet-4-6",
    client_id_placeholder=False,
):
    def _ci(ok, placeholder=False, auto=False):
        d = {"set": ok, "placeholder": placeholder}
        return d

    return {
        "credentials": {
            "todoist_client_id":     _ci(todoist_client_id_ok, placeholder=client_id_placeholder),
            "todoist_client_secret": _ci(todoist_client_secret_ok),
            "todoist_api_token":     _ci(todoist_api_token_ok),
            "llm_api_key":           _ci(llm_api_key_ok),
            "app_api_key":           {
                "set": app_api_key_ok,
                "placeholder": False,
                "auto_generated": app_api_key_auto,
            },
        },
        "todoist_authorized": todoist_authorized,
        "projects_count": projects_count,
        "llm_model": llm_model,
        "oauth": {
            "authorize_url": "https://todoist.com/oauth/authorize?client_id=cid&...",
            "redirect_uri": "http://localhost:8000/oauth/callback",
            "redirect_uri_matches": True,
        },
    }


def _invoke_status(payload):
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        mock.get("/setup/status").mock(return_value=httpx.Response(200, json=payload))
        result = runner.invoke(cli, ["status"], env={})
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_status_prints_url():
    payload = _status_payload()
    result = _invoke_status(payload)
    assert result.exit_code == 0
    assert "http://localhost:8000" in result.output


def test_status_shows_credentials():
    payload = _status_payload()
    result = _invoke_status(payload)
    assert result.exit_code == 0
    assert "TODOIST_CLIENT_ID" in result.output
    assert "[✓]" in result.output


def test_status_shows_missing_credential():
    payload = _status_payload(todoist_client_secret_ok=False)
    result = _invoke_status(payload)
    assert result.exit_code == 0
    assert "[✗]" in result.output
    assert "TODOIST_CLIENT_SECRET" in result.output


def test_status_shows_auto_generated():
    payload = _status_payload(app_api_key_auto=True)
    result = _invoke_status(payload)
    assert result.exit_code == 0
    assert "auto-generated" in result.output


def test_status_shows_llm_model():
    payload = _status_payload(llm_model="openai:gpt-4o")
    result = _invoke_status(payload)
    assert result.exit_code == 0
    assert "openai:gpt-4o" in result.output


def test_status_shows_projects_count():
    payload = _status_payload(projects_count=3)
    result = _invoke_status(payload)
    assert result.exit_code == 0
    assert "3" in result.output


def test_status_all_good_shows_all_set():
    payload = _status_payload(projects_count=1, todoist_authorized=True)
    result = _invoke_status(payload)
    assert result.exit_code == 0
    assert "all set" in result.output


def test_status_missing_cred_next_step():
    payload = _status_payload(todoist_client_id_ok=False)
    result = _invoke_status(payload)
    assert result.exit_code == 0
    assert "TODOIST_CLIENT_ID" in result.output
    assert ".env" in result.output


def test_status_placeholder_cred_next_step():
    payload = _status_payload(client_id_placeholder=True)
    result = _invoke_status(payload)
    assert result.exit_code == 0
    assert ".env" in result.output


def test_status_not_authorized_next_step():
    payload = _status_payload(todoist_authorized=False, projects_count=0)
    result = _invoke_status(payload)
    assert result.exit_code == 0
    assert "/setup" in result.output


def test_status_authorized_no_projects_next_step():
    payload = _status_payload(todoist_authorized=True, projects_count=0)
    result = _invoke_status(payload)
    assert result.exit_code == 0
    assert "projects create" in result.output


def test_status_works_without_api_key():
    """status must work even with no API key env var."""
    payload = _status_payload()
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        mock.get("/setup/status").mock(return_value=httpx.Response(200, json=payload))
        result = runner.invoke(cli, ["status"], env={})  # no TODOLIST_SORTER_API_KEY
    assert result.exit_code == 0


def test_status_server_error_exits_nonzero():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        mock.get("/setup/status").mock(return_value=httpx.Response(500, text="boom"))
        result = runner.invoke(cli, ["status"], env={})
    assert result.exit_code != 0
