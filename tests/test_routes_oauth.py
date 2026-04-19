import urllib.parse

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.oauth import build_oauth_router


def _client(tmp_path=None):
    app = FastAPI()
    db_url = f"sqlite:///{tmp_path}/app.db" if tmp_path else "sqlite:///./data/app.db"
    app.include_router(
        build_oauth_router(client_id="CID", client_secret="CSEC", database_url=db_url)
    )
    return TestClient(app)


def test_callback_success(respx_mock, tmp_path):
    route = respx_mock.post("https://todoist.com/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "TKN", "token_type": "Bearer"})
    )
    r = _client(tmp_path).get("/oauth/callback?code=abc&state=x")
    assert r.status_code == 200
    assert "App installed" in r.text
    body = urllib.parse.parse_qs(route.calls.last.request.content.decode())
    assert body["code"] == ["abc"]
    assert body["client_id"] == ["CID"]
    assert body["client_secret"] == ["CSEC"]
    # marker file should have been written
    assert (tmp_path / ".todoist_authorized").exists()


def test_callback_exchange_failure(respx_mock, tmp_path):
    respx_mock.post("https://todoist.com/oauth/access_token").mock(
        return_value=httpx.Response(400, text="bad_verification_code")
    )
    r = _client(tmp_path).get("/oauth/callback?code=bad&state=x")
    assert r.status_code == 500
    assert "Token exchange failed" in r.text
    assert "bad_verification_code" in r.text


def test_callback_missing_code(tmp_path):
    r = _client(tmp_path).get("/oauth/callback?state=x")
    assert r.status_code == 400
    assert "Missing authorization code" in r.text


def test_callback_user_denied(tmp_path):
    r = _client(tmp_path).get("/oauth/callback?error=access_denied&error_description=User+denied&state=x")
    assert r.status_code == 400
    assert "access_denied" in r.text
