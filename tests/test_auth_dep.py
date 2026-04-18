from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.routes.deps import require_api_key


def _app(expected: str) -> FastAPI:
    app = FastAPI()

    @app.get("/guarded", dependencies=[Depends(require_api_key(expected))])
    def _g():
        return {"ok": True}

    return app


def test_missing_key_rejected():
    c = TestClient(_app("s3cret"))
    assert c.get("/guarded").status_code == 401


def test_wrong_key_rejected():
    c = TestClient(_app("s3cret"))
    assert c.get("/guarded", headers={"X-API-Key": "wrong"}).status_code == 401


def test_correct_key_accepted():
    c = TestClient(_app("s3cret"))
    assert c.get("/guarded", headers={"X-API-Key": "s3cret"}).status_code == 200
