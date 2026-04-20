"""Tests for easy-mode auto-project sync (app.auto)."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from sqlmodel import Session, select

from app.auto import (
    load_categories_file,
    load_instructions_file,
    sync_auto_project,
)
from app.config import Settings
from app.models import CategoryCache, SortingProject


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        todoist_client_id="x",
        todoist_client_secret="x",
        todoist_api_token="x",
        llm_model="anthropic:claude-sonnet-4-6",
        llm_api_key="x",
        app_api_key="x",
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        default_debounce_seconds=5,
        suppression_window_seconds=30,
        auto_project_external_id="",
        auto_project_provider="todoist",
        auto_project_name="Auto",
        auto_categories_file="",
        auto_instructions_file="",
    )
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# File loaders
# ---------------------------------------------------------------------------

def test_load_categories_file_strips_comments_and_blanks(tmp_path: Path):
    p = tmp_path / "cats.txt"
    p.write_text(
        "# comment\n🥬 Gemüse\n\n  # indented comment\n  🍎 Obst  \n\n",
        encoding="utf-8",
    )
    # Lines are stripped before the `#` check, so `  # foo` counts as
    # a comment. Leading/trailing whitespace on real entries is trimmed.
    assert load_categories_file(str(p)) == ["🥬 Gemüse", "🍎 Obst"]


def test_load_categories_file_empty_path_returns_none():
    assert load_categories_file("") is None


def test_load_categories_file_missing_file_returns_none(tmp_path: Path):
    assert load_categories_file(str(tmp_path / "nope.txt")) is None


def test_load_instructions_file_strips_and_empties_to_none(tmp_path: Path):
    p = tmp_path / "inst.txt"
    p.write_text("   \n  \n", encoding="utf-8")
    assert load_instructions_file(str(p)) is None
    p.write_text("Fix typos.\n", encoding="utf-8")
    assert load_instructions_file(str(p)) == "Fix typos."


# ---------------------------------------------------------------------------
# sync_auto_project
# ---------------------------------------------------------------------------

def test_sync_disabled_when_no_external_id(session: Session, tmp_path: Path):
    s = _settings(tmp_path, auto_project_external_id="")
    assert sync_auto_project(session, s) is None
    assert session.exec(select(SortingProject)).all() == []


def test_sync_creates_project_when_missing(session: Session, tmp_path: Path):
    cats = tmp_path / "cats.txt"
    cats.write_text("🥬 Gemüse\n🍎 Obst\n", encoding="utf-8")
    inst = tmp_path / "inst.txt"
    inst.write_text("fix typos", encoding="utf-8")

    s = _settings(
        tmp_path,
        auto_project_external_id="6GpgProj",
        auto_project_name="Lidl",
        auto_categories_file=str(cats),
        auto_instructions_file=str(inst),
    )
    pid = sync_auto_project(session, s)

    assert isinstance(pid, UUID)
    project = session.get(SortingProject, pid)
    assert project is not None
    assert project.name == "Lidl"
    assert project.provider == "todoist"
    assert project.external_project_id == "6GpgProj"
    assert project.categories == ["🥬 Gemüse", "🍎 Obst"]
    assert project.additional_instructions == "fix typos"


def test_sync_skips_create_when_no_categories_file(
    session: Session, tmp_path: Path, caplog,
):
    s = _settings(
        tmp_path,
        auto_project_external_id="6GpgProj",
        auto_categories_file="",
    )
    import logging as _logging
    caplog.set_level(_logging.WARNING, logger="app.auto")
    assert sync_auto_project(session, s) is None
    assert session.exec(select(SortingProject)).all() == []
    assert "skipping creation" in caplog.text


def test_sync_noop_when_file_matches_existing(
    session: Session, tmp_path: Path,
):
    cats = tmp_path / "cats.txt"
    cats.write_text("A\nB\n", encoding="utf-8")

    # Seed existing project matching file
    from uuid import uuid4
    pid = uuid4()
    project = SortingProject(
        id=pid, name="X", provider="todoist",
        external_project_id="ext1",
        categories=["A", "B"],
        additional_instructions=None,
    )
    session.add(project)
    session.commit()

    s = _settings(
        tmp_path,
        auto_project_external_id="ext1",
        auto_categories_file=str(cats),
    )
    assert sync_auto_project(session, s) is None  # nothing changed


def test_sync_adds_category_clears_full_cache(
    session: Session, tmp_path: Path,
):
    from uuid import uuid4
    pid = uuid4()
    session.add(SortingProject(
        id=pid, name="X", provider="todoist",
        external_project_id="ext1",
        categories=["A", "B"],
    ))
    session.add(CategoryCache(
        project_id=pid, content_key="apple", category_name="A",
    ))
    session.commit()

    cats = tmp_path / "cats.txt"
    cats.write_text("A\nB\nC\n", encoding="utf-8")
    s = _settings(
        tmp_path,
        auto_project_external_id="ext1",
        auto_categories_file=str(cats),
    )

    result = sync_auto_project(session, s)
    assert result == pid

    project = session.get(SortingProject, pid)
    assert project.categories == ["A", "B", "C"]
    assert session.exec(
        select(CategoryCache).where(CategoryCache.project_id == pid)
    ).all() == []


def test_sync_removes_category_partial_invalidation(
    session: Session, tmp_path: Path,
):
    from uuid import uuid4
    pid = uuid4()
    session.add(SortingProject(
        id=pid, name="X", provider="todoist",
        external_project_id="ext1",
        categories=["A", "B"],
    ))
    session.add(CategoryCache(
        project_id=pid, content_key="apple", category_name="A",
    ))
    session.add(CategoryCache(
        project_id=pid, content_key="bat", category_name="B",
    ))
    session.commit()

    cats = tmp_path / "cats.txt"
    cats.write_text("A\n", encoding="utf-8")
    s = _settings(
        tmp_path,
        auto_project_external_id="ext1",
        auto_categories_file=str(cats),
    )

    result = sync_auto_project(session, s)
    assert result == pid

    remaining = session.exec(
        select(CategoryCache).where(CategoryCache.project_id == pid)
    ).all()
    assert [r.category_name for r in remaining] == ["A"]


def test_sync_instructions_change_clears_cache(
    session: Session, tmp_path: Path,
):
    from uuid import uuid4
    pid = uuid4()
    session.add(SortingProject(
        id=pid, name="X", provider="todoist",
        external_project_id="ext1",
        categories=["A"],
        additional_instructions="old",
    ))
    session.add(CategoryCache(
        project_id=pid, content_key="apple", category_name="A",
    ))
    session.commit()

    cats = tmp_path / "cats.txt"
    cats.write_text("A\n", encoding="utf-8")
    inst = tmp_path / "inst.txt"
    inst.write_text("new value", encoding="utf-8")

    s = _settings(
        tmp_path,
        auto_project_external_id="ext1",
        auto_categories_file=str(cats),
        auto_instructions_file=str(inst),
    )
    result = sync_auto_project(session, s)
    assert result == pid
    project = session.get(SortingProject, pid)
    assert project.additional_instructions == "new value"
    assert session.exec(
        select(CategoryCache).where(CategoryCache.project_id == pid)
    ).all() == []


def test_sync_instructions_file_missing_clears_previous_value(
    session: Session, tmp_path: Path,
):
    from uuid import uuid4
    pid = uuid4()
    session.add(SortingProject(
        id=pid, name="X", provider="todoist",
        external_project_id="ext1",
        categories=["A"],
        additional_instructions="was set",
    ))
    session.add(CategoryCache(
        project_id=pid, content_key="apple", category_name="A",
    ))
    session.commit()

    cats = tmp_path / "cats.txt"
    cats.write_text("A\n", encoding="utf-8")
    s = _settings(
        tmp_path,
        auto_project_external_id="ext1",
        auto_categories_file=str(cats),
        auto_instructions_file="",  # no file → clear
    )
    result = sync_auto_project(session, s)
    assert result == pid
    project = session.get(SortingProject, pid)
    assert project.additional_instructions is None


def test_sync_missing_categories_file_keeps_existing(
    session: Session, tmp_path: Path,
):
    from uuid import uuid4
    pid = uuid4()
    session.add(SortingProject(
        id=pid, name="X", provider="todoist",
        external_project_id="ext1",
        categories=["A", "B"],
        additional_instructions=None,
    ))
    session.commit()

    s = _settings(
        tmp_path,
        auto_project_external_id="ext1",
        auto_categories_file="",  # file not configured
    )
    # Nothing to sync (categories kept, instructions already None)
    assert sync_auto_project(session, s) is None
    project = session.get(SortingProject, pid)
    assert project.categories == ["A", "B"]
