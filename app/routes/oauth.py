import httpx
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.setup import mark_todoist_authorized

_TOKEN_URL = "https://todoist.com/oauth/access_token"


def build_oauth_router(
    *, client_id: str, client_secret: str, database_url: str = "sqlite:///./data/app.db"
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/oauth/callback",
        response_class=HTMLResponse,
        tags=["oauth"],
        summary="Todoist OAuth redirect target",
        description=(
            "Redirect target for the Todoist OAuth authorise flow. "
            "Exchanges the `code` query parameter for an access token "
            "so Todoist marks the app as \"installed\" and starts "
            "delivering webhooks. The access token itself is discarded; "
            "the service uses the personal `TODOIST_API_TOKEN` from "
            "the environment for all subsequent Todoist API calls. "
            "On success, a marker file is written so `/setup` can "
            "display the authorised state."
        ),
        responses={
            200: {"description": "OAuth flow completed successfully."},
            400: {"description": "Missing `code` or user denied access."},
            500: {"description": "Todoist token exchange failed."},
        },
    )
    async def callback(
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
        error_description: str | None = None,
    ):
        if error:
            return HTMLResponse(
                f"<h1>Authorization failed</h1>"
                f"<p><strong>{error}</strong>: {error_description or ''}</p>",
                status_code=400,
            )
        if not code:
            return HTMLResponse("<h1>Missing authorization code</h1>", status_code=400)

        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(_TOKEN_URL, data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
            })

        if r.is_error:
            return HTMLResponse(
                "<h1>Token exchange failed</h1>"
                f"<p>Todoist returned <code>{r.status_code}</code>:</p>"
                f"<pre>{r.text[:500]}</pre>",
                status_code=500,
            )

        mark_todoist_authorized(database_url)

        return HTMLResponse(
            "<!doctype html><html><head><title>Installed</title>"
            "<meta charset='utf-8'></head><body style='font-family:system-ui;"
            "max-width:40em;margin:3em auto;padding:1em;'>"
            "<h1>\u2713 App installed</h1>"
            "<p>The todolist-sorter is now authorized for your Todoist account. "
            "Webhooks will fire on item:added and item:updated events.</p>"
            "<p>You can close this tab.</p></body></html>"
        )

    return router
