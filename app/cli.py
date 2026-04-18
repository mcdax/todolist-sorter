import json
from pathlib import Path

import click
import httpx


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
    required=True,
    help="API key for authentication.",
)
@click.pass_context
def cli(ctx, url, api_key):
    """Management CLI for todolist-sorter."""
    ctx.ensure_object(dict)
    ctx.obj["client"] = httpx.Client(
        base_url=url,
        headers={"X-API-Key": api_key},
        timeout=30.0,
    )


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
    r = _handle(ctx.obj["client"].get("/projects"))
    items = r.json()
    if not items:
        click.echo("No projects found.")
        return
    for p in items:
        enabled_flag = "" if p.get("enabled", True) else " [disabled]"
        click.echo(f"  {p['id']}  {p['name']}{enabled_flag}  ({p.get('provider', '')})")


@projects.command("create")
@click.option("--name", required=True, help="Project name.")
@click.option("--external-id", required=True, help="External project ID.")
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

    r = _handle(ctx.obj["client"].post("/projects", json=payload))
    _print_json(r.json())


@projects.command("show")
@click.argument("project_id")
@click.pass_context
def show_project(ctx, project_id):
    """Show details for a project."""
    r = _handle(ctx.obj["client"].get(f"/projects/{project_id}"))
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

    r = _handle(ctx.obj["client"].put(f"/projects/{project_id}", json=payload))
    _print_json(r.json())


@projects.command("delete")
@click.argument("project_id")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def delete_project(ctx, project_id, yes):
    """Delete a project."""
    if not yes:
        click.confirm(f"Delete project {project_id}?", abort=True)
    _handle(ctx.obj["client"].delete(f"/projects/{project_id}"))
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
    r = _handle(ctx.obj["client"].get(f"/projects/{project_id}/categories"))
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
    r = _handle(ctx.obj["client"].post(f"/projects/{project_id}/categories", json=payload))
    _print_list(r.json())


@categories.command("remove")
@click.argument("project_id")
@click.argument("index", type=int)
@click.pass_context
def remove_category(ctx, project_id, index):
    """Remove a category by index."""
    r = _handle(ctx.obj["client"].delete(f"/projects/{project_id}/categories/{index}"))
    _print_list(r.json())


@categories.command("rename")
@click.argument("project_id")
@click.argument("index", type=int)
@click.argument("new_name")
@click.pass_context
def rename_category(ctx, project_id, index, new_name):
    """Rename a category by index."""
    r = _handle(ctx.obj["client"].patch(
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
    r = _handle(ctx.obj["client"].patch(
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
    r = _handle(ctx.obj["client"].put(
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
    r = _handle(ctx.obj["client"].get(f"/projects/{project_id}/cache"))
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
    _handle(ctx.obj["client"].delete(f"/projects/{project_id}/cache"))
    click.echo(f"Cache cleared for {project_id}")


# ---------------------------------------------------------------------------
# sort command
# ---------------------------------------------------------------------------


@cli.command("sort")
@click.argument("project_id")
@click.pass_context
def sort_project(ctx, project_id):
    """Trigger sorting for a project."""
    r = _handle(ctx.obj["client"].post(f"/projects/{project_id}/sort"))
    click.echo(r.json().get("status", "queued"))


def main():
    cli(obj={})


if __name__ == "__main__":
    main()
