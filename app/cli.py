import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

import click
import httpx


# ---------------------------------------------------------------------------
# Top-level group — api-key is now optional so that 'init' and 'status'
# can work without it.
# ---------------------------------------------------------------------------


@click.group()
@click.option(
    "--url",
    envvar="TODOLIST_SORTER_URL",
    default="http://localhost:8000",
    show_default=True,
    help="Base URL of the todolist-sorter server.",
)
@click.option(
    "--api-key",
    envvar="TODOLIST_SORTER_API_KEY",
    default=None,
    help="API key for authentication (required for most commands).",
)
@click.pass_context
def cli(ctx, url, api_key):
    """Management CLI for todolist-sorter."""
    ctx.ensure_object(dict)
    ctx.obj["url"] = url
    ctx.obj["api_key"] = api_key
    # Authenticated client (built lazily in _auth_client)
    ctx.obj["_raw_url"] = url


def _auth_client(ctx) -> httpx.Client:
    """Return an httpx.Client with the API key header set.

    Raises UsageError if the API key was not provided.
    """
    api_key = ctx.obj.get("api_key")
    if not api_key:
        raise click.UsageError(
            "API key is required. Set --api-key or TODOLIST_SORTER_API_KEY."
        )
    return httpx.Client(
        base_url=ctx.obj["url"],
        headers={"X-API-Key": api_key},
        timeout=30.0,
    )


def _plain_client(ctx) -> httpx.Client:
    """Return an httpx.Client without auth headers (for public endpoints)."""
    return httpx.Client(base_url=ctx.obj["url"], timeout=30.0)


def _handle(r: httpx.Response) -> httpx.Response:
    if r.is_error:
        click.echo(f"HTTP {r.status_code}: {r.text}", err=True)
        raise SystemExit(1)
    return r


def _read_lines(path: str) -> list[str]:
    return [
        ln.strip()
        for ln in Path(path).read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


def _print_list(items: list[str]) -> None:
    for i, c in enumerate(items):
        click.echo(f"  {i}. {c}")


def _print_json(obj) -> None:
    click.echo(json.dumps(obj, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# projects group
# ---------------------------------------------------------------------------


@cli.group()
def projects():
    """Manage sorting projects."""


@projects.command("list")
@click.pass_context
def list_projects(ctx):
    """List all projects."""
    with _auth_client(ctx) as client:
        r = _handle(client.get("/projects"))
    items = r.json()
    if not items:
        click.echo("No projects found.")
        return
    for p in items:
        enabled_flag = "" if p.get("enabled", True) else " [disabled]"
        click.echo(f"  {p['id']}  {p['name']}{enabled_flag}  ({p.get('provider', '')})")


@projects.command("create")
@click.option("--name", default=None, help="Project name.")
@click.option("--external-id", default=None, help="External project ID.")
@click.option("--provider", default="todoist", show_default=True, help="Backend provider.")
@click.option("--description", default=None, help="Optional description.")
@click.option("--debounce-seconds", type=int, default=5, show_default=True,
              help="Debounce delay in seconds.")
@click.option("--categories-file", default=None, metavar="PATH",
              help="Text file with one category per line.")
@click.pass_context
def create_project(ctx, name, external_id, provider, description, debounce_seconds,
                   categories_file):
    """Create a new project."""
    with _auth_client(ctx) as client:
        if external_id is None:
            # Interactive picker: fetch remote projects and let the user choose.
            r = client.get(f"/providers/{provider}/projects")
            _handle(r)
            remote = r.json()
            if not remote:
                click.echo(f"No remote projects for provider '{provider}'", err=True)
                raise SystemExit(1)
            click.echo(f"Remote {provider} projects:")
            for i, p in enumerate(remote):
                click.echo(f"  [{i}] {p['name']}  (id={p['id']})")
            idx = click.prompt("Select number", type=int)
            if idx < 0 or idx >= len(remote):
                click.echo("out of range", err=True)
                raise SystemExit(1)
            picked = remote[idx]
            external_id = picked["id"]
            if name is None:
                name = picked["name"]

        if name is None:
            click.echo("--name is required when --external-id is given", err=True)
            raise SystemExit(1)

        categories: list[str] = []
        if categories_file:
            categories = _read_lines(categories_file)

        payload = {
            "name": name,
            "external_project_id": external_id,
            "provider": provider,
            "debounce_seconds": debounce_seconds,
            "categories": categories,
        }
        if description is not None:
            payload["description"] = description

        r = _handle(client.post("/projects", json=payload))
    _print_json(r.json())


@projects.command("show")
@click.argument("project_id")
@click.pass_context
def show_project(ctx, project_id):
    """Show details for a project."""
    with _auth_client(ctx) as client:
        r = _handle(client.get(f"/projects/{project_id}"))
    _print_json(r.json())


@projects.command("update")
@click.argument("project_id")
@click.option("--name", default=None, help="New name.")
@click.option("--description", default=None, help="New description.")
@click.option("--enabled/--disabled", "enabled", default=None,
              help="Enable or disable the project.")
@click.option("--debounce-seconds", type=int, default=None,
              help="New debounce delay in seconds.")
@click.pass_context
def update_project(ctx, project_id, name, description, enabled, debounce_seconds):
    """Update a project (only provided fields are changed)."""
    payload = {}
    if name is not None:
        payload["name"] = name
    if description is not None:
        payload["description"] = description
    if enabled is not None:
        payload["enabled"] = enabled
    if debounce_seconds is not None:
        payload["debounce_seconds"] = debounce_seconds

    if not payload:
        click.echo("Nothing to update.", err=True)
        raise SystemExit(1)

    with _auth_client(ctx) as client:
        r = _handle(client.put(f"/projects/{project_id}", json=payload))
    _print_json(r.json())


@projects.command("delete")
@click.argument("project_id")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def delete_project(ctx, project_id, yes):
    """Delete a project."""
    if not yes:
        click.confirm(f"Delete project {project_id}?", abort=True)
    with _auth_client(ctx) as client:
        _handle(client.delete(f"/projects/{project_id}"))
    click.echo(f"Deleted {project_id}")


# ---------------------------------------------------------------------------
# categories group
# ---------------------------------------------------------------------------


@cli.group()
def categories():
    """Manage project categories."""


@categories.command("list")
@click.argument("project_id")
@click.pass_context
def list_categories(ctx, project_id):
    """List categories for a project."""
    with _auth_client(ctx) as client:
        r = _handle(client.get(f"/projects/{project_id}/categories"))
    _print_list(r.json())


@categories.command("add")
@click.argument("project_id")
@click.argument("name")
@click.option("--at-index", type=int, default=None,
              help="Position to insert the category (0-based).")
@click.pass_context
def add_category(ctx, project_id, name, at_index):
    """Add a category to a project."""
    payload: dict = {"name": name}
    if at_index is not None:
        payload["at_index"] = at_index
    with _auth_client(ctx) as client:
        r = _handle(client.post(f"/projects/{project_id}/categories", json=payload))
    _print_list(r.json())


@categories.command("remove")
@click.argument("project_id")
@click.argument("index", type=int)
@click.pass_context
def remove_category(ctx, project_id, index):
    """Remove a category by index."""
    with _auth_client(ctx) as client:
        r = _handle(client.delete(f"/projects/{project_id}/categories/{index}"))
    _print_list(r.json())


@categories.command("rename")
@click.argument("project_id")
@click.argument("index", type=int)
@click.argument("new_name")
@click.pass_context
def rename_category(ctx, project_id, index, new_name):
    """Rename a category by index."""
    with _auth_client(ctx) as client:
        r = _handle(client.patch(
            f"/projects/{project_id}/categories/{index}",
            json={"name": new_name},
        ))
    _print_list(r.json())


@categories.command("move")
@click.argument("project_id")
@click.argument("index", type=int)
@click.option("--to", "target", type=int, required=True,
              help="Target index to move the category to.")
@click.pass_context
def move_category(ctx, project_id, index, target):
    """Move a category to a different index."""
    with _auth_client(ctx) as client:
        r = _handle(client.patch(
            f"/projects/{project_id}/categories/{index}",
            json={"move_to": target},
        ))
    _print_list(r.json())


@categories.command("replace")
@click.argument("project_id")
@click.argument("categories_file")
@click.pass_context
def replace_categories(ctx, project_id, categories_file):
    """Replace all categories from a file (one per line)."""
    cats = _read_lines(categories_file)
    with _auth_client(ctx) as client:
        r = _handle(client.put(
            f"/projects/{project_id}/categories",
            json={"categories": cats},
        ))
    _print_list(r.json())


# ---------------------------------------------------------------------------
# cache group
# ---------------------------------------------------------------------------


@cli.group()
def cache():
    """Manage the sorting cache."""


@cache.command("show")
@click.argument("project_id")
@click.pass_context
def show_cache(ctx, project_id):
    """Show cache entries for a project."""
    with _auth_client(ctx) as client:
        r = _handle(client.get(f"/projects/{project_id}/cache"))
    entries = r.json()
    if not entries:
        click.echo("Cache is empty.")
        return
    for entry in entries:
        click.echo(f"  {entry['content_key']}  ->  {entry['category_name']}")


@cache.command("clear")
@click.argument("project_id")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def clear_cache(ctx, project_id, yes):
    """Clear the cache for a project."""
    if not yes:
        click.confirm(f"Clear cache for project {project_id}?", abort=True)
    with _auth_client(ctx) as client:
        _handle(client.delete(f"/projects/{project_id}/cache"))
    click.echo(f"Cache cleared for {project_id}")


# ---------------------------------------------------------------------------
# remote group
# ---------------------------------------------------------------------------


@cli.group()
def remote():
    """Query remote provider state (e.g. list Todoist projects)."""


@remote.command("list")
@click.option("--provider", default="todoist", show_default=True,
              help="Backend provider.")
@click.pass_context
def list_remote_projects(ctx, provider):
    """List projects available in the remote provider."""
    with _auth_client(ctx) as client:
        r = client.get(f"/providers/{provider}/projects")
        _handle(r)
    for p in r.json():
        click.echo(f"{p['id']:14}  {p['name']}")


# ---------------------------------------------------------------------------
# sort command
# ---------------------------------------------------------------------------


@cli.command("sort")
@click.argument("project_id")
@click.pass_context
def sort_project_cmd(ctx, project_id):
    """Trigger sorting for a project."""
    with _auth_client(ctx) as client:
        r = _handle(client.post(f"/projects/{project_id}/sort"))
    click.echo(r.json().get("status", "queued"))


# ---------------------------------------------------------------------------
# status command (no auth required)
# ---------------------------------------------------------------------------


@cli.command("status")
@click.pass_context
def status_cmd(ctx):
    """Show server setup status (no API key required)."""
    url = ctx.obj["url"]
    with _plain_client(ctx) as client:
        try:
            r = client.get("/setup/status")
        except httpx.ConnectError:
            click.echo(f"Cannot connect to {url}", err=True)
            raise SystemExit(1)
        if r.is_error:
            click.echo(f"HTTP {r.status_code}: {r.text}", err=True)
            raise SystemExit(1)

    data = r.json()
    click.echo(f"\nURL: {url}\n")

    creds = data.get("credentials", {})
    click.echo("Credentials:")
    _FIELD_NAMES = {
        "todoist_client_id":     "TODOIST_CLIENT_ID",
        "todoist_client_secret": "TODOIST_CLIENT_SECRET",
        "todoist_api_token":     "TODOIST_API_TOKEN",
        "llm_api_key":           "LLM_API_KEY",
        "app_api_key":           "APP_API_KEY",
    }
    missing_creds: list[str] = []
    for field, label in _FIELD_NAMES.items():
        info = creds.get(field, {})
        if not info.get("set") or info.get("placeholder"):
            icon = "[✗]"
            suffix = "  (placeholder)" if info.get("placeholder") else "  (not set)"
            missing_creds.append(label)
        else:
            icon = "[✓]"
            suffix = "  (auto-generated)" if info.get("auto_generated") else ""
        click.echo(f"  {icon} {label}{suffix}")

    authorized = data.get("todoist_authorized", False)
    projects_count = data.get("projects_count", 0)
    llm_model = data.get("llm_model", "")

    click.echo(f"\nTodoist app authorized: {'yes' if authorized else 'no'}")
    click.echo(f"Sorting projects: {projects_count}")
    click.echo(f"LLM model: {llm_model}")

    # Heuristic next step
    if missing_creds:
        first_missing = missing_creds[0]
        click.echo(f"\nNext step: set {first_missing} in .env, restart, then visit /setup.")
    elif not authorized:
        click.echo(f"\nNext step: visit {url}/setup and click Authorize.")
    elif projects_count == 0:
        click.echo("\nNext step: create a sorting project with `todolist-sorter projects create`.")
    else:
        click.echo("\nNext step: all set.")


# ---------------------------------------------------------------------------
# init command (no auth required, no api-key needed at all)
# ---------------------------------------------------------------------------


@cli.command("init")
@click.option("--output", default="./.env", show_default=True,
              help="Path to write the .env file.")
@click.option("--force", is_flag=True, help="Overwrite if file exists.")
def init_cmd(output, force):
    """Interactively generate a .env file."""
    output_path = Path(output)
    if output_path.exists() and not force:
        click.confirm(
            f"{output_path} already exists. Overwrite?",
            abort=True,
        )

    click.echo("Generating .env — press Enter to accept defaults.\n")

    client_id = click.prompt("Todoist Client ID (from developer console)")
    client_secret = click.prompt("Todoist Client Secret (from developer console)")
    api_token = click.prompt("Todoist personal API token")
    llm_model = click.prompt("LLM model", default="anthropic:claude-sonnet-4-6")
    llm_api_key = click.prompt("LLM API key")
    database_url = click.prompt("Database URL", default="sqlite:///./data/app.db")
    debounce_seconds = click.prompt("Debounce seconds", default="5")
    suppression_seconds = click.prompt("Suppression window seconds", default="30")

    app_api_key = secrets.token_urlsafe(32)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = (
        f"# Generated by todolist-sorter init on {now}\n"
        f"TODOIST_CLIENT_ID={client_id}\n"
        f"TODOIST_CLIENT_SECRET={client_secret}\n"
        f"TODOIST_API_TOKEN={api_token}\n"
        f"LLM_MODEL={llm_model}\n"
        f"LLM_API_KEY={llm_api_key}\n"
        f"APP_API_KEY={app_api_key}\n"
        f"DATABASE_URL={database_url}\n"
        f"DEFAULT_DEBOUNCE_SECONDS={debounce_seconds}\n"
        f"SUPPRESSION_WINDOW_SECONDS={suppression_seconds}\n"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    click.echo(f"\nWrote {output_path}")
    click.echo(f"APP_API_KEY was auto-generated: {app_api_key}")


def main():
    cli(obj={})


if __name__ == "__main__":
    main()
