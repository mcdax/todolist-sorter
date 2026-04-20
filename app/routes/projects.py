from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import SortingProject
from app.routes.deps import require_api_key


SortTrigger = Callable[[UUID], None]


class ProjectCreate(BaseModel):
    """Payload to create a new sorting project."""

    name: str = Field(
        description="Human-readable project name shown in the UI and logs.",
        examples=["Lidl Shopping"],
    )
    provider: str = Field(
        description=(
            "Task-backend provider identifier. Only `todoist` is supported "
            "today."
        ),
        examples=["todoist"],
    )
    external_project_id: str = Field(
        description=(
            "Provider-side project id (for Todoist: the numeric id from "
            "the project URL, passed as a string). Unique together with "
            "`provider`."
        ),
        examples=["2345678901"],
    )
    categories: list[str] = Field(
        default_factory=list,
        description=(
            "Ordered list of category names. Items are reordered into this "
            "sequence. Items the LLM cannot match to any category are "
            "sorted to the very end (orphans)."
        ),
        examples=[["🥬 Vegetables", "🍎 Fruit", "🥛 Dairy"]],
    )
    description: str | None = Field(
        default=None,
        description=(
            "Free-text context passed to the LLM as part of the prompt "
            "(e.g. \"route through Lidl: entrance → checkout\")."
        ),
    )
    additional_instructions: str | None = Field(
        default=None,
        description=(
            "Optional prompt extension that lets the LLM also transform "
            "each item's content (e.g. fix typos, prepend an emoji). "
            "When set, the transformed content is written back to the "
            "provider. Setting or changing this via PUT clears the "
            "project's cache and triggers a re-sort."
        ),
        examples=["Fix obvious typos. Prepend a fitting emoji."],
    )
    debounce_seconds: int = Field(
        default=5,
        description=(
            "Per-project override for `DEFAULT_DEBOUNCE_SECONDS`. How long "
            "the debouncer waits after the last webhook event before "
            "running a sort."
        ),
    )


class ProjectUpdate(BaseModel):
    """Partial update payload for a sorting project. Only provided fields
    are modified; omitted fields stay as they were."""

    name: str | None = Field(
        default=None, description="New human-readable project name."
    )
    description: str | None = Field(
        default=None, description="Free-text context passed to the LLM prompt."
    )
    additional_instructions: str | None = Field(
        default=None,
        description=(
            "Optional prompt extension for content transformation. "
            "Changing this value (including null↔value) clears the cache "
            "and triggers a re-sort so every item is re-evaluated under "
            "the new instructions."
        ),
    )
    enabled: bool | None = Field(
        default=None,
        description=(
            "Set to `false` to pause sorting without deleting the project. "
            "Disabled projects ignore incoming webhooks."
        ),
    )
    debounce_seconds: int | None = Field(
        default=None,
        description="Per-project debouncer delay override, in seconds.",
    )


class ProjectOut(BaseModel):
    """A sorting project as returned by the API."""

    id: UUID = Field(description="Server-assigned UUID.")
    name: str
    provider: str
    external_project_id: str
    categories: list[str]
    description: str | None
    additional_instructions: str | None
    enabled: bool
    debounce_seconds: int


def _out(p: SortingProject) -> ProjectOut:
    return ProjectOut(
        id=p.id, name=p.name, provider=p.provider,
        external_project_id=p.external_project_id,
        categories=p.categories, description=p.description,
        additional_instructions=p.additional_instructions,
        enabled=p.enabled, debounce_seconds=p.debounce_seconds,
    )


def build_router(
    *,
    api_key: str,
    session_dep: Callable[[], Iterator[Session]],
    on_sort_requested: SortTrigger = lambda _pid: None,
) -> APIRouter:
    router = APIRouter(prefix="/projects", tags=["projects"],
                       dependencies=[Depends(require_api_key(api_key))])

    def _get_session():
        yield from session_dep()

    @router.post(
        "",
        response_model=ProjectOut,
        status_code=status.HTTP_201_CREATED,
        summary="Create a sorting project",
        description=(
            "Register a new sorting project that links one provider-side "
            "project (e.g. a Todoist project) to an ordered list of "
            "categories. Returns the created project including its UUID. "
            "Responds with 409 if a project with the same "
            "`(provider, external_project_id)` already exists."
        ),
    )
    def create(body: ProjectCreate, s: Session = Depends(_get_session)):
        p = SortingProject(
            name=body.name, provider=body.provider,
            external_project_id=body.external_project_id,
            categories=body.categories, description=body.description,
            additional_instructions=body.additional_instructions,
            debounce_seconds=body.debounce_seconds,
        )
        s.add(p)
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"project with provider={body.provider!r} and "
                f"external_project_id={body.external_project_id!r} already exists",
            )
        s.refresh(p)
        return _out(p)

    @router.get(
        "",
        response_model=list[ProjectOut],
        summary="List all sorting projects",
        description="Returns every registered sorting project, unpaginated.",
    )
    def list_(s: Session = Depends(_get_session)):
        return [_out(p) for p in s.exec(select(SortingProject)).all()]

    @router.get(
        "/{pid}",
        response_model=ProjectOut,
        summary="Get a sorting project by id",
        description="Fetches a single project by its UUID. 404 if not found.",
    )
    def get(pid: UUID, s: Session = Depends(_get_session)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        return _out(p)

    @router.put(
        "/{pid}",
        response_model=ProjectOut,
        summary="Update a sorting project (partial)",
        description=(
            "Partial update — only fields present in the body are changed. "
            "Changing `additional_instructions` (including null↔value) "
            "clears the project's entire cache and triggers a re-sort so "
            "every item is re-evaluated under the new instructions."
        ),
    )
    def update(pid: UUID, body: ProjectUpdate,
               s: Session = Depends(_get_session)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        # Detect additional_instructions change (including None↔non-None)
        ai_in_body = body.model_fields_set
        instructions_changed = (
            "additional_instructions" in ai_in_body
            and body.additional_instructions != p.additional_instructions
        )
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(p, k, v)
        # Handle explicit null for additional_instructions (clear it)
        if "additional_instructions" in ai_in_body and body.additional_instructions is None:
            p.additional_instructions = None
        if instructions_changed:
            _clear_cache(s, pid)
            on_sort_requested(pid)
        p.updated_at = datetime.now(timezone.utc)
        s.add(p); s.commit(); s.refresh(p)
        return _out(p)

    @router.delete(
        "/{pid}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Delete a sorting project",
        description=(
            "Delete a project and all of its `CategoryCache` rows (via "
            "`ON DELETE CASCADE`). Returns 204 on success, 404 if the "
            "project does not exist."
        ),
    )
    def delete(pid: UUID, s: Session = Depends(_get_session)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        s.delete(p); s.commit()

    from pydantic import BaseModel as _BM, Field as _Field

    class CategoryAdd(_BM):
        """Payload to insert one category into a project's list."""

        name: str = _Field(
            description="Category name to insert (free text, typically an "
                        "emoji followed by a label).",
            examples=["🧊 Frozen"],
        )
        at_index: int | None = _Field(
            default=None,
            description="Target insertion index. Omit to append.",
            examples=[5],
        )

    class CategoryPatch(_BM):
        """Payload to rename and/or move one category. Both fields are
        optional; combine them to do both in one call."""

        name: str | None = _Field(
            default=None,
            description=(
                "New category name. Setting this clears the project's "
                "entire cache because a rename can change the LLM-level "
                "semantics of the category."
            ),
        )
        move_to: int | None = _Field(
            default=None,
            description="New index (0-based). Does not touch the cache.",
        )

    class CategoriesReplace(_BM):
        """Payload for atomic full replacement of a project's category list."""

        categories: list[str] = _Field(
            description=(
                "The complete new ordered category list. The service "
                "diffs this against the previous list: additions trigger "
                "a full cache clear, pure removals only clear the rows "
                "for removed names, pure reorders leave the cache intact."
            ),
        )

    def _clear_cache(s: Session, pid: UUID) -> None:
        from app.models import CategoryCache
        for row in s.exec(
            select(CategoryCache).where(CategoryCache.project_id == pid)
        ).all():
            s.delete(row)

    def _clear_for_category(s: Session, pid: UUID, name: str) -> None:
        from app.models import CategoryCache
        for row in s.exec(
            select(CategoryCache).where(
                CategoryCache.project_id == pid,
                CategoryCache.category_name == name,
            )
        ).all():
            s.delete(row)

    @router.get(
        "/{pid}/categories",
        response_model=list[str],
        summary="List a project's categories in order",
    )
    def list_categories(pid: UUID, s: Session = Depends(_get_session)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        return p.categories

    @router.put(
        "/{pid}/categories",
        response_model=list[str],
        summary="Replace a project's category list atomically",
        description=(
            "Replaces the entire category list in one call. Cache "
            "invalidation depends on the diff: any added name → full "
            "clear; pure removals → delete only the rows for removed "
            "names; pure reorders → cache untouched. Returns the new list."
        ),
    )
    def replace_categories(
        pid: UUID, body: CategoriesReplace,
        s: Session = Depends(_get_session),
    ):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        old = set(p.categories)
        new = set(body.categories)
        if new - old:
            _clear_cache(s, pid)
        else:
            for removed in old - new:
                _clear_for_category(s, pid, removed)
        p.categories = list(body.categories)
        p.updated_at = datetime.now(timezone.utc)
        s.add(p); s.commit(); s.refresh(p)
        on_sort_requested(pid)
        return p.categories

    @router.post(
        "/{pid}/categories",
        response_model=list[str],
        summary="Insert one category into a project's list",
        description=(
            "Insert a new category at the given 0-based index (or at the "
            "end if `at_index` is omitted). The full project cache is "
            "cleared because a newly added category may better fit "
            "existing items; the service then triggers a re-sort."
        ),
    )
    def add_category(
        pid: UUID, body: CategoryAdd, s: Session = Depends(_get_session),
    ):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        cats = list(p.categories)
        idx = body.at_index if body.at_index is not None else len(cats)
        if idx < 0 or idx > len(cats):
            raise HTTPException(422, "at_index out of range")
        cats.insert(idx, body.name)
        p.categories = cats
        p.updated_at = datetime.now(timezone.utc)
        _clear_cache(s, pid)
        s.add(p); s.commit(); s.refresh(p)
        on_sort_requested(pid)
        return p.categories

    @router.delete(
        "/{pid}/categories/{index}",
        response_model=list[str],
        summary="Remove a category by index",
        description=(
            "Remove the category at the given 0-based index. Only the "
            "cache rows for the removed name are deleted; the rest of "
            "the cache is preserved. Triggers a re-sort."
        ),
    )
    def remove_category(
        pid: UUID, index: int, s: Session = Depends(_get_session),
    ):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        cats = list(p.categories)
        if index < 0 or index >= len(cats):
            raise HTTPException(422, "index out of range")
        removed = cats.pop(index)
        p.categories = cats
        p.updated_at = datetime.now(timezone.utc)
        _clear_for_category(s, pid, removed)
        s.add(p); s.commit(); s.refresh(p)
        on_sort_requested(pid)
        return p.categories

    @router.patch(
        "/{pid}/categories/{index}",
        response_model=list[str],
        summary="Rename and/or move a category",
        description=(
            "Both `name` and `move_to` are optional. Rename clears the "
            "full project cache (a rename can change semantics); a "
            "pure move does not touch the cache. Triggers a re-sort "
            "in both cases."
        ),
    )
    def patch_category(
        pid: UUID, index: int, body: CategoryPatch,
        s: Session = Depends(_get_session),
    ):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        cats = list(p.categories)
        if index < 0 or index >= len(cats):
            raise HTTPException(422, "index out of range")
        renamed = body.name is not None and body.name != cats[index]
        if renamed:
            cats[index] = body.name
        if body.move_to is not None:
            target = body.move_to
            if target < 0 or target >= len(cats):
                raise HTTPException(422, "move_to out of range")
            item = cats.pop(index)
            cats.insert(target, item)
        p.categories = cats
        p.updated_at = datetime.now(timezone.utc)
        if renamed:
            _clear_cache(s, pid)
        s.add(p); s.commit(); s.refresh(p)
        on_sort_requested(pid)
        return p.categories

    class CacheEntry(_BM):
        """One row of the LLM-result cache for a project."""

        content_key: str = _Field(
            description="Normalised item content (lower-case, whitespace "
                        "collapsed) used as the cache key.",
        )
        category_name: str = _Field(
            description="Category assigned by the LLM.",
        )
        transformed_content: str | None = _Field(
            default=None,
            description=(
                "When `additional_instructions` is active, the rewritten "
                "content the service writes back to the provider. Null "
                "for terminal cache entries keyed on the already-"
                "transformed form (used to prevent double LLM calls on "
                "echo webhooks)."
            ),
        )

    @router.get(
        "/{pid}/cache",
        response_model=list[CacheEntry],
        summary="Inspect a project's LLM-result cache",
        description=(
            "Returns every cached entry for the project. Useful for "
            "debugging misclassifications."
        ),
    )
    def get_cache(pid: UUID, s: Session = Depends(_get_session)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        from app.models import CategoryCache
        rows = s.exec(
            select(CategoryCache).where(CategoryCache.project_id == pid)
        ).all()
        return [
            CacheEntry(
                content_key=r.content_key,
                category_name=r.category_name,
                transformed_content=r.transformed_content,
            )
            for r in rows
        ]

    @router.delete(
        "/{pid}/cache",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Clear a project's LLM cache",
        description=(
            "Drops every cached entry for the project so the next sort "
            "re-queries the LLM for every item."
        ),
    )
    def clear_cache(pid: UUID, s: Session = Depends(_get_session)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        _clear_cache(s, pid)
        s.commit()

    @router.post(
        "/{pid}/sort",
        status_code=status.HTTP_202_ACCEPTED,
        summary="Queue an immediate sort",
        description=(
            "Bypasses the debouncer and schedules a sort cycle for the "
            "project right away. Response is returned before the sort "
            "runs; use `/healthz` + container logs to observe progress."
        ),
    )
    def trigger_sort(pid: UUID, s: Session = Depends(_get_session)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        on_sort_requested(pid)
        return {"status": "queued"}

    return router
