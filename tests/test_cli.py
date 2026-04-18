import json

import httpx
import pytest
import respx
from click.testing import CliRunner

from app.cli import cli

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PROJECT = {
    "id": "abc",
    "name": "Lidl",
    "provider": "todoist",
    "external_project_id": "999",
    "enabled": True,
    "categories": [],
    "description": None,
    "debounce_seconds": 5,
}

_ENV = {"TODOLIST_SORTER_API_KEY": "k"}


# ---------------------------------------------------------------------------
# projects list
# ---------------------------------------------------------------------------


def test_projects_list():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        mock.get("/projects").mock(
            return_value=httpx.Response(200, json=[_PROJECT])
        )
        result = runner.invoke(cli, ["projects", "list"], env=_ENV)
    assert result.exit_code == 0
    assert "Lidl" in result.output
    assert "abc" in result.output


def test_projects_list_empty():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        mock.get("/projects").mock(return_value=httpx.Response(200, json=[]))
        result = runner.invoke(cli, ["projects", "list"], env=_ENV)
    assert result.exit_code == 0
    assert "No projects found" in result.output


# ---------------------------------------------------------------------------
# projects create
# ---------------------------------------------------------------------------


def test_projects_create_basic():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        route = mock.post("/projects").mock(
            return_value=httpx.Response(201, json=_PROJECT)
        )
        result = runner.invoke(cli, [
            "projects", "create",
            "--name", "Lidl",
            "--external-id", "999",
        ], env=_ENV)
    assert result.exit_code == 0
    payload = json.loads(route.calls.last.request.content)
    assert payload["name"] == "Lidl"
    assert payload["external_project_id"] == "999"
    assert payload["categories"] == []


def test_projects_create_with_categories_file(tmp_path):
    cats_file = tmp_path / "cats.txt"
    cats_file.write_text("🥬 Vegetables\n🍎 Fruit\n\n🥛 Dairy\n", encoding="utf-8")
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        route = mock.post("/projects").mock(
            return_value=httpx.Response(201, json={
                **_PROJECT,
                "categories": ["🥬 Vegetables", "🍎 Fruit", "🥛 Dairy"],
            })
        )
        result = runner.invoke(cli, [
            "projects", "create",
            "--name", "Lidl",
            "--external-id", "999",
            "--categories-file", str(cats_file),
        ], env=_ENV)
    assert result.exit_code == 0
    payload = json.loads(route.calls.last.request.content)
    assert payload["categories"] == ["🥬 Vegetables", "🍎 Fruit", "🥛 Dairy"]


# ---------------------------------------------------------------------------
# projects show / update / delete
# ---------------------------------------------------------------------------


def test_projects_show():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        mock.get("/projects/abc").mock(return_value=httpx.Response(200, json=_PROJECT))
        result = runner.invoke(cli, ["projects", "show", "abc"], env=_ENV)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["id"] == "abc"
    assert data["name"] == "Lidl"


def test_projects_update_sends_only_provided_fields():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        route = mock.put("/projects/abc").mock(
            return_value=httpx.Response(200, json={**_PROJECT, "name": "Aldi"})
        )
        result = runner.invoke(cli, [
            "projects", "update", "abc", "--name", "Aldi",
        ], env=_ENV)
    assert result.exit_code == 0
    payload = json.loads(route.calls.last.request.content)
    assert payload == {"name": "Aldi"}


def test_projects_update_nothing_to_update():
    runner = CliRunner()
    result = runner.invoke(cli, ["projects", "update", "abc"], env=_ENV)
    assert result.exit_code == 1


def test_projects_update_enabled_disabled():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        route = mock.put("/projects/abc").mock(
            return_value=httpx.Response(200, json={**_PROJECT, "enabled": False})
        )
        result = runner.invoke(cli, [
            "projects", "update", "abc", "--disabled",
        ], env=_ENV)
    assert result.exit_code == 0
    payload = json.loads(route.calls.last.request.content)
    assert payload == {"enabled": False}


def test_delete_with_yes_skips_confirm():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        mock.delete("/projects/abc").mock(return_value=httpx.Response(204))
        result = runner.invoke(cli, ["projects", "delete", "abc", "--yes"], env=_ENV)
    assert result.exit_code == 0
    assert "abc" in result.output


def test_delete_without_yes_prompts(monkeypatch):
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        mock.delete("/projects/abc").mock(return_value=httpx.Response(204))
        # simulate confirming 'y'
        result = runner.invoke(
            cli, ["projects", "delete", "abc"],
            input="y\n",
            env=_ENV,
        )
    assert result.exit_code == 0


def test_delete_without_yes_abort():
    runner = CliRunner()
    # No HTTP call expected — user aborts at the prompt, no mock needed
    result = runner.invoke(
        cli, ["projects", "delete", "abc"],
        input="n\n",
        env=_ENV,
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# categories
# ---------------------------------------------------------------------------


def test_categories_list():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        mock.get("/projects/abc/categories").mock(
            return_value=httpx.Response(200, json=["Veggies", "Dairy"])
        )
        result = runner.invoke(cli, ["categories", "list", "abc"], env=_ENV)
    assert result.exit_code == 0
    assert "0. Veggies" in result.output
    assert "1. Dairy" in result.output


def test_categories_add_without_index():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        route = mock.post("/projects/abc/categories").mock(
            return_value=httpx.Response(200, json=["A", "B"])
        )
        result = runner.invoke(cli, ["categories", "add", "abc", "B"], env=_ENV)
    assert result.exit_code == 0
    payload = json.loads(route.calls.last.request.content)
    assert payload == {"name": "B"}


def test_categories_add_with_index():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        route = mock.post("/projects/abc/categories").mock(
            return_value=httpx.Response(200, json=["A", "X", "B"])
        )
        result = runner.invoke(cli, [
            "categories", "add", "abc", "X", "--at-index", "1",
        ], env=_ENV)
    assert result.exit_code == 0
    payload = json.loads(route.calls.last.request.content)
    assert payload == {"name": "X", "at_index": 1}


def test_categories_remove():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        route = mock.delete("/projects/abc/categories/0").mock(
            return_value=httpx.Response(200, json=["B"])
        )
        result = runner.invoke(cli, ["categories", "remove", "abc", "0"], env=_ENV)
    assert result.exit_code == 0
    assert "0. B" in result.output


def test_categories_rename():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        route = mock.patch("/projects/abc/categories/1").mock(
            return_value=httpx.Response(200, json=["A", "Renamed"])
        )
        result = runner.invoke(cli, [
            "categories", "rename", "abc", "1", "Renamed",
        ], env=_ENV)
    assert result.exit_code == 0
    payload = json.loads(route.calls.last.request.content)
    assert payload == {"name": "Renamed"}
    assert "1. Renamed" in result.output


def test_categories_move():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        route = mock.patch("/projects/abc/categories/2").mock(
            return_value=httpx.Response(200, json=["A", "C", "B"])
        )
        result = runner.invoke(cli, [
            "categories", "move", "abc", "2", "--to", "1",
        ], env=_ENV)
    assert result.exit_code == 0
    payload = json.loads(route.calls.last.request.content)
    assert payload == {"move_to": 1}


def test_categories_replace_from_file(tmp_path):
    cats_file = tmp_path / "cats.txt"
    cats_file.write_text("A\nB\nC\n", encoding="utf-8")
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        route = mock.put("/projects/abc/categories").mock(
            return_value=httpx.Response(200, json=["A", "B", "C"])
        )
        result = runner.invoke(cli, [
            "categories", "replace", "abc", str(cats_file),
        ], env=_ENV)
    assert result.exit_code == 0
    payload = json.loads(route.calls.last.request.content)
    assert payload == {"categories": ["A", "B", "C"]}


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------


def test_cache_show_empty():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        mock.get("/projects/abc/cache").mock(return_value=httpx.Response(200, json=[]))
        result = runner.invoke(cli, ["cache", "show", "abc"], env=_ENV)
    assert result.exit_code == 0
    assert "empty" in result.output


def test_cache_show_entries():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        mock.get("/projects/abc/cache").mock(return_value=httpx.Response(200, json=[
            {"content_key": "hash1", "category_name": "Dairy"},
        ]))
        result = runner.invoke(cli, ["cache", "show", "abc"], env=_ENV)
    assert result.exit_code == 0
    assert "hash1" in result.output
    assert "Dairy" in result.output


def test_cache_clear_with_yes():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        mock.delete("/projects/abc/cache").mock(return_value=httpx.Response(204))
        result = runner.invoke(cli, ["cache", "clear", "abc", "--yes"], env=_ENV)
    assert result.exit_code == 0
    assert "cleared" in result.output


# ---------------------------------------------------------------------------
# sort
# ---------------------------------------------------------------------------


def test_sort():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        mock.post("/projects/abc/sort").mock(
            return_value=httpx.Response(202, json={"status": "queued"})
        )
        result = runner.invoke(cli, ["sort", "abc"], env=_ENV)
    assert result.exit_code == 0
    assert "queued" in result.output


# ---------------------------------------------------------------------------
# HTTP error handling
# ---------------------------------------------------------------------------


def test_http_error_exits_nonzero():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        mock.get("/projects").mock(
            return_value=httpx.Response(401, json={"detail": "nope"})
        )
        result = runner.invoke(cli, ["projects", "list"],
                               env={"TODOLIST_SORTER_API_KEY": "bad"})
    assert result.exit_code == 1


def test_http_500_error():
    runner = CliRunner()
    with respx.mock(base_url="http://localhost:8000") as mock:
        mock.get("/projects/abc").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )
        result = runner.invoke(cli, ["projects", "show", "abc"], env=_ENV)
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_missing_api_key_fails():
    runner = CliRunner()
    result = runner.invoke(cli, ["projects", "list"], env={})
    assert result.exit_code != 0


def test_custom_url():
    runner = CliRunner()
    with respx.mock(base_url="http://myserver:9000") as mock:
        mock.get("/projects").mock(return_value=httpx.Response(200, json=[]))
        result = runner.invoke(cli, [
            "--url", "http://myserver:9000",
            "projects", "list",
        ], env=_ENV)
    assert result.exit_code == 0
