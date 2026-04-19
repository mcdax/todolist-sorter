"""
Setup / onboarding routes.

GET /setup        → HTML onboarding page (no auth)
GET /setup/status → JSON status dict (no auth)
"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.setup import PLACEHOLDER_APP_API_KEY


def build_setup_router(
    *,
    settings,
    get_setup_status: Callable,
) -> APIRouter:
    """
    Parameters
    ----------
    settings
        The application Settings object.
    get_setup_status
        A callable(request) → dict that returns the status dict as produced by
        compute_setup_status.  It receives the FastAPI Request so it can derive
        the redirect_uri from the actual host.
    """
    router = APIRouter(tags=["setup"])

    @router.get("/setup/status")
    def setup_status(request: Request):
        status = get_setup_status(request)
        return JSONResponse(status)

    @router.get("/setup", response_class=HTMLResponse)
    def setup_page(request: Request):
        status = get_setup_status(request)
        html = _render_setup_html(status)
        return HTMLResponse(html)

    return router


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

def _cred_row(label: str, info: dict) -> str:
    extra = info.get("auto_generated", False)
    if not info["set"]:
        icon = "&#10007;"
        note = "not set"
        cls = "bad"
    elif info["placeholder"]:
        icon = "&#10007;"
        note = "placeholder &mdash; please set a real value"
        cls = "bad"
    else:
        icon = "&#10003;"
        note = "auto-generated" if extra else "ok"
        cls = "ok"
    return (
        f'<tr class="{cls}">'
        f"<td>{icon}</td>"
        f"<td><code>{label}</code></td>"
        f"<td>{note}</td>"
        "</tr>"
    )


def _render_setup_html(status: dict) -> str:
    creds = status["credentials"]
    oauth = status["oauth"]
    authorize_url = oauth["authorize_url"]
    redirect_uri = oauth["redirect_uri"]
    redirect_matches = oauth["redirect_uri_matches"]
    projects_count = status["projects_count"]
    authorized = status["todoist_authorized"]
    llm_model = status["llm_model"]

    # Credential rows
    cred_rows = "".join([
        _cred_row("TODOIST_CLIENT_ID",     creds["todoist_client_id"]),
        _cred_row("TODOIST_CLIENT_SECRET", creds["todoist_client_secret"]),
        _cred_row("TODOIST_API_TOKEN",     creds["todoist_api_token"]),
        _cred_row("LLM_API_KEY",           creds["llm_api_key"]),
        _cred_row("APP_API_KEY",           creds["app_api_key"]),
    ])

    # OAuth button
    if authorize_url:
        auth_button = (
            f'<a class="button" href="{authorize_url}">Authorize with Todoist</a>'
        )
    else:
        auth_button = (
            '<button class="button disabled" disabled>'
            "Authorize with Todoist"
            "<br><small>(set TODOIST_CLIENT_ID first)</small>"
            "</button>"
        )

    # Non-localhost http warning
    redirect_warning = ""
    if not redirect_matches:
        redirect_warning = (
            '<p class="warn">&#9888; Your redirect URI uses <strong>http://</strong> '
            "on a non-localhost host. Todoist requires https for non-local apps. "
            "Deploy behind TLS before authorising.</p>"
        )

    authorized_display = "yes &#10003;" if authorized else "no"
    base_url = redirect_uri.rsplit("/oauth/callback", 1)[0]

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Todolist Sorter &mdash; Setup</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      font-family: system-ui, sans-serif;
      max-width: 42em;
      margin: 3em auto;
      padding: 1em;
      color: #222;
      background: #fafafa;
    }}
    h1 {{ font-size: 1.6em; margin-bottom: .3em; }}
    h2 {{ font-size: 1.1em; margin-top: 2em; border-bottom: 1px solid #ddd; padding-bottom: .3em; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
    td, th {{ padding: .35em .6em; text-align: left; border: 1px solid #ddd; }}
    tr.ok td {{ background: #efffef; }}
    tr.bad td {{ background: #fff4f4; }}
    code {{ font-family: ui-monospace, monospace; font-size: .9em; }}
    .button {{
      display: inline-block;
      padding: .6em 1.4em;
      background: #db4035;
      color: #fff;
      border: none;
      border-radius: 4px;
      font-size: 1em;
      cursor: pointer;
      text-decoration: none;
      margin-top: .5em;
    }}
    .button.disabled {{ background: #ccc; cursor: not-allowed; }}
    .warn {{ color: #b45000; background: #fff8e1; border-left: 3px solid #f0a500; padding: .5em .8em; }}
    ol li {{ margin: .4em 0; }}
    footer {{ margin-top: 3em; font-size: .85em; color: #888; }}
  </style>
</head>
<body>
<h1>Todolist Sorter &mdash; Setup</h1>

<h2>Credential status</h2>
<table>
  <thead><tr><th></th><th>Variable</th><th>Status</th></tr></thead>
  <tbody>
  {cred_rows}
  </tbody>
</table>

<p><strong>Sorting projects configured:</strong> {projects_count}</p>
<p><strong>LLM model:</strong> <code>{llm_model}</code></p>
<p><strong>Todoist app authorized:</strong> {authorized_display}</p>

<h2>Todoist authorization</h2>

<p>
  <strong>OAuth redirect URI</strong> (paste this into the Todoist App Console):<br>
  <code>{redirect_uri}</code>
</p>
{redirect_warning}

{auth_button}

<h2>Step-by-step setup</h2>
<ol>
  <li>
    Create a Todoist app at
    <a href="https://developer.todoist.com/appconsole.html" target="_blank" rel="noopener">
      developer.todoist.com/appconsole.html</a>.
  </li>
  <li>
    Copy <strong>Client ID</strong> and <strong>Client Secret</strong> into your
    <code>.env</code> file as <code>TODOIST_CLIENT_ID</code> and
    <code>TODOIST_CLIENT_SECRET</code>.
  </li>
  <li>
    Generate a personal API token at
    <a href="https://todoist.com/app/settings/integrations/developer" target="_blank" rel="noopener">
      todoist.com/app/settings/integrations/developer</a>
    and set it as <code>TODOIST_API_TOKEN</code> in <code>.env</code>.
  </li>
  <li>
    In the Todoist App Console, set the <strong>OAuth redirect URI</strong> to:<br>
    <code>{redirect_uri}</code>
  </li>
  <li>
    Enable <strong>item:added</strong> and <strong>item:updated</strong> webhook
    events with callback URL:<br>
    <code>{base_url}/webhook/todoist</code>
  </li>
  <li>Click the <em>Authorize with Todoist</em> button above.</li>
  <li>
    Once installed, create a sorting project via the CLI
    (<code>todolist-sorter projects create</code>) or the
    <a href="/docs">Swagger UI at /docs</a>.
  </li>
</ol>

<footer>
  <a href="/docs">API docs (Swagger UI)</a> &bull;
  <a href="/setup/status">/setup/status (JSON)</a>
</footer>
</body>
</html>"""
    return html
