from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    todoist_client_id: str = ""
    todoist_client_secret: str = ""
    todoist_api_token: str = ""
    llm_model: str = "anthropic:claude-sonnet-4-6"
    llm_api_key: str = ""
    llm_base_url: str = ""
    app_api_key: str = ""
    database_url: str = "sqlite:///./data/app.db"
    default_debounce_seconds: int = 5
    suppression_window_seconds: int = 30

    # Easy mode: auto-create/reconcile one sorting project from env + files
    # on startup. All fields are optional and inert unless
    # `auto_project_external_id` is non-empty.
    auto_project_external_id: str = ""
    auto_project_provider: str = "todoist"
    auto_project_name: str = "Auto"
    auto_categories_file: str = ""
    auto_instructions_file: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
