"""Easy mode: create or reconcile one sorting project from env vars +
text files mounted into the container.

Activated when `AUTO_PROJECT_EXTERNAL_ID` is set. See README for usage.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlmodel import Session, select

from app.config import Settings
from app.models import SortingProject
from app.projects_ops import (
    reconcile_additional_instructions,
    reconcile_categories,
)

log = logging.getLogger(__name__)


def load_categories_file(path: str) -> list[str] | None:
    """Read a category-per-line file. Returns the list of categories.

    Empty lines and `#`-prefixed comment lines are skipped.
    Returns `None` if `path` is empty or the file does not exist.
    """
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    categories: list[str] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        categories.append(line)
    return categories


def load_instructions_file(path: str) -> str | None:
    """Read and trim the instructions file. Returns `None` if `path` is
    empty, the file does not exist, or its trimmed content is empty."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8").strip()
    return text or None


def sync_auto_project(
    session: Session, settings: Settings
) -> UUID | None:
    """Apply the env-var-driven auto-project spec to the database.

    Returns the project's UUID if a sort should be triggered (something
    changed, or a new project was created). Returns None if the feature
    is disabled or nothing needs doing.
    """
    external_id = settings.auto_project_external_id.strip()
    if not external_id:
        return None

    provider = (settings.auto_project_provider or "todoist").strip() or "todoist"
    name = (settings.auto_project_name or "Auto").strip() or "Auto"

    categories = load_categories_file(settings.auto_categories_file)
    instructions = load_instructions_file(settings.auto_instructions_file)

    project = session.exec(
        select(SortingProject).where(
            SortingProject.provider == provider,
            SortingProject.external_project_id == external_id,
        )
    ).first()

    # Branch 1: project does not yet exist
    if project is None:
        if not categories:
            log.warning(
                "auto-project: %s/%s not found and no usable "
                "AUTO_CATEGORIES_FILE (%r); skipping creation",
                provider, external_id, settings.auto_categories_file,
            )
            return None

        project = SortingProject(
            name=name,
            provider=provider,
            external_project_id=external_id,
            categories=categories,
            additional_instructions=instructions,
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        log.info(
            "auto-project: created %r (%s) with %d categories, "
            "additional_instructions=%s",
            name, project.id, len(categories), bool(instructions),
        )
        return project.id

    # Branch 2: project exists — reconcile fields
    changed = False

    if categories is not None:
        if reconcile_categories(session, project, categories):
            log.info(
                "auto-project: %r categories updated (%d items)",
                project.name, len(categories),
            )
            changed = True
    else:
        log.info(
            "auto-project: %r has no AUTO_CATEGORIES_FILE, keeping "
            "existing %d categories",
            project.name, len(project.categories),
        )

    # For instructions we always sync: a missing/empty file means "clear it"
    if reconcile_additional_instructions(session, project, instructions):
        log.info(
            "auto-project: %r additional_instructions updated (now %s)",
            project.name, "set" if instructions else "cleared",
        )
        changed = True

    if not changed:
        log.info("auto-project: %r already up to date", project.name)
        return None

    project.updated_at = datetime.now(timezone.utc)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project.id
