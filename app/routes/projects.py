from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
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
        s.add(p); s.commit(); s.refresh(p)
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

    return router
