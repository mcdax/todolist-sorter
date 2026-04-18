from fastapi.testclient import TestClient


def test_healthz(monkeypatch, tmp_path):
    db = tmp_path / "test.db"
    monkeypatch.setenv("TODOIST_CLIENT_SECRET", "s")
    monkeypatch.setenv("TODOIST_API_TOKEN", "t")
    monkeypatch.setenv("LLM_MODEL", "anthropic:claude-sonnet-4-6")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("APP_API_KEY", "app")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")

    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
