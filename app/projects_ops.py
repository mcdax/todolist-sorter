"""Database operations on `SortingProject` + `CategoryCache` that are
shared between the REST API routes and other callers (e.g. the
auto-project sync on startup)."""

from __future__ import annotations

from uuid import UUID

from sqlmodel import Session, select

from app.models import CategoryCache, SortingProject


def clear_project_cache(session: Session, project_id: UUID) -> None:
    """Delete every `CategoryCache` row for the given project."""
    for row in session.exec(
        select(CategoryCache).where(CategoryCache.project_id == project_id)
    ).all():
        session.delete(row)


def clear_cache_for_category(
    session: Session, project_id: UUID, name: str
) -> None:
    """Delete cache rows whose `category_name` equals `name`."""
    for row in session.exec(
        select(CategoryCache).where(
            CategoryCache.project_id == project_id,
            CategoryCache.category_name == name,
        )
    ).all():
        session.delete(row)


def reconcile_categories(
    session: Session,
    project: SortingProject,
    new_categories: list[str],
) -> bool:
    """Apply `new_categories` to `project` with the same cache-invalidation
    rules the REST API uses:

    - any added name (new - old) → full project cache clear
    - else, for each removed name → delete rows for that name
    - pure reorder (same set, different order) → cache untouched

    Returns True if the ordered list actually changed; False if the input
    equals the current value.
    """
    if list(new_categories) == list(project.categories):
        return False

    old = set(project.categories)
    new = set(new_categories)
    if new - old:
        clear_project_cache(session, project.id)
    else:
        for removed in old - new:
            clear_cache_for_category(session, project.id, removed)
    project.categories = list(new_categories)
    return True


def reconcile_additional_instructions(
    session: Session,
    project: SortingProject,
    new_value: str | None,
) -> bool:
    """Set `project.additional_instructions` to `new_value`. Empty strings
    are normalised to `None`. If the effective value changed (including
    `None ↔ str`) the full project cache is cleared. Returns True on any
    change, False otherwise."""
    normalised = new_value or None
    if normalised == project.additional_instructions:
        return False
    project.additional_instructions = normalised
    clear_project_cache(session, project.id)
    return True
