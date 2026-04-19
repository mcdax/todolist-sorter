from app.config import Settings, get_settings


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("TODOIST_CLIENT_ID", "cid")
    monkeypatch.setenv("TODOIST_CLIENT_SECRET", "secret")
    monkeypatch.setenv("TODOIST_API_TOKEN", "token")
    monkeypatch.setenv("LLM_MODEL", "anthropic:claude-sonnet-4-6")
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("APP_API_KEY", "app-key")
    get_settings.cache_clear()

    s = Settings()

    assert s.todoist_client_id == "cid"
    assert s.todoist_client_secret == "secret"
    assert s.todoist_api_token == "token"
    assert s.llm_model == "anthropic:claude-sonnet-4-6"
    assert s.llm_api_key == "llm-key"
    assert s.app_api_key == "app-key"
    assert s.database_url == "sqlite:///./data/app.db"
    assert s.default_debounce_seconds == 5
    assert s.suppression_window_seconds == 30
