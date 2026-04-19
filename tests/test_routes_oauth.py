import urllib.parse

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.oauth import build_oauth_router


def _client():
    app = FastAPI()
    app.include_router(build_oauth_router(client_id="CID", client_secret="CSEC"))
    return TestClient(app)


def test_callback_success(respx_mock):
    route = respx_mock.post("https://todoist.com/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "TKN", "token_type": "Bearer"})
    )
    r = _client().get("/oauth/callback?code=abc&state=x")
    assert r.status_code == 200
    assert "App installed" in r.text
    body = urllib.parse.parse_qs(route.calls.last.request.content.decode())
    assert body["code"] == ["abc"]
    assert body["client_id"] == ["CID"]
    assert body["client_secret"] == ["CSEC"]


def test_callback_exchange_failure(respx_mock):
    respx_mock.post("https://todoist.com/oauth/access_token").mock(
        return_value=httpx.Response(400, text="bad_verification_code")
    )
    r = _client().get("/oauth/callback?code=bad&state=x")
    assert r.status_code == 500
    assert "Token exchange failed" in r.text
    assert "bad_verification_code" in r.text


def test_callback_missing_code():
    r = _client().get("/oauth/callback?state=x")
    assert r.status_code == 400
    assert "Missing authorization code" in r.text


def test_callback_user_denied():
    r = _client().get("/oauth/callback?error=access_denied&error_description=User+denied&state=x")
    assert r.status_code == 400
    assert "access_denied" in r.text
