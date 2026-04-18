from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import SortingProject
from app.routes.deps import require_api_key


SortTrigger = Callable[[UUID], None]


class ProjectCreate(BaseModel):
    name: str
    provider: str
    external_project_id: str
    categories: list[str] = []
    description: str | None = None
    debounce_seconds: int = 5


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    debounce_seconds: int | None = None


class ProjectOut(BaseModel):
    id: UUID
    name: str
    provider: str
    external_project_id: str
    categories: list[str]
    description: str | None
    enabled: bool
    debounce_seconds: int


def _out(p: SortingProject) -> ProjectOut:
    return ProjectOut(
        id=p.id, name=p.name, provider=p.provider,
        external_project_id=p.external_project_id,
        categories=p.categories, description=p.description,
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

    @router.post("", response_model=ProjectOut,
                 status_code=status.HTTP_201_CREATED)
    def create(body: ProjectCreate, s: Session = Depends(_get_session)):
        p = SortingProject(
            name=body.name, provider=body.provider,
            external_project_id=body.external_project_id,
            categories=body.categories, description=body.description,
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

    @router.get("", response_model=list[ProjectOut])
    def list_(s: Session = Depends(_get_session)):
        return [_out(p) for p in s.exec(select(SortingProject)).all()]

    @router.get("/{pid}", response_model=ProjectOut)
    def get(pid: UUID, s: Session = Depends(_get_session)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        return _out(p)

    @router.put("/{pid}", response_model=ProjectOut)
    def update(pid: UUID, body: ProjectUpdate,
               s: Session = Depends(_get_session)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(p, k, v)
        p.updated_at = datetime.now(timezone.utc)
        s.add(p); s.commit(); s.refresh(p)
        return _out(p)

    @router.delete("/{pid}", status_code=status.HTTP_204_NO_CONTENT)
    def delete(pid: UUID, s: Session = Depends(_get_session)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        s.delete(p); s.commit()

    from pydantic import BaseModel as _BM

    class CategoryAdd(_BM):
        name: str
        at_index: int | None = None

    class CategoryPatch(_BM):
        name: str | None = None
        move_to: int | None = None

    class CategoriesReplace(_BM):
        categories: list[str]

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

    @router.get("/{pid}/categories", response_model=list[str])
    def list_categories(pid: UUID, s: Session = Depends(_get_session)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        return p.categories

    @router.put("/{pid}/categories", response_model=list[str])
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

    @router.post("/{pid}/categories", response_model=list[str])
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

    @router.delete("/{pid}/categories/{index}", response_model=list[str])
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

    @router.patch("/{pid}/categories/{index}", response_model=list[str])
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

    @router.get("/{pid}/cache")
    def get_cache(pid: UUID, s: Session = Depends(_get_session)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        from app.models import CategoryCache
        rows = s.exec(
            select(CategoryCache).where(CategoryCache.project_id == pid)
        ).all()
        return [{"content_key": r.content_key,
                 "category_name": r.category_name} for r in rows]

    @router.delete("/{pid}/cache", status_code=status.HTTP_204_NO_CONTENT)
    def clear_cache(pid: UUID, s: Session = Depends(_get_session)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        _clear_cache(s, pid)
        s.commit()

    @router.post("/{pid}/sort", status_code=status.HTTP_202_ACCEPTED)
    def trigger_sort(pid: UUID, s: Session = Depends(_get_session)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        on_sort_requested(pid)
        return {"status": "queued"}

    return router
