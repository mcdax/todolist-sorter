"""
Setup helpers: placeholder detection, auto-generated APP_API_KEY, status computation.
"""
from __future__ import annotations

import logging
import os
import secrets
import stat
from pathlib import Path
from urllib.parse import quote_plus

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known placeholder strings from .env.example
# ---------------------------------------------------------------------------

PLACEHOLDER_CLIENT_ID = "your-todoist-client-id"
PLACEHOLDER_CLIENT_SECRET = "your-todoist-webhook-client-secret"
PLACEHOLDER_API_TOKEN = "your-todoist-api-token"
PLACEHOLDER_LLM_API_KEY = "your-llm-api-key"
PLACEHOLDER_APP_API_KEY = "generate-a-long-random-string"

_PLACEHOLDERS: dict[str, str] = {
    "todoist_client_id":     PLACEHOLDER_CLIENT_ID,
    "todoist_client_secret": PLACEHOLDER_CLIENT_SECRET,
    "todoist_api_token":     PLACEHOLDER_API_TOKEN,
    "llm_api_key":           PLACEHOLDER_LLM_API_KEY,
    "app_api_key":           PLACEHOLDER_APP_API_KEY,
}

# ---------------------------------------------------------------------------
# Auto-generated APP_API_KEY state
# ---------------------------------------------------------------------------

_AUTO_GENERATED: bool = False


def is_auto_generated() -> bool:
    """Return True if APP_API_KEY was auto-generated during this process lifetime."""
    return _AUTO_GENERATED


def _set_auto_generated(value: bool) -> None:
    global _AUTO_GENERATED
    _AUTO_GENERATED = value


# ---------------------------------------------------------------------------
# Determine data dir from DATABASE_URL
# ---------------------------------------------------------------------------

def _data_dir_from_db_url(database_url: str) -> Path:
    """
    For sqlite:///./data/app.db  → ./data
    For anything else            → ./data
    """
    if database_url.startswith("sqlite:///"):
        path_part = database_url[len("sqlite:///"):]
        return Path(path_part).parent
    return Path("./data")


def _api_key_path(database_url: str) -> Path:
    return _data_dir_from_db_url(database_url) / ".api_key"


# ---------------------------------------------------------------------------
# resolve_app_api_key
# ---------------------------------------------------------------------------

def resolve_app_api_key(settings) -> str:
    """
    If settings.app_api_key is empty or the known placeholder, read or generate
    a side-file key from <data_dir>/.api_key.

    Returns the resolved key string.  Also sets the module-level _AUTO_GENERATED
    flag when a new key is written.
    """
    _set_auto_generated(False)

    current = getattr(settings, "app_api_key", "") or ""
    placeholder = _PLACEHOLDERS["app_api_key"]

    if current and current != placeholder:
        # A real key is already configured — nothing to do.
        return current

    key_path = _api_key_path(settings.database_url)

    if key_path.exists():
        key = key_path.read_text(encoding="utf-8").strip()
        if key:
            return key

    # Generate a fresh key and persist it.
    key = secrets.token_urlsafe(32)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(key, encoding="utf-8")
    key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    _set_auto_generated(True)
    log.warning("Auto-generated APP_API_KEY: %s (saved to %s)", key, key_path)
    return key


# ---------------------------------------------------------------------------
# compute_setup_status
# ---------------------------------------------------------------------------

def _cred_info(value: str, field: str) -> dict:
    placeholder_val = _PLACEHOLDERS.get(field, "")
    is_set = bool(value)
    is_placeholder = is_set and value == placeholder_val
    return {"set": is_set, "placeholder": is_placeholder}


def compute_setup_status(request, settings, projects_count: int, authorized: bool) -> dict:
    """
    Build the full status dict consumed by GET /setup/status and the HTML page.

    ``request`` is a FastAPI/Starlette Request (used to derive redirect_uri).
    """
    app_key_info = _cred_info(settings.app_api_key, "app_api_key")
    app_key_info["auto_generated"] = is_auto_generated()

    creds = {
        "todoist_client_id":     _cred_info(settings.todoist_client_id,     "todoist_client_id"),
        "todoist_client_secret": _cred_info(settings.todoist_client_secret,  "todoist_client_secret"),
        "todoist_api_token":     _cred_info(settings.todoist_api_token,      "todoist_api_token"),
        "llm_api_key":           _cred_info(settings.llm_api_key,            "llm_api_key"),
        "app_api_key":           app_key_info,
    }

    # Build OAuth info from request
    scheme = request.url.scheme
    host = request.url.netloc  # host[:port]
    redirect_uri = f"{scheme}://{host}/oauth/callback"
    redirect_uri_matches = (scheme == "https") or (host.split(":")[0] in ("localhost", "127.0.0.1"))

    client_id = settings.todoist_client_id
    client_id_ok = client_id and not creds["todoist_client_id"]["placeholder"]
    if client_id_ok:
        authorize_url = (
            "https://todoist.com/oauth/authorize"
            f"?client_id={client_id}"
            "&scope=data:read_write"
            "&state=setup"
            f"&redirect_uri={quote_plus(redirect_uri)}"
        )
    else:
        authorize_url = ""

    return {
        "credentials": creds,
        "todoist_authorized": authorized,
        "projects_count": projects_count,
        "llm_model": settings.llm_model,
        "oauth": {
            "authorize_url": authorize_url,
            "redirect_uri": redirect_uri,
            "redirect_uri_matches": redirect_uri_matches,
        },
    }


# ---------------------------------------------------------------------------
# todoist_authorized marker file
# ---------------------------------------------------------------------------

def _authorized_marker_path(database_url: str) -> Path:
    return _data_dir_from_db_url(database_url) / ".todoist_authorized"


def mark_todoist_authorized(database_url: str) -> None:
    """Write the marker file that indicates a successful OAuth callback."""
    path = _authorized_marker_path(database_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def is_todoist_authorized(database_url: str) -> bool:
    """Return True if the marker file exists (callback ever succeeded)."""
    return _authorized_marker_path(database_url).exists()
