import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models import Model
from sqlmodel import Session, select

from app.backends.base import Task
from app.models import CategoryCache, SortingProject
from app.normalize import content_key as _content_key

log = logging.getLogger(__name__)


class Assignment(BaseModel):
    item_id: str
    category_name: str
    transformed_content: str | None = None


class CategorizedItems(BaseModel):
    assignments: list[Assignment]


SYSTEM_PROMPT = (
    "You categorize shopping list items into the given categories. "
    "Respond strictly in the required JSON schema. Pick exactly one "
    "category from the list for each item to be categorized. Do not "
    "invent categories and do not change the reference assignments."
)


def render_prompt(
    *,
    categories: list[str],
    description: str | None,
    hits: dict[str, str],
    misses: list[Task],
    additional_instructions: str | None = None,
) -> str:
    lines: list[str] = []
    lines.append("Categories (in this order):")
    for i, name in enumerate(categories, 1):
        lines.append(f"  {i}. {name}")
    lines.append("")
    if description:
        lines.append(description)
        lines.append("")
    if hits:
        lines.append("Already assigned (for reference only, do not change):")
        for content, cat in hits.items():
            lines.append(f"  - {content} → {cat}")
        lines.append("")
    lines.append("Please categorize:")
    for task in misses:
        lines.append(f'  - id={task.id}, content="{task.content}"')
    if additional_instructions:
        lines.append("")
        lines.append(
            "Additionally, apply the following transformations to each item's content and"
        )
        lines.append(
            "return the new content in the `transformed_content` field. Leave unchanged"
        )
        lines.append("if no transformation applies:")
        lines.append(additional_instructions)
    return "\n".join(lines)


async def categorize(
    *,
    model: Model | str,
    categories: list[str],
    description: str | None,
    hits: dict[str, str],
    misses: list[Task],
    additional_instructions: str | None = None,
) -> CategorizedItems:
    agent = Agent(
        model,
        output_type=CategorizedItems,
        system_prompt=SYSTEM_PROMPT,
    )
    prompt = render_prompt(
        categories=categories,
        description=description,
        hits=hits,
        misses=misses,
        additional_instructions=additional_instructions,
    )
    result = await agent.run(prompt)
    return result.output


def validate_assignments(
    result: CategorizedItems,
    *,
    categories: list[str],
    requested_ids: set[str],
) -> list[Assignment]:
    cat_set = set(categories)
    seen: set[str] = set()
    valid: list[Assignment] = []
    for a in result.assignments:
        if a.item_id not in requested_ids:
            continue
        if a.item_id in seen:
            continue
        if a.category_name not in cat_set:
            continue
        seen.add(a.item_id)
        valid.append(a)
    return valid


def compute_reorder(
    tasks: list[Task],
    categories: list[str],
    assignments: dict[str, str],
) -> list[str]:
    cat_index = {name: i for i, name in enumerate(categories)}
    orphan_index = len(categories)
    positioned = []
    for pos, t in enumerate(tasks):
        cat_name = assignments.get(t.id)
        idx = cat_index.get(cat_name, orphan_index) if cat_name else orphan_index
        positioned.append((idx, pos, t.id))
    positioned.sort(key=lambda x: (x[0], x[1]))
    return [tid for _, _, tid in positioned]


CategorizeFn = Callable[..., Awaitable[CategorizedItems]]
ReorderCallback = Callable[[UUID, list[str]], None]


async def sort_project(
    *,
    project_id: UUID,
    session: Session,
    backend: Any,
    llm_model: Any,
    categorize_fn: CategorizeFn = categorize,
    on_reorder: ReorderCallback = lambda _pid, _ids: None,
) -> None:
    project = session.get(SortingProject, project_id)
    if not project or not project.enabled:
        log.info("sort aborted: project %s not found or disabled", project_id)
        return

    tasks = await backend.get_tasks(project)
    if len(tasks) < 2:
        log.info(
            "sort aborted: project %r has %d task(s), need at least 2",
            project.name,
            len(tasks),
        )
        return

    log.info("sort_project start: project=%r total_tasks=%d", project.name, len(tasks))

    additional_instructions: str | None = project.additional_instructions or None

    keys = {t.id: _content_key(t.content) for t in tasks}
    cache_rows = session.exec(
        select(CategoryCache).where(CategoryCache.project_id == project_id)
    ).all()
    # Map content_key -> (category_name, transformed_content)
    cached: dict[str, tuple[str, str | None]] = {
        c.content_key: (c.category_name, c.transformed_content) for c in cache_rows
    }

    assignments: dict[str, str] = {}
    # Map task_id -> transformed_content from cache hits
    hit_transformed: dict[str, str | None] = {}
    hit_contents: dict[str, str] = {}
    misses: list[Task] = []
    for t in tasks:
        k = keys[t.id]
        if k in cached:
            cat_name, trans = cached[k]
            assignments[t.id] = cat_name
            hit_contents[t.content] = cat_name
            hit_transformed[t.id] = trans
            log.info("cache hit: %s → %s", t.content, cat_name)
        else:
            misses.append(t)

    # Map task_id -> transformed_content from LLM assignments
    llm_transformed: dict[str, str | None] = {}

    if misses:
        log.info(
            "%d item(s) need LLM: %s",
            len(misses),
            [m.content for m in misses],
        )
        try:
            result = await categorize_fn(
                model=llm_model,
                categories=project.categories,
                description=project.description,
                hits=hit_contents,
                misses=misses,
                additional_instructions=additional_instructions,
            )
        except Exception:
            log.exception("LLM categorization failed for project %s", project_id)
            return
        valid = validate_assignments(
            result,
            categories=project.categories,
            requested_ids={m.id for m in misses},
        )
        assigned_ids = {a.item_id for a in valid}
        for a in valid:
            assignments[a.item_id] = a.category_name
            miss_content = next(m.content for m in misses if m.id == a.item_id)
            # Treat empty string as no transformation
            trans = a.transformed_content if a.transformed_content else None
            llm_transformed[a.item_id] = trans
            log.info("LLM categorized: %s → %s", miss_content, a.category_name)
            _upsert_cache(session, project_id,
                          _content_key(miss_content), a.category_name,
                          transformed_content=trans)
        for m in misses:
            if m.id not in assigned_ids:
                log.warning("orphan: %s", m.content)
        session.commit()

    current = await backend.get_tasks(project)
    current_ids = {t.id for t in current}
    ordered = [
        tid for tid in compute_reorder(current, project.categories, assignments)
        if tid in current_ids
    ]
    if len(ordered) < 2:
        return

    # Compute content-update plan (only when additional_instructions is set)
    id_to_content = {t.id: t.content for t in current}
    content_updates: list[tuple[str, str, str]] = []  # (task_id, old, new)
    if additional_instructions:
        for t in current:
            if t.id not in current_ids:
                continue
            # Prefer LLM-derived transformation; fall back to cached hit
            trans = llm_transformed.get(t.id, hit_transformed.get(t.id))
            if trans and trans != t.content:
                content_updates.append((t.id, t.content, trans))

    update_ids = {tid for tid, _, _ in content_updates}
    affected_ids = list(set(ordered) | update_ids)

    # Mark suppression BEFORE firing writes so webhook echoes get dropped
    on_reorder(project_id, affected_ids)

    # Issue content updates
    for task_id, old_content, new_content in content_updates:
        log.info("will update content: %s %r → %r", task_id, old_content, new_content)
        try:
            await backend.update_task_content(project, task_id, new_content)
        except Exception:
            log.warning(
                "failed to update content for task %s, continuing", task_id,
                exc_info=True,
            )
    if content_updates:
        log.info("updated %d item contents", len(content_updates))

    ordered_contents = [id_to_content[tid] for tid in ordered]
    log.info("reordered %d items: %s", len(ordered), ordered_contents)
    await backend.reorder(project, ordered)


def _upsert_cache(
    session: Session,
    project_id: UUID,
    ckey: str,
    category_name: str,
    transformed_content: str | None = None,
) -> None:
    existing = session.get(CategoryCache, (project_id, ckey))
    if existing:
        changed = False
        if existing.category_name != category_name:
            existing.category_name = category_name
            changed = True
        if existing.transformed_content != transformed_content:
            existing.transformed_content = transformed_content
            changed = True
        if changed:
            existing.updated_at = datetime.now(timezone.utc)
        return
    session.add(CategoryCache(
        project_id=project_id,
        content_key=ckey,
        category_name=category_name,
        transformed_content=transformed_content,
    ))
