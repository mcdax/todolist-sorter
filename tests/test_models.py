from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import CategoryCache, SortingProject


def _engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


def test_sorting_project_roundtrip():
    engine = _engine()
    with Session(engine) as s:
        p = SortingProject(
            name="Lidl",
            provider="todoist",
            external_project_id="123",
            categories=["🥬 Vegetables", "🍎 Fruit"],
            description="Supermarket route",
        )
        s.add(p); s.commit(); s.refresh(p)
        loaded = s.exec(select(SortingProject)).one()
        assert loaded.name == "Lidl"
        assert loaded.categories == ["🥬 Vegetables", "🍎 Fruit"]
        assert loaded.enabled is True
        assert loaded.debounce_seconds == 5


def test_category_cache_composite_key():
    engine = _engine()
    with Session(engine) as s:
        pid = uuid4()
        s.add(SortingProject(id=pid, name="L", provider="todoist",
                             external_project_id="1", categories=["A"]))
        s.commit()
        s.add(CategoryCache(project_id=pid, content_key="apples",
                            category_name="A"))
        s.commit()
        row = s.exec(select(CategoryCache)).one()
        assert row.category_name == "A"


def test_unique_provider_external():
    engine = _engine()
    with Session(engine) as s:
        s.add(SortingProject(name="A", provider="todoist",
                             external_project_id="1", categories=[]))
        s.commit()
        s.add(SortingProject(name="B", provider="todoist",
                             external_project_id="1", categories=[]))
        with pytest.raises(IntegrityError):
            s.commit()
