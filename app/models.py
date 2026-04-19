from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SortingProject(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("provider", "external_project_id",
                         name="uq_provider_external"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str
    provider: str
    external_project_id: str = Field(index=True)
    provider_config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    categories: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    description: str | None = None
    additional_instructions: str | None = Field(default=None)
    debounce_seconds: int = 5
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class CategoryCache(SQLModel, table=True):
    project_id: UUID = Field(
        foreign_key="sortingproject.id",
        primary_key=True,
        ondelete="CASCADE",
    )
    content_key: str = Field(primary_key=True)
    category_name: str
    transformed_content: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def __init__(self, **kwargs: object) -> None:
        if "project_id" in kwargs and isinstance(kwargs["project_id"], str):
            kwargs["project_id"] = UUID(kwargs["project_id"])
        super().__init__(**kwargs)
